#!/bin/bash
export PYTHONUTF8=1
# 讀專案根目錄 .env 的 BREEZY_DIR（環境變數優先）
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[ -z "$BREEZY_DIR" ] && [ -f "$ENV_FILE" ] && BREEZY_DIR="$(sed -n 's/^BREEZY_DIR=//p' "$ENV_FILE" | tr -d '\r' | tail -1)"
cd "${BREEZY_DIR:-$HOME/tts/BreezyVoice}"
ls data
time .venv/bin/python single_inference.py --content_to_synthesize "任務已經完成，我們撤退吧。記住，今天的事誰都不准說出去。這裡沒有你想像的那麼安全。" --speaker_prompt_audio_path "./data/example.wav" --output_path results/test1.wav 2>&1 | grep -vE "Warning|warn|^\s*$" | tail -15
ls -la results
