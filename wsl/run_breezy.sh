#!/bin/bash
# 在 WSL 啟動 BreezyVoice 服務（port 9880）
export PYTHONUTF8=1
cd ~/tts/BreezyVoice
cp /mnt/c/Users/User/Documents/GitHub/tts-web/wsl/breezy_server.py ./breezy_server.py
exec .venv/bin/python breezy_server.py
