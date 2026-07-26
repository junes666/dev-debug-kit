# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：开发调试 · Dev Debug Kit（单文件 exe）。

在 Windows 上：  pip install -r requirements.txt pyinstaller
                pyinstaller --noconfirm --clean devdebug.spec
产物：dist/开发调试.exe
"""

import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# 需随程序一起打包的数据文件（运行时读取）
datas = [
    ("lib", "lib"),        # beautify.js / terser.min.js（JS 引擎使用）
    ("assets", "assets"),  # 图标 / 树 +- 图标
]
# 翻译模型（若存在则一并打包，约 160MB）
if os.path.isdir("models"):
    datas.append(("models", "models"))

# main.py 通过 importlib 动态加载模块，需显式声明，否则打包后找不到
hiddenimports = [
    "app.modules.http_tool",
    "app.modules.js_tool",
    "app.modules.json_tool",
    "app.modules.jsondiff_tool",
    "app.modules.codec_tool",
    "app.modules.qrcode_tool",
    "app.modules.translate_tool",
    "app.modules.regex_tool",
    "ctranslate2", "sentencepiece",
]

# 翻译引擎的本地动态库/数据（被 try-import 保护，需显式收集）
binaries = []
for _pkg in ("ctranslate2", "sentencepiece"):
    try:
        binaries += collect_dynamic_libs(_pkg)
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# 排除用不到的重型 Qt 模块，显著减小体积
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQmlModels",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# onedir（文件夹）模式：不再每次启动解包，秒启动。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="开发调试",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI 应用，不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="开发调试",
)
