# -*- coding: utf-8 -*-
"""
BreezyVoice 常駐服務（在 WSL 的 $BREEZY_DIR（預設 ~/tts/BreezyVoice）目錄、用其 .venv 執行）
POST /tts  JSON {"prompt_wav_b64": "...", "prompt_text": "參考音逐字稿", "text": "要念的內容"}
回傳 audio/wav（22050 Hz）
GET /health
"""
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "third_party", "Matcha-TTS"))

import torch  # noqa: E402
import torchaudio  # noqa: E402
from g2pw import G2PWConverter  # noqa: E402

import single_inference as si  # noqa: E402
from cosyvoice.utils.file_utils import load_wav  # noqa: E402

MODEL = os.environ.get("BREEZY_MODEL", "MediaTek-Research/BreezyVoice-300M")
PORT = int(os.environ.get("BREEZY_PORT", "9880"))

print("[BreezyVoice] 載入模型…", flush=True)
t0 = time.time()
COSY = si.CustomCosyVoice(MODEL)
G2P = G2PWConverter()
LOCK = threading.Lock()
print("[BreezyVoice] 就緒（%.1fs）port %d" % (time.time() - t0, PORT), flush=True)


def synth(prompt_wav_bytes, prompt_text, text):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(prompt_wav_bytes)
        path = f.name
    try:
        prompt16 = load_wav(path, 16000)
    finally:
        os.remove(path)
    pt = COSY.frontend.text_normalize_new(prompt_text, split=False)
    ct = COSY.frontend.text_normalize_new(text, split=False)
    pt_b = si.get_bopomofo_rare(pt, G2P)
    ct_b = si.get_bopomofo_rare(ct, G2P)
    with LOCK, torch.inference_mode():
        out = COSY.inference_zero_shot_no_normalize(ct_b, pt_b, prompt16)
    buf = io.BytesIO()
    torchaudio.save(buf, out["tts_speech"].cpu(), 22050, format="wav")
    return buf.getvalue(), ct_b


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            info = {"ok": True, "model": MODEL, "cuda": torch.cuda.is_available(),
                    "vram_gb": round(torch.cuda.memory_allocated() / 1e9, 2) if torch.cuda.is_available() else 0}
            return self._send(200, json.dumps(info).encode(), "application/json")
        self._send(404, b"{}", "application/json")

    def do_POST(self):
        if self.path != "/tts":
            return self._send(404, b"{}", "application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            p = json.loads(self.rfile.read(n).decode("utf-8"))
            t = time.time()
            wav, bopo = synth(base64.b64decode(p["prompt_wav_b64"]), p.get("prompt_text", ""), p["text"])
            print("[BreezyVoice] %.1fs %s" % (time.time() - t, bopo[:60]), flush=True)
            self._send(200, wav, "audio/wav")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False).encode(),
                       "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
