#!/usr/bin/env bash
# 在 Linux / macOS 上打包成本平台可执行文件（产物：dist/开发调试）
set -e
cd "$(dirname "$0")"

PY=python3
command -v $PY >/dev/null 2>&1 || PY=python

echo "[开发调试] 安装依赖与打包工具..."
$PY -m pip install -r requirements.txt pyinstaller

echo "[开发调试] 开始打包（首次较慢）..."
$PY -m PyInstaller --noconfirm --clean devdebug.spec

echo
echo "打包完成：dist/开发调试"
