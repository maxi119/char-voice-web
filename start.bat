@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 在 WSL 啟動 BreezyVoice（台灣腔）服務
rem 讀 .env（系統環境變數優先）
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not defined %%a if not "%%b"=="" set "%%a=%%b"
rem WSL_DISTRO：WSL 發行版名稱（預設 Ubuntu）；BREEZY_DIR 透過 WSLENV 傳進 WSL
if not defined WSL_DISTRO set "WSL_DISTRO=Ubuntu"
if defined BREEZY_DIR set "WSLENV=%WSLENV%:BREEZY_DIR"
start "BreezyVoice" /min wsl -d %WSL_DISTRO% --cd "%~dp0wsl" -- bash ./run_breezy.sh
python -m pip install --user -q opencc-python-reimplemented >nul 2>&1
start "" http://localhost:8190
python server.py
pause
