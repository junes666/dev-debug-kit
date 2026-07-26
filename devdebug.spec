# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：开发调试 · Dev Debug Kit（onedir，秒启动）。

一份 spec 出两个版本，用环境变量切换：
  - 全离线版：DEVDEBUG_FULL=1 pyinstaller --noconfirm --clean devdebug.spec
      内置 ctranslate2 + sentencepiece + numpy + models/，翻译开箱即用。
  - 精简版（默认）：pyinstaller --noconfirm --clean devdebug.spec
      不含翻译运行库与模型，翻译首次使用时自动下载；体积几十 MB。

打包后建议运行 scripts/trim_qt.py 清理未用的 Qt 组件。
产物：dist/开发调试/开发调试.exe
"""
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

FULL = os.environ.get("DEVDEBUG_FULL") == "1"

# ---- 数据文件 ----
datas = [
    ("lib", "lib"),        # beautify.js / terser.min.js（JS 引擎）
    ("assets", "assets"),  # 图标 / 树 +- 图标
]
if FULL and os.path.isdir("models"):
    datas.append(("models", "models"))   # 仅全离线版内置翻译模型（~160MB）

# ---- 动态导入的模块（main.py 用 importlib 加载）----
hiddenimports = [
    "app.modules.http_tool", "app.modules.js_tool", "app.modules.json_tool",
    "app.modules.jsondiff_tool", "app.modules.codec_tool", "app.modules.qrcode_tool",
    "app.modules.translate_tool", "app.modules.regex_tool",
    "app.translate_component",
    "zxingcpp", "PIL.Image",       # 二维码解码（轻量，替代 opencv）
]

# ---- 翻译运行库：仅全离线版内置 ----
binaries = []
if FULL:
    hiddenimports += ["ctranslate2", "sentencepiece", "numpy"]
    for _pkg in ("ctranslate2", "sentencepiece"):
        try:
            binaries += collect_dynamic_libs(_pkg)
            datas += collect_data_files(_pkg)
        except Exception:
            pass

# ---- 排除 ----
excludes = [
    "tkinter", "unittest", "pydoc",
    "opencv-python", "cv2",               # 已用 zxingcpp 替代
    # 未用的重型 Qt 模块
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
if not FULL:
    # 精简版：翻译运行库不内置（首次使用时下载）
    excludes += ["ctranslate2", "sentencepiece", "numpy"]

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
    upx=False,               # 关闭 upx：避免部分 Windows/杀软下 DLL 加载异常
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
