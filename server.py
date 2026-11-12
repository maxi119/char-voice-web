# -*- coding: utf-8 -*-
"""
TTS 聲音工坊 — ComfyUI (Qwen3-TTS) 簡化操作網站
只用 Python 標準庫 + ffmpeg，不需另外安裝套件。

啟動：python server.py      然後開 http://localhost:8190
"""
import base64
import io
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import zipfile
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # 繁轉簡：Qwen3-TTS 對繁體字會偏向港式發音、念錯字，送進模型前先轉成簡體
    from opencc import OpenCC
    _T2S = OpenCC("t2s").convert
except Exception:
    _T2S = None
    print("[提示] 未安裝 opencc-python-reimplemented，繁體字不會轉簡體（可能出現港式發音）")

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path):
    """讀專案根目錄的 .env（KEY=VALUE）；已存在的系統環境變數優先，空值略過。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v


load_dotenv(os.path.join(ROOT, ".env"))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")
AUDIO = os.path.join(DATA, "audio")
TMP = os.path.join(DATA, "tmp")
LIB_FILE = os.path.join(DATA, "library.json")
CFG_FILE = os.path.join(ROOT, "config.json")
SR = 24000  # Qwen3-TTS 輸出取樣率

for d in (DATA, AUDIO, TMP):
    os.makedirs(d, exist_ok=True)

DEFAULT_CFG = {
    "comfy_url": "http://192.168.50.55:8188",
    "host": "0.0.0.0",
    "port": 8190,
    "clone_model_size": "1.7B",
    "temperature": 0.9,
    "segment_gap_ms": 60,
    "normalize": True,
    "default_language": "Chinese",
    "simplify_for_tts": True,  # 送進模型前把繁體轉簡體（只影響發音，不改你看到的文字）
    # 用來跑 Whisper 字詞對齊（重點字定位）的 Python；需裝有 openai-whisper。找不到時改用字數比例估計
    # 優先讀環境變數 ALIGNER_PYTHON（例如 ComfyUI 的 python_embeded\python.exe）
    "aligner_python": "",
    # BreezyVoice（台灣腔）服務位址，由 wsl/run_breezy.sh 在 WSL 啟動
    "breezy_url": "http://localhost:9880",
    # 自動加在聲音描述後面的口音指示（留空則不加）
    "accent_instruct": "使用台灣口音的標準國語發音，咬字清楚自然",
    # 重點字效果預設：gain(dB)、pitch(半音)、tempo(倍率)
    "presets": {
        "重": {"gain": 5, "pitch": 1, "tempo": 0.9, "label": "加重"},
        "高": {"gain": 0, "pitch": 3, "tempo": 1.0, "label": "提高音調"},
        "低": {"gain": 0, "pitch": -3, "tempo": 1.0, "label": "降低音調"},
        "慢": {"gain": 0, "pitch": 0, "tempo": 0.8, "label": "放慢"},
        "快": {"gain": 0, "pitch": 0, "tempo": 1.25, "label": "加快"},
        "輕": {"gain": -6, "pitch": 0, "tempo": 1.0, "label": "輕聲"},
    },
}

NODES = {
    "design": "AILab_Qwen3TTSVoiceDesign_Advanced",
    "clone": "AILab_Qwen3TTSVoiceClone_Advanced",
    "speaker": "AILab_Qwen3TTSCustomVoice_Advanced",
}

_lock = threading.RLock()


# ---------------------------------------------------------------- 設定 / 資料庫
def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                user = json.load(f)
            presets = user.pop("presets", None)
            cfg.update(user)
            if isinstance(presets, dict):
                cfg["presets"].update(presets)
        except Exception:
            traceback.print_exc()
    return cfg


def save_cfg(cfg):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CFG = load_cfg()
if not os.path.exists(CFG_FILE):
    save_cfg(CFG)


def load_lib():
    if os.path.exists(LIB_FILE):
        with open(LIB_FILE, encoding="utf-8") as f:
            lib = json.load(f)
    else:
        lib = {}
    lib.setdefault("characters", [])
    lib.setdefault("clips", [])
    return lib


def save_lib(lib):
    tmp = LIB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIB_FILE)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id():
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- ffmpeg / 音訊工具
def ffmpeg(args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 失敗: " + p.stderr.strip()[-500:])


def to_wav(src, dst):
    ffmpeg(["-i", src, "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst])


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return 0


def fx_chain(gain=0.0, pitch=0.0, tempo=1.0):
    f = []
    if abs(tempo - 1) > 1e-3 or abs(pitch) > 1e-3:
        ratio = 2 ** (pitch / 12.0)
        f.append("rubberband=tempo=%.4f:pitch=%.4f:formant=preserved:pitchq=quality" % (tempo, ratio))
    if abs(gain) > 1e-3:
        f.append("volume=%.2fdB" % gain)
    return f


def apply_fx(src, dst, gain=0.0, pitch=0.0, tempo=1.0, trim=False, extra=None):
    f = []
    if trim:
        t = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02"
        f += [t, "areverse", t, "areverse"]
    f += fx_chain(gain, pitch, tempo)
    if extra:
        f += extra
    if not f:
        shutil.copyfile(src, dst)
        return
    ffmpeg(["-i", src, "-af", ",".join(f), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst])


def read_pcm(path):
    with wave.open(path, "rb") as w:
        a = array("h")
        a.frombytes(w.readframes(w.getnframes()))
    return a


def fade(a, ms=12):
    n = min(len(a) // 2, int(SR * ms / 1000))
    for i in range(n):
        k = i / float(n)
        a[i] = int(a[i] * k)
        a[-1 - i] = int(a[-1 - i] * k)
    return a


def write_pcm(path, a):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(a.tobytes())


# ---------------------------------------------------------------- 標記語法解析
# [重]文字[/]   [高 慢]文字[/]   [v+4 p+2 s0.9]文字[/]   [停0.5]
TAG_RE = re.compile(r"\[停\s*([\d.]+)\s*秒?\]|\[(?![:：])([^\[\]/]+)\](.*?)\[/\]", re.S)
BOPO_RE = re.compile(r"\[[:：][^\[\]]*\]")  # BreezyVoice 注音標註，例如 行[:ㄒㄧㄥ2]


def strip_bopo(s, keep_len=False):
    return BOPO_RE.sub((lambda m: " " * len(m.group(0))) if keep_len else "", s or "")
PUNCT_RE = re.compile(r"^[\s，,。．.！!？?；;：:、…—\-~～」』）)\"'”’]+")
SENT_RE = re.compile(r"(?<=[。！？!?；;\n])")


def parse_tags(spec):
    g, p, t = 0.0, 0.0, 1.0
    for tok in spec.replace(",", " ").replace("，", " ").split():
        if tok in CFG["presets"]:
            pr = CFG["presets"][tok]
            g += float(pr.get("gain", 0))
            p += float(pr.get("pitch", 0))
            t *= float(pr.get("tempo", 1))
            continue
        m = re.fullmatch(r"([vVpPsS])([+-]?[\d.]+)", tok)
        if m:
            k, v = m.group(1).lower(), float(m.group(2))
            if k == "v":
                g += v
            elif k == "p":
                p += v
            else:
                t *= v if v > 0 else 1
    return {"gain": g, "pitch": p, "tempo": max(0.3, min(3.0, t))}


def split_long(text, limit=220):
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for s in SENT_RE.split(text):
        if len(buf) + len(s) > limit and buf:
            parts.append(buf)
            buf = ""
        buf += s
    if buf:
        parts.append(buf)
    return parts


def parse_markup(text):
    items, pos = [], 0
    plain = lambda s: [{"type": "text", "text": x, "fx": None} for x in split_long(s) if x.strip()]
    for m in TAG_RE.finditer(text):
        items += plain(text[pos:m.start()])
        if m.group(1):
            items.append({"type": "pause", "sec": min(10.0, float(m.group(1)))})
        elif m.group(3).strip():
            items.append({"type": "text", "text": m.group(3), "fx": parse_tags(m.group(2))})
        pos = m.end()
    items += plain(text[pos:])
    # 段落開頭的標點併入前一段（例如「[重]不行[/]！」→「不行！」），避免單獨生成標點
    out = []
    for it in items:
        if it["type"] == "text" and out and out[-1]["type"] == "text":
            m = PUNCT_RE.match(it["text"])
            if m and m.group(0):
                out[-1]["text"] += m.group(0).strip()
                it["text"] = it["text"][m.end():]
        if it["type"] == "pause" or re.search(r"\w", it["text"]):
            out.append(it)
    return out


def strip_markup(text):
    return TAG_RE.sub(lambda m: "" if m.group(1) else m.group(3), text)


# ---------------------------------------------------------------- ComfyUI 客戶端
class Comfy:
    def __init__(self):
        self.schemas = {}

    @property
    def base(self):
        return CFG["comfy_url"].rstrip("/")

    def req(self, path, data=None, headers=None, timeout=30, raw=False):
        h = {"User-Agent": "tts-web"}
        if headers:
            h.update(headers)
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError("ComfyUI HTTP %s: %s" % (e.code, body[:1500]))
        return body if raw else json.loads(body or b"{}")

    def schema(self, cls, refresh=False):
        if refresh or cls not in self.schemas:
            info = self.req("/object_info/" + urllib.parse.quote(cls))
            if cls not in info:
                raise RuntimeError("ComfyUI 沒有節點 %s，請先安裝 ComfyUI-QwenTTS" % cls)
            self.schemas[cls] = info[cls]
        return self.schemas[cls]

    @staticmethod
    def combo_options(spec):
        if isinstance(spec[0], list):
            return spec[0]
        if spec[0] == "COMBO" and len(spec) > 1:
            return spec[1].get("options", [])
        return None

    def build(self, cls, given):
        """用節點 schema 補齊預設值，只送 schema 裡存在的參數。"""
        sc = self.schema(cls)["input"]
        out = {}
        for section in ("required", "optional"):
            for name, spec in (sc.get(section) or {}).items():
                if name in given:
                    val = given[name]
                    opts = self.combo_options(spec)
                    if opts and not isinstance(val, list) and val not in opts:
                        raise RuntimeError("%s.%s 不接受值 %r（可用：%s）" % (cls, name, val, opts))
                    out[name] = val
                    continue
                opts = self.combo_options(spec)
                meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if opts:
                    out[name] = meta.get("default", opts[0] if opts else None)
                elif "default" in meta:
                    out[name] = meta["default"]
                elif section == "required":
                    raise RuntimeError("%s 缺少必要參數 %s" % (cls, name))
        return out

    def options(self, cls, name):
        try:
            sc = self.schema(cls)["input"]
        except Exception:
            return []
        for section in ("required", "optional"):
            spec = (sc.get(section) or {}).get(name)
            if spec:
                return self.combo_options(spec) or []
        return []

    def upload(self, path, name):
        boundary = "----ttsweb" + uuid.uuid4().hex
        with open(path, "rb") as f:
            content = f.read()
        parts = []
        for k, v in (("subfolder", "ttsweb"), ("type", "input"), ("overwrite", "true")):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="image"; filename="%s"\r\n'
                      'Content-Type: audio/wav\r\n\r\n' % (boundary, name)).encode() + content + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode())
        res = self.req("/upload/image", data=b"".join(parts),
                       headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, timeout=60)
        sub = res.get("subfolder") or ""
        return (sub + "/" if sub else "") + res["name"]

    def run(self, workflow, out_node, cancel=None, timeout=900):
        res = self.req("/prompt", {"prompt": workflow, "client_id": "tts-web"})
        if res.get("node_errors"):
            raise RuntimeError("工作流程錯誤: " + json.dumps(res["node_errors"], ensure_ascii=False)[:1500])
        pid = res["prompt_id"]
        t0 = time.time()
        while True:
            if cancel and cancel.is_set():
                try:
                    self.req("/interrupt", {})
                except Exception:
                    pass
                raise RuntimeError("已取消")
            if time.time() - t0 > timeout:
                raise RuntimeError("ComfyUI 執行逾時")
            h = self.req("/history/" + pid)
            if pid in h:
                entry = h[pid]
                st = entry.get("status", {})
                if st.get("status_str") == "error":
                    msg = ""
                    for m in st.get("messages", []):
                        if m[0] == "execution_error":
                            msg = m[1].get("exception_message", "")
                    raise RuntimeError("ComfyUI 執行錯誤: " + msg.strip()[:1500])
                outs = entry.get("outputs", {}).get(out_node, {})
                files = outs.get("audio") or []
                if files:
                    return files[0]
                if st.get("completed"):
                    raise RuntimeError("ComfyUI 沒有輸出音訊")
            time.sleep(0.6)

    def fetch(self, fileinfo, dst_wav):
        q = urllib.parse.urlencode({"filename": fileinfo["filename"], "subfolder": fileinfo.get("subfolder", ""),
                                    "type": fileinfo.get("type", "output")})
        data = self.req("/view?" + q, raw=True, timeout=120)
        ext = os.path.splitext(fileinfo["filename"])[1] or ".flac"
        raw_path = dst_wav + ext
        with open(raw_path, "wb") as f:
            f.write(data)
        to_wav(raw_path, dst_wav)
        os.remove(raw_path)


COMFY = Comfy()


def text_units(text):
    """估算朗讀長度單位：中日韓字 1、其他語言約 3 字母 1。"""
    cjk = len(re.findall(r"[぀-ヿ㐀-鿿가-힯]", text))
    other = len(re.findall(r"[A-Za-z0-9]", text))
    return max(1, cjk + other / 3.0)


def max_seconds(text):
    return text_units(text) * 0.5 + 3.0  # 慢速朗讀約 0.4 秒/字，再留餘裕


def with_accent(instruct):
    acc = (CFG.get("accent_instruct") or "").strip()
    instruct = (instruct or "").strip()
    if not acc or acc in instruct:
        return instruct
    return (instruct.rstrip("，,。. ") + "。" + acc) if instruct else acc


def tts_once(mode, text, opts, dst, unload, cancel=None, retries=2):
    """執行一次 TTS，存成 dst (wav)；輸出異常過長（模型失控）時換種子重試。"""
    limit = max_seconds(strip_bopo(text))
    if mode == "breezy":
        breezy_call(text, opts, dst)
        return None
    seed = int(opts.get("seed") if opts.get("seed") not in (None, "", -1) else random.randint(0, 2**31 - 1))
    for attempt in range(retries + 1):
        _tts_call(mode, text, dict(opts, seed=seed), dst, unload, cancel, limit)
        dur = wav_duration(dst)
        if dur <= limit:
            break
        print("[警告] 片段過長 %.1fs > %.1fs（%s），重試 %d" % (dur, limit, text[:20], attempt + 1))
        if attempt == retries:  # 最後手段：截斷
            t = dst + ".cut.wav"
            ffmpeg(["-i", dst, "-t", "%.2f" % limit, "-af", "afade=t=out:st=%.2f:d=0.3" % max(0, limit - 0.3), t])
            os.replace(t, dst)
        else:
            seed = random.randint(0, 2**31 - 1)
    return seed


def to_tts_text(s):
    if not s or not CFG.get("simplify_for_tts", True) or not _T2S:
        return s
    return _T2S(s)


def breezy_call(text, opts, dst):
    """BreezyVoice：參考音 + 逐字稿 → 台灣腔語音。輸入保持繁體（模型就是用繁體訓練的）。"""
    ref_text = strip_markup(opts.get("ref_text") or "").strip()
    if not ref_text:
        raise RuntimeError("BreezyVoice 需要參考音的逐字稿，請在角色「編輯」中填入")
    with open(opts["ref_path"], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({"prompt_wav_b64": b64, "prompt_text": ref_text, "text": text.strip()}).encode("utf-8")
    req = urllib.request.Request(CFG["breezy_url"].rstrip("/") + "/tts", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("BreezyVoice 錯誤：" + e.read().decode("utf-8", "replace")[:500])
    except urllib.error.URLError:
        raise RuntimeError("BreezyVoice 服務沒有啟動（請執行 start.bat 或 wsl/run_breezy.sh）")
    raw = dst + ".breezy.wav"
    with open(raw, "wb") as f:
        f.write(data)
    to_wav(raw, dst)
    os.remove(raw)


def breezy_online():
    try:
        with urllib.request.urlopen(CFG["breezy_url"].rstrip("/") + "/health", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _tts_call(mode, text, opts, dst, unload, cancel, limit):
    cls = NODES[mode]
    ALIGNER.release_gpu()
    text = to_tts_text(strip_bopo(text))
    seed = int(opts["seed"])
    tokens = int(min(4096, max(256, (limit * 12 + 255) // 256 * 256)))  # 12 Hz codec
    lang = opts.get("language")
    if not lang or lang == "Auto":
        lang = CFG.get("default_language") or "Auto"
    common = {"language": lang, "seed": seed,
              "unload_models": bool(unload), "do_sample": True, "max_new_tokens": tokens,
              "temperature": float(opts.get("temperature", CFG["temperature"])),
              "top_k": 50, "top_p": 1.0, "repetition_penalty": 1.05}
    if mode == "design":
        given = dict(common, text=text, instruct=to_tts_text(with_accent(opts["instruct"])), model_size="1.7B")
        wf = {"1": {"class_type": cls, "inputs": None}}
    elif mode == "speaker":
        given = dict(common, text=text, speaker=opts["speaker"], instruct=to_tts_text(with_accent(opts.get("instruct", ""))),
                     model_size=opts.get("model_size") or "1.7B")
        wf = {"1": {"class_type": cls, "inputs": None}}
    else:
        ref = COMFY.upload(opts["ref_path"], "ref_%s.wav" % opts["ref_key"])
        ref_text = to_tts_text((opts.get("ref_text") or "").strip())
        given = dict(common, target_text=text, model_size=opts.get("model_size") or CFG["clone_model_size"],
                     reference_audio=["0", 0], reference_text=ref_text, x_vector_only=not ref_text)
        wf = {"0": {"class_type": "LoadAudio", "inputs": {"audio": ref}}, "1": {"class_type": cls, "inputs": None}}
    if "language" in given and given["language"] not in COMFY.options(cls, "language"):
        given.pop("language")
    wf["1"]["inputs"] = COMFY.build(cls, given)
    wf["9"] = {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "ttsweb/" + new_id()}}
    info = COMFY.run(wf, "9", cancel)
    COMFY.fetch(info, dst)


# ---------------------------------------------------------------- 工作佇列
JOBS = {}
GPU = threading.Lock()


def start_job(kind, fn, payload):
    jid = new_id()
    job = {"id": jid, "kind": kind, "state": "queued", "progress": 0, "total": 1, "message": "排隊中…",
           "result": None, "error": None, "created": time.time(), "cancel": threading.Event()}
    JOBS[jid] = job

    def runner():
        with GPU:
            if job["cancel"].is_set():
                job["state"], job["error"] = "error", "已取消"
                return
            job["state"], job["message"] = "running", "生成中…"
            try:
                job["result"] = fn(job, payload)
                job["state"], job["message"] = "done", "完成"
            except Exception as e:
                traceback.print_exc()
                job["state"], job["error"] = "error", str(e)
    threading.Thread(target=runner, daemon=True).start()
    # 清掉舊工作
    for k in [k for k, j in JOBS.items() if time.time() - j["created"] > 3600]:
        JOBS.pop(k, None)
    return jid


def job_design(job, p):
    n = max(1, min(6, int(p.get("count", 1))))
    job["total"] = n + (1 if p.get("taiwan") else 0)
    base_seed = p.get("seed")
    out = []
    for i in range(n):
        job["progress"], job["message"] = i, "生成候選 %d/%d…" % (i + 1, n)
        fid = "cand_" + new_id() + ".wav"
        opts = dict(p)
        opts["seed"] = (int(base_seed) + i) if base_seed not in (None, "", -1) else None
        seed = tts_once("design", p["text"], opts, os.path.join(TMP, fid), unload=(i == n - 1), cancel=job["cancel"])
        out.append({"file": "tmp/" + fid, "seed": seed, "duration": wav_duration(os.path.join(TMP, fid))})
    if p.get("taiwan"):
        for i, c in enumerate(out):
            job["progress"], job["message"] = n, "BreezyVoice 台灣腔重念 %d/%d…" % (i + 1, n)
            tw = "candtw_" + new_id() + ".wav"
            breezy_call(p["text"], {"ref_path": os.path.join(DATA, c["file"]), "ref_text": p["text"]}, os.path.join(TMP, tw))
            c["tw_file"], c["tw_duration"] = "tmp/" + tw, wav_duration(os.path.join(TMP, tw))
    job["progress"] = n
    return {"candidates": out, "instruct": p["instruct"], "text": p["text"], "language": p.get("language")}


def build_chunks(items, limit=220):
    """把解析結果組成「整句生成」的區塊：停頓才切開；重點字只記錄位置，不拆開生成。"""
    chunks, cur = [], None
    for it in items:
        if it["type"] == "pause":
            cur = None
            chunks.append(it)
            continue
        if cur is None or len(cur["text"]) + len(it["text"]) > limit:
            cur = {"type": "text", "text": "", "spans": []}
            chunks.append(cur)
        start = len(cur["text"])
        cur["text"] += it["text"]
        if it["fx"]:
            cur["spans"].append((start, len(cur["text"]), it["fx"]))
    for c in chunks:  # 去掉頭尾空白並修正位置
        if c["type"] == "text":
            lead = len(c["text"]) - len(c["text"].lstrip())
            c["text"] = c["text"].strip()
            c["spans"] = [(max(0, a - lead), min(len(c["text"]), b - lead), fx) for a, b, fx in c["spans"]]
    return [c for c in chunks if c["type"] == "pause" or c["text"]]


LANG_WHISPER = {"Chinese": "zh", "English": "en", "Japanese": "ja", "Korean": "ko", "French": "fr", "German": "de",
                "Spanish": "es", "Portuguese": "pt", "Russian": "ru", "Italian": "it"}


class Aligner:
    """以 ComfyUI 的 Python（含 openai-whisper）跑常駐的對齊程序，閒置 5 分鐘自動關閉釋放顯存。"""

    def __init__(self):
        self.proc = None
        self.device = None
        self.lock = threading.Lock()
        self.last = 0
        threading.Thread(target=self._reaper, daemon=True).start()

    def python(self):
        p = os.environ.get("ALIGNER_PYTHON") or CFG.get("aligner_python") or ""
        return p if p and os.path.exists(p) else None

    def _reaper(self):
        while True:
            time.sleep(30)
            with self.lock:
                if self.proc and time.time() - self.last > 300:
                    self._stop()

    def _stop(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        self.proc = None
        self.device = None

    def release_gpu(self):
        """在載入大型 TTS 模型前釋放對齊程序佔用的顯存。"""
        with self.lock:
            if self.proc and self.device == "cuda":
                self._stop()

    def align(self, wav, text, language):
        py = self.python()
        if not py:
            return None
        with self.lock:
            self.last = time.time()
            if self.proc is None or self.proc.poll() is not None:
                env = dict(os.environ, PYTHONIOENCODING="utf-8")
                self.proc = subprocess.Popen([py, "-s", os.path.join(ROOT, "align_worker.py")], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                             text=True, encoding="utf-8", bufsize=1)
            try:
                self.proc.stdin.write(json.dumps({"audio": wav, "text": text, "language": LANG_WHISPER.get(language)},
                                                 ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("對齊程序結束")
                    if line.startswith("@@"):
                        res = json.loads(line[2:])
                        self.device = res.get("device")
                        break
            except Exception as e:
                print("[對齊] 失敗：", e)
                self._stop()
                return None
            self.last = time.time()
        if res.get("error") or not res.get("times"):
            print("[對齊] 無結果：", res.get("error"))
            return None
        return res


ALIGNER = Aligner()


def proportional_times(text, total):
    """沒有對齊程序時的備案：依字數比例估計每個字的時間。"""
    w = [0.0 if c.isspace() else (0.6 if PUNCT_RE.match(c) else 1.0) for c in text]
    s = sum(w) or 1
    t, out = 0.0, []
    for x in w:
        d = total * x / s
        out.append([t, t + d])
        t += d
    return out


def xjoin(a, b, ms=8):
    n = min(int(SR * ms / 1000), len(a), len(b))
    for i in range(n):
        k = (i + 1) / float(n + 1)
        j = len(a) - n + i
        a[j] = int(a[j] * (1 - k) + b[i] * k)
    a.extend(b[n:])
    return a


def emphasize(pcm, wav_path, chunk, language, work):
    """在整句音檔中找出重點字的時間範圍，只對那一段套用音量／音高／速度效果。"""
    text = strip_bopo(chunk["text"], keep_len=True)
    simp = to_tts_text(text)
    res = ALIGNER.align(wav_path, simp if len(simp) == len(text) else text, language)
    if res:
        times, how = res["times"], "whisper(%s) %d/%d" % (res.get("device"), res["matched"], res["total"])
    else:
        times, how = proportional_times(text, len(pcm) / float(SR)), "比例估計"
    ranges = []
    for a, b, fx in chunk["spans"]:
        ts = [times[i] for i in range(a, b) if i < len(times) and times[i]]
        if ts:
            ranges.append((min(t[0] for t in ts), max(t[1] for t in ts), fx))
    ranges.sort(key=lambda r: r[0])
    out, pos = array("h"), 0
    for k, (t0, t1, fx) in enumerate(ranges):
        a = max(pos, int((t0 - 0.05) * SR))
        b = min(len(pcm), int((t1 + 0.06) * SR))
        if b - a < int(0.05 * SR):
            continue
        xjoin(out, pcm[pos:a])
        src, dst = os.path.join(work, "span_in.wav"), os.path.join(work, "span_out.wav")
        write_pcm(src, pcm[a:b])
        apply_fx(src, dst, fx.get("gain", 0), fx.get("pitch", 0), fx.get("tempo", 1))
        xjoin(out, read_pcm(dst))
        pos = b
    xjoin(out, pcm[pos:])
    print("[重點字] %s：%s" % (how, [(round(r[0], 2), round(r[1], 2)) for r in ranges]))
    return out, how


def job_speak(job, p):
    lib = load_lib()
    opts = {"language": p.get("language"), "temperature": p.get("temperature", CFG["temperature"]),
            "seed": p.get("seed")}
    char = None
    if p.get("character_id"):
        char = next((c for c in lib["characters"] if c["id"] == p["character_id"]), None)
        if not char:
            raise RuntimeError("找不到角色")
        mode = "breezy" if p.get("engine") == "breezy" else "clone"
        opts.update(ref_path=os.path.join(AUDIO, char["ref_audio"]), ref_text=char.get("ref_text", ""),
                    ref_key=char["id"], model_size=p.get("model_size"))
        if not opts["language"]:
            opts["language"] = char.get("language")
    else:
        mode = "speaker"
        opts.update(speaker=p["speaker"], instruct=p.get("instruct", ""), model_size=p.get("model_size"))
    if opts.get("seed") in (None, "", -1):
        opts["seed"] = random.randint(0, 2**31 - 1)  # 各片段同一 seed，音色較一致

    chunks = build_chunks(parse_markup(p["text"]))
    texts = [c for c in chunks if c["type"] == "text"]
    if not texts:
        raise RuntimeError("沒有可朗讀的文字")
    job["total"] = len(texts) + 1
    multi = len(chunks) > 1
    work = os.path.join(TMP, "job_" + job["id"])
    os.makedirs(work, exist_ok=True)
    pcm = array("h")
    gap = array("h", [0]) * int(SR * float(p.get("gap_ms", CFG["segment_gap_ms"])) / 1000)
    done = 0
    align_info = []
    try:
        for ch in chunks:
            if ch["type"] == "pause":
                pcm += array("h", [0]) * int(SR * ch["sec"])
                continue
            job["progress"], job["message"] = done, "生成 %d/%d：%s" % (done + 1, len(texts), ch["text"][:16])
            raw = os.path.join(work, "%03d_raw.wav" % done)
            proc = os.path.join(work, "%03d.wav" % done)
            tts_once(mode, ch["text"], opts, raw, unload=(done == len(texts) - 1), cancel=job["cancel"])
            if multi:
                apply_fx(raw, proc, trim=True)
            else:
                shutil.copyfile(raw, proc)
            seg = read_pcm(proc)
            if ch["spans"]:
                job["message"] = "定位重點字 %d/%d…" % (done + 1, len(texts))
                seg, how = emphasize(seg, proc, ch, opts.get("language"), work)
                align_info.append(how)
            if multi:
                seg = fade(seg)
                if len(pcm):
                    pcm += gap
            pcm += seg
            done += 1
        job["progress"], job["message"] = done, "後製（語速／音高／音量）…"
        joined = os.path.join(work, "joined.wav")
        write_pcm(joined, pcm)
        fid = new_id() + ".wav"
        extra = ["loudnorm=I=-16:TP=-1.5:LRA=11"] if p.get("normalize", CFG["normalize"]) else []
        apply_fx(joined, os.path.join(AUDIO, fid), 0, float(p.get("pitch", 0)), float(p.get("speed", 1)), extra=extra)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    clip = {"id": new_id(), "character_id": char["id"] if char else None,
            "speaker": None if char else p.get("speaker"), "text": p["text"], "plain": strip_bopo(strip_markup(p["text"])),
            "file": fid, "duration": wav_duration(os.path.join(AUDIO, fid)),
            "params": {k: p.get(k) for k in ("speed", "pitch", "language", "instruct", "model_size", "gap_ms", "engine")},
            "seed": opts["seed"], "favorite": False, "note": p.get("note", ""), "tags": p.get("tags", []),
            "align": align_info, "created": now()}
    with _lock:
        lib = load_lib()
        lib["clips"].insert(0, clip)
        save_lib(lib)
    return {"clip": clip}


# ---------------------------------------------------------------- 角色 / 片段操作
def b64_to_wav(b64, dst):
    if "," in b64[:100]:
        b64 = b64.split(",", 1)[1]
    raw = dst + ".upload"
    with open(raw, "wb") as f:
        f.write(base64.b64decode(b64))
    try:
        to_wav(raw, dst)
    finally:
        os.remove(raw)


def create_character(p):
    cid = new_id()
    fid = "ref_%s.wav" % cid
    dst = os.path.join(AUDIO, fid)
    if p.get("candidate"):
        src = os.path.normpath(os.path.join(DATA, p["candidate"]))
        if not src.startswith(TMP) or not os.path.exists(src):
            raise RuntimeError("候選音檔不存在（可能已過期）")
        shutil.copyfile(src, dst)
        source = "design"
    elif p.get("audio_b64"):
        b64_to_wav(p["audio_b64"], dst)
        if p.get("trim"):
            t = dst + ".t.wav"
            apply_fx(dst, t, trim=True)
            os.replace(t, dst)
        source = "upload"
    else:
        raise RuntimeError("需要候選音檔或上傳音檔")
    c = {"id": cid, "name": p.get("name") or "未命名角色", "description": p.get("description", ""),
         "instruct": p.get("instruct", ""), "language": p.get("language") or "Auto", "tags": p.get("tags", []),
         "notes": p.get("notes", ""), "ref_audio": fid, "ref_text": p.get("ref_text", ""), "seed": p.get("seed"),
         "source": source, "created": now(), "duration": wav_duration(dst)}
    with _lock:
        lib = load_lib()
        lib["characters"].insert(0, c)
        save_lib(lib)
    return c


def update_character(cid, p):
    with _lock:
        lib = load_lib()
        c = next((c for c in lib["characters"] if c["id"] == cid), None)
        if not c:
            raise RuntimeError("找不到角色")
        for k in ("name", "description", "instruct", "language", "tags", "notes", "ref_text"):
            if k in p:
                c[k] = p[k]
        if p.get("ref_from_clip"):
            clip = next((x for x in lib["clips"] if x["id"] == p["ref_from_clip"]), None)
            if not clip:
                raise RuntimeError("找不到片段")
            shutil.copyfile(os.path.join(AUDIO, clip["file"]), os.path.join(AUDIO, c["ref_audio"]))
            c["ref_text"] = clip.get("plain", "")
            c["duration"] = clip.get("duration")
        if p.get("audio_b64"):
            b64_to_wav(p["audio_b64"], os.path.join(AUDIO, c["ref_audio"]))
            c["duration"] = wav_duration(os.path.join(AUDIO, c["ref_audio"]))
        c["updated"] = now()
        save_lib(lib)
        return c


def rm_audio(name):
    for f in (name, name[:-4] + ".mp3"):
        try:
            os.remove(os.path.join(AUDIO, f))
        except OSError:
            pass


def delete_character(cid, with_clips=True):
    with _lock:
        lib = load_lib()
        c = next((c for c in lib["characters"] if c["id"] == cid), None)
        if not c:
            return
        lib["characters"].remove(c)
        rm_audio(c["ref_audio"])
        keep = []
        for clip in lib["clips"]:
            if clip.get("character_id") == cid and with_clips:
                rm_audio(clip["file"])
            else:
                keep.append(clip)
        lib["clips"] = keep
        save_lib(lib)


def update_clip(cid, p):
    with _lock:
        lib = load_lib()
        c = next((c for c in lib["clips"] if c["id"] == cid), None)
        if not c:
            raise RuntimeError("找不到片段")
        for k in ("favorite", "note", "tags", "title"):
            if k in p:
                c[k] = p[k]
        save_lib(lib)
        return c


def delete_clip(cid):
    with _lock:
        lib = load_lib()
        c = next((c for c in lib["clips"] if c["id"] == cid), None)
        if c:
            lib["clips"].remove(c)
            rm_audio(c["file"])
            save_lib(lib)


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s)[:40] or "clip"


def export_character(cid):
    lib = load_lib()
    c = next((c for c in lib["characters"] if c["id"] == cid), None)
    if not c:
        raise RuntimeError("找不到角色")
    clips = [x for x in lib["clips"] if x.get("character_id") == cid]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(AUDIO, c["ref_audio"]), "reference.wav")
        for i, x in enumerate(clips):
            p = os.path.join(AUDIO, x["file"])
            if os.path.exists(p):
                z.write(p, "clips/%03d_%s.wav" % (i + 1, safe_name(x.get("plain", ""))))
                x["_zip"] = "clips/%03d_%s.wav" % (i + 1, safe_name(x.get("plain", "")))
        z.writestr("character.json", json.dumps({"character": c, "clips": clips}, ensure_ascii=False, indent=2))
    return buf.getvalue(), safe_name(c["name"]) + ".zip"


def import_character(p):
    b64 = p["zip_b64"].split(",", 1)[-1]
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(b64)))
    meta = json.loads(z.read("character.json").decode("utf-8"))
    c = meta["character"]
    old = c["id"]
    c["id"] = new_id()
    c["ref_audio"] = "ref_%s.wav" % c["id"]
    c["name"] = c.get("name", "匯入角色")
    with open(os.path.join(AUDIO, c["ref_audio"]), "wb") as f:
        f.write(z.read("reference.wav"))
    clips = []
    for x in meta.get("clips", []):
        src = x.pop("_zip", None)
        if not src or src not in z.namelist():
            continue
        x["id"], x["file"], x["character_id"] = new_id(), new_id() + ".wav", c["id"]
        with open(os.path.join(AUDIO, x["file"]), "wb") as f:
            f.write(z.read(src))
        clips.append(x)
    with _lock:
        lib = load_lib()
        lib["characters"].insert(0, c)
        lib["clips"] = clips + lib["clips"]
        save_lib(lib)
    return {"character": c, "clips": len(clips), "old_id": old}


def status():
    st = {"comfy_url": CFG["comfy_url"], "online": False, "nodes": {}, "languages": [], "speakers": [],
          "model_sizes": [], "vram": None, "queue": None}
    try:
        s = COMFY.req("/system_stats", timeout=5)
        st["online"] = True
        dev = (s.get("devices") or [{}])[0]
        st["gpu"] = dev.get("name")
        st["vram"] = [dev.get("vram_free"), dev.get("vram_total")]
        q = COMFY.req("/queue", timeout=5)
        st["queue"] = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    except Exception as e:
        st["error"] = str(e)
        return st
    for k, cls in NODES.items():
        try:
            COMFY.schema(cls, refresh=True)
            st["nodes"][k] = True
        except Exception:
            st["nodes"][k] = False
    st["languages"] = COMFY.options(NODES["design"], "language") or COMFY.options(NODES["clone"], "language")
    st["speakers"] = COMFY.options(NODES["speaker"], "speaker")
    st["model_sizes"] = COMFY.options(NODES["clone"], "model_size")
    st["opencc"] = bool(_T2S)
    st["breezy"] = breezy_online()
    return st


def cleanup_tmp():
    cutoff = time.time() - 24 * 3600
    for f in os.listdir(TMP):
        p = os.path.join(TMP, f)
        try:
            if os.path.getmtime(p) < cutoff:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "tts-web/1.0"

    def log_message(self, fmt, *args):
        if "/api/jobs/" not in (args[0] if args else ""):
            print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, ctype, name=None, cache=True):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if name:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def route(self, method):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        try:
            if method == "GET" and not path.startswith("/api/") and not path.startswith("/audio/"):
                return self.static(path)
            if method == "GET" and path.startswith("/audio/"):
                return self.audio(path[7:], qs)
            parts = path.strip("/").split("/")[1:]  # 去掉 api
            key = (method, parts[0] if parts else "")
            arg = parts[1] if len(parts) > 1 else None

            if key == ("GET", "status"):
                return self.send_json(status())
            if key == ("GET", "config"):
                return self.send_json(CFG)
            if key == ("PUT", "config"):
                p = self.body()
                for k in ("comfy_url", "clone_model_size", "temperature", "segment_gap_ms", "normalize", "presets",
                          "accent_instruct", "default_language", "simplify_for_tts", "breezy_url"):
                    if k in p:
                        CFG[k] = p[k]
                COMFY.schemas.clear()
                save_cfg(CFG)
                return self.send_json(CFG)
            if key == ("GET", "library"):
                return self.send_json(load_lib())
            if key == ("POST", "design"):
                p = self.body()
                if not p.get("instruct", "").strip() or not p.get("text", "").strip():
                    return self.send_json({"error": "請填寫聲音描述與試聽文字"}, 400)
                return self.send_json({"job": start_job("design", job_design, p)})
            if key == ("POST", "speak"):
                p = self.body()
                if not p.get("text", "").strip():
                    return self.send_json({"error": "請輸入台詞"}, 400)
                if not p.get("character_id") and not p.get("speaker"):
                    return self.send_json({"error": "請選擇角色或預設音色"}, 400)
                return self.send_json({"job": start_job("speak", job_speak, p)})
            if key == ("POST", "preview"):
                return self.send_json({"items": parse_markup(self.body().get("text", ""))})
            if key == ("GET", "jobs") and arg in JOBS:
                j = {k: v for k, v in JOBS[arg].items() if k != "cancel"}
                return self.send_json(j)
            if key == ("DELETE", "jobs") and arg in JOBS:
                JOBS[arg]["cancel"].set()
                return self.send_json({"ok": True})
            if key == ("POST", "characters"):
                return self.send_json(create_character(self.body()))
            if key == ("PUT", "characters") and arg:
                return self.send_json(update_character(arg, self.body()))
            if key == ("DELETE", "characters") and arg:
                delete_character(arg, qs.get("keep_clips", ["0"])[0] != "1")
                return self.send_json({"ok": True})
            if key == ("GET", "export") and arg:
                data, name = export_character(arg)
                return self.send_bytes(data, "application/zip", name, cache=False)
            if key == ("POST", "import"):
                return self.send_json(import_character(self.body()))
            if key == ("PUT", "clips") and arg:
                return self.send_json(update_clip(arg, self.body()))
            if key == ("DELETE", "clips") and arg:
                delete_clip(arg)
                return self.send_json({"ok": True})
            if key == ("POST", "free"):
                COMFY.req("/free", {"unload_models": True, "free_memory": True})
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self.send_json({"error": str(e)}, 500)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        f = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not f.startswith(STATIC) or not os.path.isfile(f):
            return self.send_json({"error": "not found"}, 404)
        with open(f, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(data, ctype, cache=False)

    def audio(self, rel, qs):
        rel = urllib.parse.unquote(rel)
        base = TMP if rel.startswith("tmp/") else AUDIO
        f = os.path.normpath(os.path.join(base, rel[4:] if rel.startswith("tmp/") else rel))
        if not f.startswith(base) or not os.path.isfile(f):
            return self.send_json({"error": "not found"}, 404)
        fmt = qs.get("format", ["wav"])[0]
        name = qs.get("name", [None])[0]
        if fmt == "mp3":
            mp3 = f[:-4] + ".mp3"
            if not os.path.exists(mp3):
                ffmpeg(["-i", f, "-c:a", "libmp3lame", "-q:a", "2", mp3])
            f = mp3
        with open(f, "rb") as fh:
            data = fh.read()
        self.send_bytes(data, "audio/mpeg" if fmt == "mp3" else "audio/wav",
                        (safe_name(name) + "." + fmt) if name else None)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")


def main():
    cleanup_tmp()
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), H)
    print("TTS 聲音工坊已啟動： http://localhost:%s   (ComfyUI: %s)" % (CFG["port"], CFG["comfy_url"]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
