#!/bin/bash
# One-time setup: creates a private Python environment with the app's dependencies.
cd "$(dirname "$0")"
python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && \
  .venv/bin/pip install -q -r requirements.txt && echo "Setup complete."
command -v ffmpeg >/dev/null || [ -x /opt/homebrew/bin/ffmpeg ] || \
  echo "Tip: install ffmpeg (brew install ffmpeg) for best-quality video and MP3 conversion."
