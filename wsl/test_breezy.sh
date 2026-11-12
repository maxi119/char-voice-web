#!/bin/bash
export PYTHONUTF8=1
cd ~/tts/BreezyVoice
ls data
time .venv/bin/python single_inference.py --content_to_synthesize "任務已經完成，我們撤退吧。記住，今天的事誰都不准說出去。這裡沒有你想像的那麼安全。" --speaker_prompt_audio_path "./data/example.wav" --output_path results/test1.wav 2>&1 | grep -vE "Warning|warn|^\s*$" | tail -15
ls -la results
