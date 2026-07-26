"""资源路径解析：兼容源码运行与 PyInstaller 冻结打包两种情形。"""
from __future__ import annotations

import sys
import pathlib


def base_dir() -> pathlib.Path:
    # PyInstaller 冻结后，数据文件被解包到 sys._MEIPASS
    if getattr(sys, "frozen", False):
        return pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    return pathlib.Path(__file__).resolve().parent.parent


def res(rel: str) -> pathlib.Path:
    return base_dir() / rel
