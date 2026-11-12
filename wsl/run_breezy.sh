#!/bin/bash
# 在 WSL 啟動 BreezyVoice 服務（port 9880）
export PYTHONUTF8=1
# 讀專案根目錄 .env 的 BREEZY_DIR（環境變數優先）
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[ -z "$BREEZY_DIR" ] && [ -f "$ENV_FILE" ] && BREEZY_DIR="$(sed -n 's/^BREEZY_DIR=//p' "$ENV_FILE" | tr -d '\r' | tail -1)"
# BREEZY_DIR：BreezyVoice 安裝目錄（預設 ~/tts/BreezyVoice）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${BREEZY_DIR:-$HOME/tts/BreezyVoice}"
cp "$SCRIPT_DIR/breezy_server.py" ./breezy_server.py
exec .venv/bin/python breezy_server.py
