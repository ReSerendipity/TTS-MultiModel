#!/bin/bash
set -e

echo "=== TTS MultiModel Installation Script ==="
echo ""

# Check Python version
PYTHON=${PYTHON:-python3}
if ! command -v "$PYTHON" &>/dev/null; then
    echo "[ERROR] $PYTHON not found. Please install Python 3.10+."
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PY_VERSION"

# Check GPU availability
if command -v nvidia-smi &>/dev/null; then
    echo "NVIDIA GPU detected:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "  (nvidia-smi available but GPU info unavailable)"
else
    echo "[WARNING] nvidia-smi not found. GPU acceleration may not be available."
    echo "  For GPU support, install NVIDIA drivers and CUDA toolkit."
fi
echo ""

# Create virtual environment
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -e . -q

# Enable git hooks (hooksPath is local config, safe to set automatically)
if [ -f .githooks/install.sh ]; then
    echo "Enabling git hooks..."
    sh .githooks/install.sh
    echo ""
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Download models:  ./start.sh --download"
echo "  2. Start server:     ./start.sh"
echo "  3. Open browser:     http://127.0.0.1:7869"
echo ""
