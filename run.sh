#!/bin/bash
# Quick launcher for DenseWealth paper trader (Linux/Mac)

set -e

echo ""
echo "========================================"
echo "  DENSEWEALTH - Polymarket Paper Trader"
echo "========================================"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ ERROR: Python 3 not found"
    echo "   Please install Python 3.10+"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ ERROR: Python 3.10+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Check if dependencies are installed
if ! python3 -c "import httpx" &> /dev/null; then
    echo "📦 Installing dependencies..."
    python3 -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Failed to install dependencies"
        exit 1
    fi
fi

# Run health check
echo "🏥 Running health check..."
echo ""
python3 healthcheck.py
if [ $? -ne 0 ]; then
    echo ""
    echo "Fix the issues above before starting."
    exit 1
fi

# Start the bot
echo ""
echo "🚀 Starting paper trader..."
echo "Press Ctrl+C to stop"
echo ""
python3 main.py
