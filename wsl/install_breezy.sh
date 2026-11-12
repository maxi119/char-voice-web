#!/bin/bash
set -e
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p ~/tts && cd ~/tts
[ -d BreezyVoice ] || git clone --recursive https://github.com/mtkresearch/BreezyVoice.git
cd BreezyVoice
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -d .venv ] || uv venv -p 3.10 .venv -q
grep -v deepspeed requirements.txt > req_nods.txt
VIRTUAL_ENV=.venv uv pip install -q "setuptools<70" wheel
VIRTUAL_ENV=.venv uv pip install -r req_nods.txt --index-strategy unsafe-best-match --no-build-isolation-package openai-whisper 2>&1 | tail -8
VIRTUAL_ENV=.venv uv pip install -q "ruamel.yaml<0.18" "transformers==4.44.2" "numpy<2"
.venv/bin/python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"
