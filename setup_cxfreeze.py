# -*- coding: utf-8 -*-
"""cx_Freeze 打包脚本（备选，wine 友好）。

用法：python setup_cxfreeze.py build_exe
产物：build/exe.win-amd64-3.x/开发调试.exe（及依赖，整个文件夹分发）
"""
import sys
# 先导入 PySide6 子模块，使其绑定为 PySide6 的属性，
# 规避 cx_Freeze qthooks 对 PySide6 6.11 使用 getattr 解析子模块失败的问题。
import PySide6.QtCore  # noqa: F401
import PySide6.QtGui   # noqa: F401
import PySide6.QtWidgets  # noqa: F401
from cx_Freeze import setup, Executable

build_exe_options = {
    "packages": ["app", "app.modules", "PySide6", "shiboken6", "quickjs", "segno",
                 "Crypto", "zxingcpp", "PIL", "urllib", "json", "hashlib", "hmac"],
    "includes": [
        "app.modules.http_tool", "app.modules.js_tool", "app.modules.json_tool",
        "app.modules.jsondiff_tool", "app.modules.codec_tool", "app.modules.qrcode_tool",
    ],
    "include_files": [("lib/", "lib/"), ("assets/", "assets/")],
    "excludes": [
        "tkinter", "test", "unittest", "pydoc",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtDesigner", "PySide6.QtWebSockets",
    ],
    "include_msvcr": True,
    "optimize": 1,
}

base = "gui" if sys.platform == "win32" else None

setup(
    name="dev-debug-kit",
    version="1.0.0",
    description="开发调试 · Dev Debug Kit —— 离线一体化开发调试工具箱",
    options={"build_exe": build_exe_options},
    executables=[Executable(
        "main.py",
        base=base,
        target_name="开发调试.exe",
        icon="assets/icon.ico",
    )],
)
