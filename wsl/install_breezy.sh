#!/bin/bash
set -e
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# 讀專案根目錄 .env 的 BREEZY_DIR（環境變數優先）
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[ -z "$BREEZY_DIR" ] && [ -f "$ENV_FILE" ] && BREEZY_DIR="$(sed -n 's/^BREEZY_DIR=//p' "$ENV_FILE" | tr -d '\r' | tail -1)"
# BREEZY_DIR：BreezyVoice 安裝目錄（預設 ~/tts/BreezyVoice）
BREEZY_DIR="${BREEZY_DIR:-$HOME/tts/BreezyVoice}"
mkdir -p "$(dirname "$BREEZY_DIR")"
[ -d "$BREEZY_DIR" ] || git clone --recursive https://github.com/mtkresearch/BreezyVoice.git "$BREEZY_DIR"
cd "$BREEZY_DIR"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -d .venv ] || uv venv -p 3.10 .venv -q
grep -v deepspeed requirements.txt > req_nods.txt
VIRTUAL_ENV=.venv uv pip install -q "setuptools<70" wheel
VIRTUAL_ENV=.venv uv pip install -r req_nods.txt --index-strategy unsafe-best-match --no-build-isolation-package openai-whisper 2>&1 | tail -8
VIRTUAL_ENV=.venv uv pip install -q "ruamel.yaml<0.18" "transformers==4.44.2" "numpy<2"
.venv/bin/python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"
