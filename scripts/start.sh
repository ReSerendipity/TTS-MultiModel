#!/bin/bash
set -e

echo "=== TTS MultiModel Startup Script ==="

VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

PORT=${PORT:-7869}
HOST=${HOST:-127.0.0.1}

# Handle --download flag
if [ "$1" = "--download" ]; then
    echo "Downloading models..."
    pip install modelscope -q 2>/dev/null || true
    echo ""
    echo "VoxCPM2:"
    echo "  modelscope download OpenBMB/VoxCPM2 --local_dir model/VoxCPM2"
    echo ""
    echo "IndexTTS2:"
    echo "  modelscope download IndexTeam/IndexTTS-2 --local_dir model/IndexTTS2"
    echo ""
    echo "After downloading, run: ./start.sh"
    exit 0
fi

# Check if models exist
MISSING=""
if [ ! -d "model/VoxCPM2" ] || [ -z "$(ls model/VoxCPM2 2>/dev/null)" ]; then
    MISSING="VoxCPM2"
fi
if [ ! -d "model/IndexTTS2" ] || [ -z "$(ls model/IndexTTS2 2>/dev/null)" ]; then
    MISSING="$MISSING IndexTTS2"
fi

if [ -n "$MISSING" ]; then
    echo "[WARNING] Missing models:$MISSING"
    echo "  Run './start.sh --download' for download instructions."
    echo ""
fi

echo "Starting server: http://${HOST}:${PORT}"
python -c "from integrated_app.app_server import run_server; run_server('${HOST}', ${PORT})"
