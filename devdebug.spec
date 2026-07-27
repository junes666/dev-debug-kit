# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：开发调试 · Dev Debug Kit（onedir）。

翻译/OCR 原生库（ctranslate2、sentencepiece、onnxruntime…）**不打进 exe**，
避免 Windows 上冻结导入 access violation。全离线版由 scripts/pack_offline_runtime.py
把 libs+models 放到 dist/开发调试/translate_data/。

  精简版：pyinstaller --noconfirm --clean devdebug.spec
  全离线：同上后再跑 pack_offline_runtime.py（或 build 脚本）
"""
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# ---- 数据文件：仅 UI / JS，不含翻译原生库与大模型 ----
# translate 纯 py 以文件形式打入，便于「下载离线组件」拷到 translate_data/app
datas = [
    ("lib", "lib"),
    ("assets", "assets"),
    ("app/__init__.py", "app"),
    ("app/resources.py", "app"),
    ("app/translate_engine.py", "app"),
    ("app/translate_component.py", "app"),
    ("app/translate_deps.py", "app"),
    ("app/translate_worker.py", "app"),
    ("app/translate_data_worker.py", "app"),
]

hiddenimports = [
    "app.modules.http_tool", "app.modules.js_tool", "app.modules.json_tool",
    "app.modules.jsondiff_tool", "app.modules.codec_tool", "app.modules.qrcode_tool",
    "app.modules.translate_tool", "app.modules.regex_tool",
    "app.translate_component", "app.translate_worker", "app.translate_engine",
    "app.translate_deps", "app.translate_data_worker", "app.resources",
    "zxingcpp", "PIL.Image",
]

binaries = []

# 明确排除：翻译/OCR 原生栈一律外置 translate_data，禁止打进 _internal
excludes = [
    "tkinter", "unittest", "pydoc",
    "ctranslate2", "sentencepiece", "numpy", "yaml", "PyYAML",
    "cv2", "opencv-python", "opencv-python-headless",
    "rapidocr_onnxruntime", "onnxruntime", "shapely", "pyclipper",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQmlModels",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtNetwork", "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtVirtualKeyboard",
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
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="开发调试",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="开发调试",
)
