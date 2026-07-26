"""翻译组件管理：定位 / 检测 / 下载 翻译运行库(ctranslate2 + sentencepiece + numpy)与中英模型。

两种发行版共用同一套代码：
- **全离线版**：ct2/sentencepiece/numpy 已随包内置、models/ 已内置 → is_ready() 直接 True，翻译开箱即用。
- **精简版**：主包不含这些 → 首次使用翻译时，从 PyPI 下载 wheel、从 argos 下载模型，
  解压到 exe 同级的 translate_data/，通过 sys.path + os.add_dll_directory 载入，之后永久离线。

设计要点：绝不在导入时加载 ctranslate2 的原生 DLL（那在真 Windows 上可能硬崩），
只用 importlib.util.find_spec 探测。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import zipfile
import urllib.request
from pathlib import Path

from app.resources import res, base_dir

# 需要的运行库 wheel（与打包所用 Python 3.12 / win_amd64 匹配）
_WHEELS = [
    ("ctranslate2", "4.8.1"),
    ("sentencepiece", None),   # None = 取最新
    ("numpy", None),
]
_MODEL_URLS = {
    "zh_en": "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel",
    "en_zh": "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel",
}
_UA = {"User-Agent": "Mozilla/5.0 (DevDebugKit)"}


# --------------------------------------------------------------------------- #
#  路径
# --------------------------------------------------------------------------- #
def _writable_root() -> Path:
    """可写的外置组件根目录：冻结后放 exe 同级；源码运行放项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "translate_data"
    return base_dir()


def _libs_dir() -> Path:
    return _writable_root() / "libs"


def models_root() -> Path:
    """模型根目录：优先随包内置的 models/（全离线版），否则外置目录。"""
    bundled = res("models")
    if (bundled / "zh_en" / "model").is_dir():
        return bundled
    return _writable_root() / "models"


def _add_libs_to_path():
    """把外置 libs 目录加入模块与 DLL 搜索路径（幂等）。"""
    d = _libs_dir()
    if not d.is_dir():
        return
    p = str(d)
    if p not in sys.path:
        sys.path.insert(0, p)
    if hasattr(os, "add_dll_directory"):
        for sub in ("ctranslate2", "numpy.libs", "numpy/.libs"):
            dd = d / sub
            if dd.is_dir():
                try:
                    os.add_dll_directory(str(dd))
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
#  检测
# --------------------------------------------------------------------------- #
def deps_available() -> bool:
    """ctranslate2 + sentencepiece 是否可用（不加载其 DLL，只探测）。"""
    _add_libs_to_path()
    try:
        return (importlib.util.find_spec("ctranslate2") is not None
                and importlib.util.find_spec("sentencepiece") is not None)
    except Exception:
        return False


def models_available() -> bool:
    r = models_root()
    return all((r / n / "model").is_dir() for n in ("zh_en", "en_zh"))


def is_ready() -> bool:
    return deps_available() and models_available()


def missing_summary() -> str:
    miss = []
    if not deps_available():
        miss.append("翻译运行库")
    if not models_available():
        miss.append("中英模型")
    return "、".join(miss)


# --------------------------------------------------------------------------- #
#  下载
# --------------------------------------------------------------------------- #
def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def _http_download(url: str, on_bytes=None) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if on_bytes:
                on_bytes(got, total)
        return buf.getvalue()


def _pypi_wheel_url(pkg: str, version: str | None) -> str:
    """从 PyPI 找匹配 cp312/abi3 + win_amd64（或纯 py3）的 wheel。"""
    api = f"https://pypi.org/pypi/{pkg}/{version}/json" if version else f"https://pypi.org/pypi/{pkg}/json"
    data = json.loads(_http_get(api))
    files = data["urls"] if version else data["releases"][data["info"]["version"]]
    cands = [f for f in files if f["filename"].endswith(".whl")]

    def score(fn: str) -> int:
        s = 0
        if "win_amd64" in fn:
            s += 4
        if "cp312" in fn:
            s += 2
        elif "abi3" in fn or "py3-none-any" in fn:
            s += 1
        return s
    cands = [f for f in cands if "win_amd64" in f["filename"] or "py3-none-any" in f["filename"]]
    cands = [f for f in cands if ("cp312" in f["filename"] or "abi3" in f["filename"]
                                  or "py3-none-any" in f["filename"])]
    if not cands:
        raise RuntimeError(f"未找到 {pkg} 的合适 wheel")
    best = max(cands, key=lambda f: score(f["filename"]))
    return best["url"]


def download_all(progress=None):
    """下载并部署翻译组件。progress(stage:str, done:int, total:int) 可选。

    抛异常表示失败（调用方捕获并提示）。成功后 is_ready() 应为 True。
    """
    root = _writable_root()
    libs = _libs_dir()
    libs.mkdir(parents=True, exist_ok=True)
    (root / "models").mkdir(parents=True, exist_ok=True)

    # 1) 运行库 wheels
    for pkg, ver in _WHEELS:
        if pkg == "ctranslate2" and importlib.util.find_spec("ctranslate2"):
            continue
        if progress:
            progress(f"下载 {pkg}", 0, 0)
        url = _pypi_wheel_url(pkg, ver)
        data = _http_download(url, lambda d, t, p=pkg: progress and progress(f"下载 {p}", d, t))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(libs)
        if pkg == "ctranslate2":
            _strip_cuda(libs / "ctranslate2")   # 删掉 CPU 用不到的 CUDA DLL，避免真机加载崩

    # 2) 模型
    for name, url in _MODEL_URLS.items():
        dst = root / "models" / name
        if (dst / "model").is_dir():
            continue
        if progress:
            progress(f"下载模型 {name}", 0, 0)
        data = _http_download(url, lambda d, t, n=name: progress and progress(f"下载模型 {n}", d, t))
        _extract_argosmodel(data, dst)

    _add_libs_to_path()
    if not is_ready():
        raise RuntimeError("组件已下载但仍不可用，请检查磁盘空间/权限")


def _strip_cuda(ct2_dir: Path):
    """删除 ctranslate2 目录下 CPU 推理用不到的 CUDA 相关 DLL（cudnn/cublas/cudart 等），
    避免在无 CUDA 的真 Windows 上加载这些 DLL 触发崩溃。"""
    if not ct2_dir.is_dir():
        return
    for f in ct2_dir.iterdir():
        low = f.name.lower()
        if any(k in low for k in ("cudnn", "cublas", "cudart", "cuda")):
            try:
                f.unlink()
            except Exception:
                pass


def _extract_argosmodel(data: bytes, dst: Path):
    """.argosmodel 是 zip，内含 model/(ct2) 与 sentencepiece.model。抽取到 dst。"""
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        # 找到 model/ 与 sentencepiece.model 所在前缀
        for n in names:
            if n.endswith("/"):
                continue
            rel = n.split("/", 1)[1] if "/" in n and not n.startswith("model/") else n
            # 统一落到 dst 下，保留 model/ 结构与 sentencepiece.model
            low = n.lower()
            if "sentencepiece" in low and low.endswith(".model"):
                (dst / "sentencepiece.model").write_bytes(zf.read(n))
            elif "/model/" in ("/" + n) or n.startswith("model/") or "/model/" in n:
                idx = n.find("model/")
                sub = n[idx:]  # model/....
                out = dst / sub
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(n))
