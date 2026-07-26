#!/usr/bin/env bash
# 一键启动：首次自动创建 venv 并安装依赖，之后直接运行
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v $PY >/dev/null 2>&1; then PY=python; fi

if [ ! -d ".venv" ]; then
  echo "[开发调试] 首次运行：创建虚拟环境并安装依赖…"
  $PY -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

exec ./.venv/bin/python main.py
