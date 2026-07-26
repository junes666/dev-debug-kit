"""打包后裁剪未用的 Qt 组件（基于实测：本应用只用 QtWidgets/QtGui/QtCore）。

用法：python scripts/trim_qt.py <dist/开发调试 目录>
删除的都是运行时未加载的组件；保留 qwindows 平台插件、windowsvistastyle 样式、
png/jpg/gif/ico/webp 图像插件（保证二维码"从图片解析"等功能正常）。
"""
import os
import shutil
import sys
from pathlib import Path

# 未用的 Qt 模块 DLL / 绑定（被 hook 依赖链拖入，excludes 删不掉，需删文件）
DLL_NAMES = [
    "Qt6Quick.dll", "Qt6Pdf.dll", "Qt6Qml.dll", "Qt6QmlModels.dll",
    "Qt6OpenGL.dll", "Qt6Network.dll", "Qt6VirtualKeyboard.dll", "Qt6Svg.dll",
    "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll", "Qt6OpenGLWidgets.dll",
    "QtNetwork.pyd", "QtOpenGL.pyd", "QtSvg.pyd",
    "opengl32sw.dll",   # 软件 OpenGL 20MB：纯 widgets 光栅渲染无需（真 Windows 有 GL）
    "d3dcompiler_47.dll",  # D3D 编译器，仅 ANGLE/QtQuick 需要
    "Qt6Qml.dll",
]
# 整目录删除（相对 PySide6/）
DIR_NAMES = ["translations", "qml"]
# plugins 下未用的子目录
PLUGIN_DIRS = ["tls", "networkinformation", "generic", "sqldrivers",
               "multimedia", "position", "sensors", "canbus", "webview",
               "virtualkeyboard", "designer"]
# platforms 里只保留 qwindows；imageformats 只保留常用格式
KEEP_PLATFORMS = {"qwindows.dll"}
KEEP_IMAGEFORMATS = {"qjpeg.dll", "qgif.dll", "qico.dll", "qwebp.dll"}


def _rm(p: Path):
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
        return True
    except Exception:
        return False


def trim(app_dir: Path) -> int:
    ps = app_dir / "_internal" / "PySide6"
    if not ps.is_dir():
        # 有些布局把 Qt 放 _internal/PySide6/Qt/... —— 两处都试
        ps = app_dir / "_internal" / "PySide6"
    if not ps.is_dir():
        print("未找到 PySide6 目录，跳过：", ps)
        return 0
    before = _dir_size(app_dir)

    for name in DLL_NAMES:
        for f in ps.rglob(name):
            _rm(f)
    for d in DIR_NAMES:
        _rm(ps / d)
        _rm(ps / "Qt" / d)

    # plugins 清理
    for base in (ps / "plugins", ps / "Qt" / "plugins"):
        if not base.is_dir():
            continue
        for d in PLUGIN_DIRS:
            _rm(base / d)
        # platforms：只留 qwindows
        plat = base / "platforms"
        if plat.is_dir():
            for f in plat.iterdir():
                if f.name not in KEEP_PLATFORMS:
                    _rm(f)
        # imageformats：只留常用
        imgf = base / "imageformats"
        if imgf.is_dir():
            for f in imgf.iterdir():
                if f.name not in KEEP_IMAGEFORMATS:
                    _rm(f)
        # iconengines：删 svg 图标引擎
        _rm(base / "iconengines" / "qsvgicon.dll")

    # 全离线版：删除 ctranslate2 里 CPU 用不到的 CUDA DLL（避免真机加载崩）
    ct2 = app_dir / "_internal" / "ctranslate2"
    if ct2.is_dir():
        for f in ct2.iterdir():
            low = f.name.lower()
            if any(k in low for k in ("cudnn", "cublas", "cudart", "cuda")):
                _rm(f)

    after = _dir_size(app_dir)
    saved = (before - after) / 1048576
    print(f"Qt 裁剪完成：{before/1048576:.0f}MB -> {after/1048576:.0f}MB，省 {saved:.0f}MB")
    return int(saved)


def _dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return total


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/trim_qt.py <dist/开发调试 目录>")
        sys.exit(1)
    trim(Path(sys.argv[1]))
