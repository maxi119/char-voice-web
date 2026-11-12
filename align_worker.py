# -*- coding: utf-8 -*-
"""
字詞時間對齊工作程序（由 server.py 以 ComfyUI 的 Python 啟動，需要 openai-whisper）。
stdin 每行一個 JSON：{"audio": 路徑, "text": 原文, "language": "zh"}
stdout 每行回覆：@@{"times": [[start,end] 或 null, ...], "matched": n, "total": n, "transcript": "..."}
times 與原文逐字對應，標點為 null。
"""
import difflib
import json
import os
import sys

import torch
import whisper

MODEL_NAME = os.environ.get("ALIGN_MODEL", "large-v3-turbo")
PUNCT = set("，,。．.！!？?；;：:、…—-~～「」『』（）()\"'“”‘’《》〈〉[]【】 \t\r\n")
model = None


DEVICE = None


def pick_device():
    """顯存夠（> 3GB 可用）才用 GPU，否則用 CPU，避免和 TTS 模型搶顯存而溢出到共享記憶體。"""
    if os.environ.get("ALIGN_DEVICE") in ("cpu", "cuda"):
        return os.environ["ALIGN_DEVICE"]
    if torch.cuda.is_available():
        free, _ = torch.cuda.mem_get_info()
        if free > 3 * 1024 ** 3:
            return "cuda"
    return "cpu"


def load():
    global model, DEVICE
    if model is None:
        DEVICE = pick_device()
        model = whisper.load_model(MODEL_NAME, device=DEVICE)
    return model


def handle(req):
    m = load()
    text = req["text"]
    r = m.transcribe(req["audio"], language=req.get("language") or None, word_timestamps=True,
                     initial_prompt=text, condition_on_previous_text=False, fp16=(DEVICE == "cuda"))
    tchars, ttimes = [], []
    for seg in r.get("segments", []):
        for w in seg.get("words", []):
            cs = [c for c in w["word"] if c not in PUNCT]
            if not cs:
                continue
            d = (w["end"] - w["start"]) / len(cs)
            for k, c in enumerate(cs):
                tchars.append(c.lower())
                ttimes.append([w["start"] + k * d, w["start"] + (k + 1) * d])

    idx = [i for i, c in enumerate(text) if c not in PUNCT]
    ochars = [text[i].lower() for i in idx]
    sm = difflib.SequenceMatcher(None, ochars, tchars, autojunk=False)
    t = [None] * len(ochars)
    matched = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                t[i1 + k] = ttimes[j1 + k]
                matched += tag == "equal"
    known = [k for k, v in enumerate(t) if v is not None]
    if not known:
        return {"times": None, "matched": 0, "total": len(ochars), "transcript": r.get("text", ""), "device": DEVICE}
    # 未對上的字：在已知鄰居之間線性內插
    avg = sum(t[k][1] - t[k][0] for k in known) / len(known)
    for k in range(len(t)):
        if t[k] is not None:
            continue
        prev = max([p for p in known if p < k], default=None)
        nxt = min([n for n in known if n > k], default=None)
        if prev is not None and nxt is not None:
            a, b = t[prev][1], t[nxt][0]
            n = nxt - prev - 1
            step = (b - a) / max(1, n)
            pos = k - prev - 1
            t[k] = [a + pos * step, a + (pos + 1) * step]
        elif prev is not None:
            s = t[prev][1] + (k - prev - 1) * avg
            t[k] = [s, s + avg]
        else:
            e = t[nxt][0] - (nxt - k - 1) * avg
            t[k] = [max(0, e - avg), max(0, e)]
    out = [None] * len(text)
    for j, i in enumerate(idx):
        out[i] = [round(t[j][0], 3), round(t[j][1], 3)]
    return {"times": out, "matched": matched, "total": len(ochars), "transcript": r.get("text", ""), "device": DEVICE}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            res = handle(json.loads(line))
        except Exception as e:  # noqa
            res = {"error": "%s: %s" % (type(e).__name__, e)}
        sys.stdout.write("@@" + json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    main()
