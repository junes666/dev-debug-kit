@echo off
REM 一键启动：首次自动创建 venv 并安装依赖，之后直接运行
cd /d "%~dp0"

if not exist ".venv" (
  echo [开发调试] 首次运行：创建虚拟环境并安装依赖...
  python -m venv .venv
  call .venv\Scripts\python.exe -m pip install --upgrade pip
  call .venv\Scripts\pip.exe install -r requirements.txt
)

.venv\Scripts\python.exe main.py
