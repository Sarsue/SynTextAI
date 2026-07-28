#!/bin/bash
set -euo pipefail

WHISPER_CACHE_DIR=${WHISPER_CACHE_DIR:-/app/models}
WHISPER_MODEL_NAME=${WHISPER_MODEL_NAME:-small}
MODEL_PATH="$WHISPER_CACHE_DIR/faster-whisper-${WHISPER_MODEL_NAME}"

echo "📦 Checking for Whisper model in $MODEL_PATH"

if [ -d "$MODEL_PATH" ]; then
    echo "✅ Found existing Whisper model"
    exit 0
fi

# Create the directory if it doesn't exist
mkdir -p "$WHISPER_CACHE_DIR"

# Download the model with retries
MAX_RETRIES=${WHISPER_DOWNLOAD_MAX_RETRIES:-5}
BASE_SLEEP_SECONDS=${WHISPER_DOWNLOAD_BASE_SLEEP_SECONDS:-5}

for i in $(seq 1 "$MAX_RETRIES"); do
    echo "Attempt $i/$MAX_RETRIES: Downloading Whisper model (${WHISPER_MODEL_NAME})..."
    if python3 - <<PY
import os
import sys

cache_dir = os.environ.get("WHISPER_CACHE_DIR", "/app/models")
model_name = os.environ.get("WHISPER_MODEL_NAME", "small")

try:
    from faster_whisper import download_model
except Exception as e:
    print(f"❌ faster_whisper import failed: {e}")
    sys.exit(2)

print(f"Starting model download: {model_name} -> {cache_dir}")
try:
    download_model(model_name, output_dir=cache_dir, local_files_only=False)
    print("✅ Successfully downloaded Whisper model")
    sys.exit(0)
except Exception as e:
    print(f"❌ Download failed: {e}")
    sys.exit(1)
PY
    then
        echo "✅ Successfully downloaded Whisper model"
        exit 0
    fi

    if [ "$i" -lt "$MAX_RETRIES" ]; then
        sleep_seconds=$((BASE_SLEEP_SECONDS * i))
        echo "⏳ Retry in ${sleep_seconds}s..."
        sleep "$sleep_seconds"
    fi
done

echo "❌ Failed to download Whisper model after ${MAX_RETRIES} attempts"
exit 1
