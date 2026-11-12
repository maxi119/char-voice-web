# 聲音工坊 — ComfyUI Qwen3-TTS 簡化操作網站

雙擊 `start.bat`（或 `python server.py`），然後開啟 http://localhost:8190
區網其他電腦：http://192.168.50.55:8190

## 引擎
| 引擎 | 用途 | 執行位置 |
|---|---|---|
| Qwen3-TTS（ComfyUI） | 依描述設計聲音、內建音色、角色複製 | ComfyUI（Windows） |
| **BreezyVoice**（聯發科） | **台灣腔**角色朗讀、台灣腔重念；支援注音校正 `行[:ㄒㄧㄥ2]` | WSL2 Ubuntu，port 9880 |

建議流程：先用 Qwen 依描述設計音色，勾選「用 BreezyVoice 以台灣腔重念」，存角色時參考音選台灣腔版；台詞生成時引擎選 BreezyVoice。

BreezyVoice 安裝（WSL）：`wsl -d Ubuntu -- bash /mnt/c/Users/User/Documents/GitHub/tts-web/wsl/install_breezy.sh`
手動啟動：`wsl -d Ubuntu -- bash /mnt/c/Users/User/Documents/GitHub/tts-web/wsl/run_breezy.sh`（start.bat 會自動啟動）

## 前置需求
- ComfyUI 已安裝 [ComfyUI-QwenTTS](https://github.com/1038lab/ComfyUI-QwenTTS)（第一次生成時會自動下載模型）
- ffmpeg（已安裝）、Python 3.10+（只用標準函式庫，不需要 pip install）

## 功能
- **設計聲音**：用文字描述 → Qwen3-TTS VoiceDesign 生成多個候選 → 存成角色
- **上傳錄音建立角色**：用現成錄音（3–15 秒）複製音色
- **台詞生成**：用角色音色（VoiceClone）或內建音色（CustomVoice＋語氣指示）朗讀
  - 語速／整體音高：rubberband 後製處理（保留共振峰）
  - 重點字：`[重]文字[/]` 加重、`[高]` 提高音調、`[低]` `[慢]` `[快]` `[輕]`，可以組合 `[重 高]`
  - 自訂：`[v+4 p+2 s0.9]文字[/]`（音量 dB／音高半音／速度倍率）
  - 停頓：`[停0.5]`
- **聲音庫**：角色卡、片段收藏／備註／標籤、設為參考音、WAV／MP3 下載、zip 匯出匯入

## 資料
- `data/library.json`：角色與片段資料；`data/audio/`：音檔
- `config.json`：ComfyUI 位址與重點字效果預設

## 原理說明
- 整句一次生成，所以語氣和發音都比較自然。生成後用 Whisper（large-v3-turbo，在 ComfyUI 的 Python 裡執行）找出重點字的時間位置，只對那一段套用音量、音高、速度效果。
- 只有 `[停]` 會把句子切開。
- 找不到 Whisper 時，改用字數比例估計位置。
- 對齊程序閒置 5 分鐘後會自動關閉，釋放顯存（約 1.6GB）。
- 繁轉簡：Qwen3-TTS 讀繁體字時會偏港腔，也常念錯，所以送進 Qwen 前會自動轉成簡體。BreezyVoice 則直接用繁體。
- 顯存：BreezyVoice 約 2GB、Qwen 約 4.2GB、Whisper 約 1.6GB。可用顯存不足 3GB 時，Whisper 會自動改用 CPU（每句約 10 秒）。
- 口音：設定頁的「口音指示」會自動加在聲音描述後面，預設是台灣口音國語。語言選「自動」時會改用中文，避免模型猜成粵語。
- 防失控：會依字數限制最長生成長度；如果輸出異常過長，會自動換種子重試。

## ComfyUI 端的變更（安裝時做的）
- 安裝 `custom_nodes/ComfyUI-QwenTTS` 和它的相依套件，另外補裝 `sox`。
- transformers 由 5.10.1 降到 4.57.3，因為 Qwen3-TTS 官方只支援這個版本。
- 安裝前的套件清單備份在 `E:\ComfyUI_windows_portable\pip_freeze_before_qwentts.txt`。
- 還原指令：`python_embeded\python.exe -m pip install transformers==5.10.1 huggingface_hub==1.17.0`
