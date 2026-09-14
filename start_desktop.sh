#!/usr/bin/env bash
# CodeBuddy2API Desktop Launcher (macOS / Linux)
# Double-click this file, or run: ./start_desktop.sh
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "未找到 python3，请先安装 Python 3.9 或更高版本。" >&2
    read -r -p "按回车键退出..." _
    exit 1
fi

# Create a virtual environment on first run so dependencies stay isolated.
if [ ! -d "venv" ]; then
    echo "首次运行：正在创建虚拟环境..."
    "$PYTHON_BIN" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

if ! python -c "import webview" >/dev/null 2>&1; then
    echo "正在安装依赖（首次运行需要几分钟）..."
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
fi

echo "正在启动 CodeBuddy2API 桌面应用..."
exec python desktop.py
