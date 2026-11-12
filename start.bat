@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 在 WSL 啟動 BreezyVoice（台灣腔）服務
start "BreezyVoice" /min wsl -d Ubuntu -- bash /mnt/c/Users/User/Documents/GitHub/tts-web/wsl/run_breezy.sh
python -m pip install --user -q opencc-python-reimplemented >nul 2>&1
start "" http://localhost:8190
python server.py
pause
