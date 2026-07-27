"""翻译组件管理：离线运行库与模型的定位 / 检测 /（精简版）下载。

设计（解决 Windows 打包后 sentencepiece/onnxruntime 原生崩溃）：

- **绝不**让 PyInstaller 冻结导入 ctranslate2 / sentencepiece / onnxruntime。
  这些带 .pyd/.dll 的包必须放在 exe 同级的 ``translate_data/libs``，用普通文件系统
  路径 + ``os.add_dll_directory`` 加载，和「源码 + venv」行为一致。
- **全离线版**：安装包自带 ``translate_data/``（py + libs + models + worker）。
- **精简版**：一键下载与全离线版相同布局（需联网一次）。
- 依赖版本唯一来源：``app.translate_deps``（pack 与 download 共用）。
- 半成品：``.installing`` 存在或无 ``.runtime_ready`` 时，冻结/外置布局不得 is_ready。

本模块只做路径与探测，**不**在 import 时加载任何原生 DLL。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import sys
import zipfile
import urllib.request
from pathlib import Path

from app.resources import res, base_dir
from app import translate_deps as depspec

_MODEL_URLS = {
    "zh_en": "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel",
    "en_zh": "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel",
}
_UA = {"User-Agent": "Mozilla/5.0 (DevDebugKit)"}

_LIBS_READY = False


# --------------------------------------------------------------------------- #
#  路径
# --------------------------------------------------------------------------- #
def _writable_root() -> Path:
    """外置组件根 translate_data。

    优先级：
    1) 环境变量 DEVDEBUG_TRANSLATE_DATA（外部 CPython worker 启动时注入）
    2) 冻结主程序：exe 同级 translate_data
    3) 源码：项目根 / translate_data
    """
    env = os.environ.get("DEVDEBUG_TRANSLATE_DATA", "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "translate_data"
    return base_dir() / "translate_data"


def external_python() -> Path | None:
    """translate_data/py/python.exe —— 非冻结的真 CPython，专跑翻译/OCR。"""
    py = _writable_root() / "py" / "python.exe"
    return py if py.is_file() else None


def worker_script() -> Path | None:
    root = _writable_root()
    for name in ("worker_main.py", "translate_data_worker.py"):
        p = root / name
        if p.is_file():
            return p
    src = base_dir() / "app" / "translate_data_worker.py"
    return src if src.is_file() else None


def _libs_dir() -> Path:
    return _writable_root() / "libs"


def models_root() -> Path:
    """模型根：优先 translate_data/models；其次仓库/包内 models/（开发或旧布局）。"""
    ext = _writable_root() / "models"
    if (ext / "zh_en" / "model").is_dir() and (ext / "en_zh" / "model").is_dir():
        return ext
    bundled = res("models")
    if (bundled / "zh_en" / "model").is_dir() and (bundled / "en_zh" / "model").is_dir():
        return bundled
    return ext


def ensure_runtime_path() -> Path:
    """确保外置 libs 在模块搜索路径最前，并注册 DLL 目录。返回 libs 路径。"""
    global _LIBS_READY
    d = _libs_dir()
    if not d.is_dir():
        return d

    p = str(d.resolve())
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(p)
        except Exception:
            pass
        try:
            for root, dirs, files in os.walk(p):
                low_files = [f.lower() for f in files]
                if any(f.endswith((".dll", ".pyd", ".so")) for f in low_files):
                    try:
                        os.add_dll_directory(root)
                    except Exception:
                        pass
                dirs[:] = [x for x in dirs if x.lower() not in ("tests", "test", "__pycache__")]
        except Exception:
            pass

    try:
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

    _LIBS_READY = True
    return d


# --------------------------------------------------------------------------- #
#  检测
# --------------------------------------------------------------------------- #
def _spec_ok(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _uses_external_layout() -> bool:
    """是否走 translate_data 外置布局（冻结 exe，或已有 libs/py）。"""
    if getattr(sys, "frozen", False):
        return True
    root = _writable_root()
    return (root / "libs").is_dir() or (root / "py").is_dir() or (root / depspec.READY_MARKER).is_file()


def _install_in_progress() -> bool:
    return (_writable_root() / depspec.INSTALLING_MARKER).is_file()


def _ready_marker_ok() -> bool:
    """外置布局必须有完整安装标记；源码 venv 模式不要求。"""
    if not _uses_external_layout():
        return True
    root = _writable_root()
    if _install_in_progress():
        return False
    return (root / depspec.READY_MARKER).is_file()


def deps_available() -> bool:
    """离线翻译运行库是否可被找到（外置 libs 或开发 venv）。"""
    ensure_runtime_path()
    if _uses_external_layout():
        sp = _libs_dir() / "sentencepiece"
        ct = _libs_dir() / "ctranslate2"
        np = _libs_dir() / "numpy"
        return sp.is_dir() and ct.is_dir() and np.is_dir()
    return _spec_ok("ctranslate2") and _spec_ok("sentencepiece")


def models_available() -> bool:
    r = models_root()
    return all(
        (r / n / "model").is_dir() and (r / n / "sentencepiece.model").is_file()
        for n in ("zh_en", "en_zh")
    )


def embed_python_complete(py_dir: Path | None = None) -> bool:
    """外置 embed CPython 是否完整（非仅 python.exe+dll）。

    python312.zip 必须 size>=1MB **且** 自身是完整 ZIP（testzip），
    截断/损坏即使 >1MB 也返回 False。
    """
    py_dir = py_dir or (_writable_root() / "py")
    need = [
        py_dir / "python.exe",
        py_dir / "python312.dll",
        py_dir / "python312.zip",
    ]
    if not all(p.is_file() and p.stat().st_size > 0 for p in need):
        return False
    stdlib_zip = py_dir / "python312.zip"
    if stdlib_zip.stat().st_size < 1_000_000:
        return False
    # 关键：标准库 zip 自身完整性（不能只看 size）
    if not _zip_intact(path=stdlib_zip):
        return False
    pth_files = list(py_dir.glob("python*._pth"))
    if not pth_files:
        return False
    try:
        text = pth_files[0].read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if "import site" not in text:
        return False
    norm = text.replace("/", "\\").lower()
    if "libs" not in norm:
        return False
    if "python312.zip" not in norm and "python312.zip" not in text:
        return False
    return True


def worker_bundle_complete(root: Path | None = None) -> list[str]:
    """校验 worker 纯 Python 模块齐全。返回缺失列表（空=OK）。"""
    root = root or _writable_root()
    missing: list[str] = []
    if not (root / depspec.WORKER_ENTRY).is_file():
        missing.append(depspec.WORKER_ENTRY)
    app = root / "app"
    for name in depspec.WORKER_APP_MODULES:
        if not (app / name).is_file():
            missing.append(f"app/{name}")
    return missing


def runtime_ok() -> bool:
    """外置真 CPython worker 是否齐（完整 embed + worker 模块）。"""
    root = _writable_root()
    if not embed_python_complete(root / "py"):
        return False
    if worker_bundle_complete(root):
        return False
    return True


def is_ready() -> bool:
    """离线翻译是否可用。

    冻结 / 外置布局：运行库 + 模型 + 外置 CPython + 完整标记。
    半成品（.installing 或无 .runtime_ready）一律 False。
    """
    if _install_in_progress():
        return False
    if not (deps_available() and models_available()):
        return False
    if _uses_external_layout():
        if not runtime_ok():
            return False
        if not _ready_marker_ok():
            return False
    return True


def ocr_available() -> bool:
    """离线 OCR 是否可用。"""
    ensure_runtime_path()
    if _uses_external_layout():
        if not runtime_ok() or not _ready_marker_ok():
            return False
        return (_libs_dir() / "rapidocr_onnxruntime").is_dir() and (
            (_libs_dir() / "onnxruntime").is_dir()
        )
    return _spec_ok("rapidocr_onnxruntime") and _spec_ok("onnxruntime")


def missing_summary() -> str:
    miss = []
    if _install_in_progress():
        miss.append("安装未完成(translate_data/.installing)")
    if not deps_available():
        miss.append("离线翻译运行库(translate_data/libs)")
    if not models_available():
        miss.append("中英模型(translate_data/models)")
    if _uses_external_layout() and not runtime_ok():
        miss.append("外置Python(translate_data/py/python.exe+worker_main.py)")
    if _uses_external_layout() and not _ready_marker_ok() and not _install_in_progress():
        miss.append("完整标记(translate_data/.runtime_ready)")
    return "、".join(miss) if miss else ""


def read_installed_versions() -> dict:
    """读取 translate_data/VERSIONS.json（若有）。"""
    p = _writable_root() / "VERSIONS.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm_pkg(name: str) -> str:
    return name.strip().replace("-", "_").lower()


# dist-info / METADATA Name 与 PyPI 名的别名
_PKG_ALIASES = {
    "opencv_python_headless": "opencv_python_headless",
    "opencv-python-headless": "opencv_python_headless",
    "pillow": "pillow",
    "pyyaml": "pyyaml",
    "protobuf": "protobuf",
}


def _dep_norm(pypi: str) -> str:
    return _norm_pkg(pypi)


def _find_dist_info(libs: Path, pypi: str, version: str | None = None) -> Path | None:
    """在 libs 下找匹配 Name[/Version] 的 *.dist-info 目录。"""
    key = _dep_norm(pypi)
    for info in libs.glob("*.dist-info"):
        meta = info / "METADATA"
        name = ver = None
        if meta.is_file():
            try:
                text = meta.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            m_name = re.search(r"^Name:\s*(.+)$", text, re.M)
            m_ver = re.search(r"^Version:\s*(.+)$", text, re.M)
            if m_name:
                name = _norm_pkg(m_name.group(1))
            if m_ver:
                ver = m_ver.group(1).strip()
        if name is None:
            stem = info.name[: -len(".dist-info")]
            parts = stem.rsplit("-", 1)
            if len(parts) == 2:
                name, ver = _norm_pkg(parts[0]), parts[1]
        if name != key:
            # pillow / opencv 别名
            if key == "opencv_python_headless" and name in (
                "opencv_python_headless", "opencv_python",
            ):
                pass
            elif key == "pillow" and name == "pillow":
                pass
            else:
                continue
        if version is not None and ver != version:
            continue
        return info
    return None


def verify_record_integrity(libs: Path, dist_info: Path) -> list[str]:
    """按 wheel dist-info/RECORD 做深度完整性校验。

    RECORD 为 CSV：path,hash,size
    - 每条记录对应文件必须存在（相对 libs 根，即 flat 解压布局）
    - 若 size 非空：与实际文件大小一致
    - 若 hash 为 ``sha256=...``：按 urlsafe-base64（可无 padding）解码后与文件内容匹配
    - RECORD 自身允许空 hash / 空 size
    """
    import base64
    import csv
    import hashlib

    errors: list[str] = []
    record = dist_info / "RECORD"
    if not record.is_file():
        return [f"{dist_info.name} 缺少 RECORD"]

    try:
        text = record.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"无法读 {dist_info.name}/RECORD: {e}"]

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [f"{dist_info.name}/RECORD 为空"]

    for row in rows:
        if not row or not row[0].strip():
            continue
        rel = row[0].strip().replace("\\", "/")
        # 跳过绝对路径 / Windows 盘符异常
        if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
            errors.append(f"RECORD 非法路径: {rel}")
            continue
        hash_field = row[1].strip() if len(row) > 1 else ""
        size_field = row[2].strip() if len(row) > 2 else ""

        target = libs / rel
        # RECORD 自身：允许空 hash
        is_record_self = rel.endswith("/RECORD") or rel.endswith("\\RECORD") or rel.endswith(".dist-info/RECORD")

        if not target.is_file():
            # 目录条目偶发；RECORD 只列文件
            if target.is_dir():
                continue
            errors.append(f"RECORD 缺文件: {rel}")
            continue

        if size_field != "":
            try:
                expect_sz = int(size_field)
            except ValueError:
                errors.append(f"RECORD 坏 size {rel}: {size_field!r}")
                continue
            try:
                actual_sz = target.stat().st_size
            except Exception as e:
                errors.append(f"无法 stat {rel}: {e}")
                continue
            if actual_sz != expect_sz:
                errors.append(f"RECORD size 不匹配 {rel}: 实际 {actual_sz} != 声明 {expect_sz}")

        if hash_field == "":
            # RECORD 自身或未提供 hash：允许
            continue
        if is_record_self and not hash_field:
            continue

        # sha256=<urlsafe-b64>
        m = re.match(r"^(sha256)=([A-Za-z0-9_-]+)$", hash_field)
        if not m:
            # 其他算法少见；有声明但无法识别则报错，避免 silently pass
            if hash_field.startswith("sha256="):
                errors.append(f"RECORD 坏 sha256 编码 {rel}")
            # md5/sha1 等忽略严格？用户只要 sha256；未知算法当错误更安全
            elif "=" in hash_field:
                # 非 sha256 跳过严格校验但仍要求文件存在（已检查）
                continue
            else:
                errors.append(f"RECORD 无法解析 hash {rel}: {hash_field!r}")
            continue
        b64 = m.group(2)
        # urlsafe base64，补 padding
        pad = "=" * ((4 - len(b64) % 4) % 4)
        try:
            expect_digest = base64.urlsafe_b64decode(b64 + pad)
        except Exception:
            errors.append(f"RECORD sha256 b64 解码失败 {rel}")
            continue
        try:
            h = hashlib.sha256()
            with open(target, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
            got = h.digest()
        except Exception as e:
            errors.append(f"无法哈希 {rel}: {e}")
            continue
        if got != expect_digest:
            errors.append(f"RECORD sha256 不匹配 {rel}")

    return errors


def dep_install_complete(libs: Path, dep: "depspec.Dep") -> list[str]:
    """单包深度完整：版本 METADATA + RECORD 文件/size/hash。空列表=通过。

    半解压场景（有 dist-info 但缺 .pyd/.py）必须失败：优先走 RECORD，
    不能仅因顶层目录存在 + Name/Version 匹配就放行。
    """
    errors: list[str] = []
    dir_ok = any((libs / d).exists() for d in dep.dirs)

    info = _find_dist_info(libs, dep.pypi, dep.version)
    if info is None:
        any_info = _find_dist_info(libs, dep.pypi, None)
        if any_info is None:
            if dir_ok:
                errors.append(f"{dep.pypi} 已装但无 dist-info，无法确认版本=={dep.version}")
            elif dep.required:
                errors.append(f"缺少 {dep.pypi}=={dep.version}")
        else:
            meta = any_info / "METADATA"
            got = "?"
            if meta.is_file():
                m_ver = re.search(
                    r"^Version:\s*(.+)$",
                    meta.read_text(encoding="utf-8", errors="replace"),
                    re.M,
                )
                if m_ver:
                    got = m_ver.group(1).strip()
            errors.append(f"{dep.pypi} 版本 {got} != 钉死 {dep.version}")
            # 版本不对也尽量做 RECORD（半解压线索）
            rec_errs = verify_record_integrity(libs, any_info)
            for e in rec_errs:
                errors.append(f"{dep.pypi}: {e}")
        return errors

    if not dir_ok and dep.required:
        errors.append(f"缺少包文件/目录 {dep.pypi}（期望 {dep.dirs}）")

    # RECORD 深度：即使顶层 dir 缺失，有 dist-info 也要报缺文件
    rec_errs = verify_record_integrity(libs, info)
    for e in rec_errs:
        errors.append(f"{dep.pypi}: {e}")

    key = _dep_norm(dep.pypi)
    if key == "shapely" and dep.version in depspec.SHAPELY_EXCLUDED:
        errors.append(f"shapely=={dep.version} 被 rapidocr 元数据排除（!=2.0.4）")
    return errors


def verify_libs_versions(libs: Path | None = None, with_ocr: bool = True) -> list[str]:
    """校验 libs：版本 + RECORD 深度完整性（文件存在/size/sha256）。

    半解压（有 dist-info 缺 .pyd/.py）会失败。Shapely 不得为 2.0.4。
    """
    libs = libs or _libs_dir()
    errors: list[str] = []
    if not libs.is_dir():
        return ["libs 目录不存在"]

    for dep in depspec.deps_for(with_ocr=with_ocr, include_optional=False):
        errors.extend(dep_install_complete(libs, dep))
    return errors


# --------------------------------------------------------------------------- #
#  下载 / 安装
# --------------------------------------------------------------------------- #
def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def _http_download(url: str, on_bytes=None) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=300) as r:
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


def pypi_wheel_info(pkg: str, version: str) -> dict:
    """解析 win_amd64 / cp312（或 abi3 / py3-none-any）wheel：url/filename/sha256/size。"""
    api = f"https://pypi.org/pypi/{pkg}/{version}/json"
    data = json.loads(_http_get(api))
    files = data["urls"]
    cands = [f for f in files if f["filename"].endswith(".whl")]

    def score(fn: str) -> int:
        s = 0
        low = fn.lower()
        if "win_amd64" in low:
            s += 10
        elif "py3-none-any" in low or "py2.py3-none-any" in low:
            s += 6
        else:
            return -100
        if "cp312" in low:
            s += 5
        elif "abi3" in low:
            s += 3
        elif "py3-none-any" in low or "py2.py3-none-any" in low:
            s += 2
        if "cp311" in low or "cp310" in low or "cp39" in low:
            s -= 3
        if "win32" in low and "win_amd64" not in low:
            s -= 50
        return s

    ranked = sorted(cands, key=lambda f: score(f["filename"]), reverse=True)
    if not ranked or score(ranked[0]["filename"]) < 0:
        raise RuntimeError(f"未找到 {pkg}=={version} 的 win_amd64/cp312 wheel")
    best = ranked[0]
    fn = best["filename"].lower()
    if "win_amd64" not in fn and "none-any" not in fn:
        raise RuntimeError(f"wheel 架构不符: {best['filename']}")
    sha = (best.get("digests") or {}).get("sha256") or ""
    if not sha:
        raise RuntimeError(f"PyPI 未提供 {best['filename']} 的 sha256")
    return {
        "url": best["url"],
        "filename": best["filename"],
        "sha256": sha,
        "size": int(best.get("size") or 0),
    }


def pypi_wheel_url(pkg: str, version: str) -> str:
    """兼容旧接口：只返回 URL。"""
    return pypi_wheel_info(pkg, version)["url"]


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _zip_intact(path: Path | None = None, data: bytes | None = None) -> bool:
    """ZIP/wheel 完整性：能打开且 testzip 无坏成员。"""
    try:
        if data is not None:
            zf = zipfile.ZipFile(io.BytesIO(data))
        elif path is not None:
            zf = zipfile.ZipFile(path)
        else:
            return False
        with zf:
            bad = zf.testzip()
            if bad is not None:
                return False
            if not zf.namelist():
                return False
        return True
    except Exception:
        return False


def load_cached_artifact(
    cache_path: Path,
    *,
    expect_sha256: str,
    min_size: int = 1,
    kind: str = "zip",
) -> bytes | None:
    """读缓存：尺寸 + sha256 + ZIP 完整性。失败则删除缓存并返回 None。"""
    if not cache_path.is_file():
        return None
    try:
        sz = cache_path.stat().st_size
    except Exception:
        return None
    if sz < min_size:
        try:
            cache_path.unlink()
        except Exception:
            pass
        return None
    try:
        data = cache_path.read_bytes()
    except Exception:
        return None
    if expect_sha256:
        got = _sha256_bytes(data)
        if got.lower() != expect_sha256.lower():
            try:
                cache_path.unlink()
            except Exception:
                pass
            return None
    if kind in ("zip", "whl", "wheel") and not _zip_intact(data=data):
        try:
            cache_path.unlink()
        except Exception:
            pass
        return None
    return data


def save_cached_artifact(cache_path: Path, data: bytes, expect_sha256: str = "") -> None:
    if expect_sha256 and _sha256_bytes(data).lower() != expect_sha256.lower():
        raise RuntimeError(
            f"下载内容 sha256 不匹配: {cache_path.name} "
            f"got={_sha256_bytes(data)[:16]}… expect={expect_sha256[:16]}…"
        )
    if cache_path.suffix.lower() in (".zip", ".whl") or "whl" in cache_path.name:
        if not _zip_intact(data=data):
            raise RuntimeError(f"下载内容不是完整 ZIP: {cache_path.name}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(cache_path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        cache_path.write_bytes(data)


def _strip_cuda(ct2_dir: Path, libs: Path | None = None):
    """去掉 ctranslate2 自带的 CUDA DLL（CPU 离线包不需要），并同步改写 RECORD。

    否则 RECORD 仍声明 cudnn/cublas 等文件，深度完整性校验会误报失败。
    """
    if not ct2_dir.is_dir():
        return
    removed: set[str] = set()
    for f in list(ct2_dir.iterdir()):
        low = f.name.lower()
        if any(k in low for k in ("cudnn", "cublas", "cudart", "cuda")):
            try:
                f.unlink()
                # RECORD 路径相对 libs 根
                removed.add(f"{ct2_dir.name}/{f.name}")
                removed.add(f.name)
            except Exception:
                pass
    if not removed:
        return
    # 改写 dist-info/RECORD，去掉已删条目，保持深验一致
    root = libs if libs is not None else ct2_dir.parent
    for info in root.glob("ctranslate2-*.dist-info"):
        record = info / "RECORD"
        if not record.is_file():
            continue
        try:
            text = record.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        out_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            raw = line.strip()
            if not raw:
                out_lines.append(line)
                continue
            # CSV 首列 path
            path = raw.split(",", 1)[0].strip().replace("\\", "/")
            base = path.rsplit("/", 1)[-1]
            if path in removed or base in removed or any(
                k in base.lower() for k in ("cudnn", "cublas", "cudart", "cuda")
            ):
                # 仅当文件确实已不在磁盘时跳过
                if not (root / path).is_file():
                    continue
            out_lines.append(line if line.endswith("\n") else line + "\n")
        try:
            record.write_text("".join(out_lines), encoding="utf-8")
        except Exception:
            pass


def _extract_argosmodel(data: bytes, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            low = n.lower()
            if "sentencepiece" in low and low.endswith(".model"):
                (dst / "sentencepiece.model").write_bytes(zf.read(n))
            elif "/model/" in ("/" + n) or n.startswith("model/"):
                idx = n.find("model/")
                sub = n[idx:]
                out = dst / sub
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(n))


def _extract_wheel(data: bytes, libs: Path) -> str:
    """解压 wheel 到 libs，返回文件名。"""
    libs.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # 记录主 dist-info 名
        names = zf.namelist()
        zf.extractall(libs)
        for n in names:
            if n.endswith(".dist-info/METADATA") or n.endswith(".dist-info\\METADATA"):
                return n.split("/")[0].split("\\")[0]
    return ""


def write_embed_pth(py_dir: Path):
    pth = None
    for f in py_dir.glob("python*._pth"):
        pth = f
        break
    if pth is None:
        pth = py_dir / "python312._pth"
    pth.write_text(
        "python312.zip\n.\n..\\libs\n..\nimport site\n",
        encoding="utf-8",
    )


def _clear_dir_contents(d: Path):
    if not d.is_dir():
        return
    for c in list(d.iterdir()):
        if c.is_dir():
            shutil.rmtree(c, ignore_errors=True)
        else:
            try:
                c.unlink()
            except Exception:
                pass


def install_embed_python(py_dir: Path, progress=None, cache_dir: Path | None = None) -> Path:
    """安装 Windows embeddable CPython 到 py_dir。

    完整校验：python.exe + python312.dll + python312.zip + 有效 _pth。
    半解压目录不会被当成成功；损坏缓存（截断 ZIP / 错 sha256）会删除并重下。
    """
    py_dir.mkdir(parents=True, exist_ok=True)
    if embed_python_complete(py_dir):
        write_embed_pth(py_dir)  # 确保 _pth 指向 libs
        if embed_python_complete(py_dir):
            return py_dir

    cache_dir = cache_dir or (base_dir() / ".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    zpath = cache_dir / f"python-{depspec.EMBED_PYTHON_VERSION}-embed-amd64.zip"
    expect = depspec.EMBED_PYTHON_SHA256

    data = load_cached_artifact(
        zpath,
        expect_sha256=expect,
        min_size=depspec.EMBED_PYTHON_MIN_SIZE,
        kind="zip",
    )
    if data is None:
        if progress:
            progress("下载外置 Python 运行时", 0, 0)
        data = _http_download(
            depspec.EMBED_PYTHON_URL,
            lambda d, t: progress and progress("下载外置 Python 运行时", d, t),
        )
        save_cached_artifact(zpath, data, expect_sha256=expect)

    # 半解压/缺文件：清空后重解
    _clear_dir_contents(py_dir)
    if not _zip_intact(data=data):
        try:
            zpath.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError("embed Python ZIP 损坏，已删除缓存，请重试下载")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(py_dir)
    write_embed_pth(py_dir)
    if not embed_python_complete(py_dir):
        raise RuntimeError(
            "外置 Python 解压后仍不完整（需 python.exe / python312.dll / "
            "python312.zip / 有效 ._pth）"
        )
    return py_dir


def install_worker_bundle(root: Path, progress=None, src_app: Path | None = None):
    """把 worker_main.py 与 app 纯 Python 模块写入 translate_data。

    缺任一清单模块直接失败，禁止静默跳过。
    """
    if progress:
        progress("安装翻译 worker 脚本", 0, 1)

    if src_app is None:
        if getattr(sys, "frozen", False):
            src_app = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
        else:
            src_app = base_dir() / "app"

    if not src_app.is_dir():
        raise RuntimeError(f"找不到 app 源目录：{src_app}")

    missing_src = [n for n in depspec.WORKER_APP_MODULES if not (src_app / n).is_file()]
    if missing_src:
        raise RuntimeError(
            "install_worker_bundle 源模块缺失（拒绝静默跳过）："
            + ", ".join(missing_src)
        )

    dst_app = root / "app"
    dst_app.mkdir(parents=True, exist_ok=True)
    (dst_app / "__init__.py").write_text("# translate runtime\n", encoding="utf-8")

    for name in depspec.WORKER_APP_MODULES:
        src = src_app / name
        shutil.copy2(src, dst_app / name)
        if not (dst_app / name).is_file() or (dst_app / name).stat().st_size <= 0:
            raise RuntimeError(f"复制失败或空文件：app/{name}")

    worker_src = src_app / "translate_data_worker.py"
    shutil.copy2(worker_src, root / depspec.WORKER_ENTRY)
    if not (root / depspec.WORKER_ENTRY).is_file():
        raise RuntimeError(f"无法写入 {depspec.WORKER_ENTRY}")

    miss = worker_bundle_complete(root)
    if miss:
        raise RuntimeError("worker 模块安装后仍缺：" + ", ".join(miss))

    if progress:
        progress("安装翻译 worker 脚本", 1, 1)


def write_versions_file(root: Path, with_ocr: bool = True, extra: dict | None = None):
    payload = {
        "manifest_version": depspec.MANIFEST_VERSION,
        "embed_python": depspec.EMBED_PYTHON_VERSION,
        "packages": depspec.expected_versions(with_ocr=with_ocr),
        "with_ocr": with_ocr,
    }
    if extra:
        payload.update(extra)
    (root / "VERSIONS.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def mark_installing(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    ready = root / depspec.READY_MARKER
    if ready.is_file():
        try:
            ready.unlink()
        except Exception:
            pass
    (root / depspec.INSTALLING_MARKER).write_text("1\n", encoding="utf-8")


def mark_ready(root: Path):
    inst = root / depspec.INSTALLING_MARKER
    if inst.is_file():
        try:
            inst.unlink()
        except Exception:
            pass
    (root / depspec.READY_MARKER).write_text("ok\n", encoding="utf-8")


def clear_installing(root: Path):
    inst = root / depspec.INSTALLING_MARKER
    if inst.is_file():
        try:
            inst.unlink()
        except Exception:
            pass


def _purge_dep_from_libs(libs: Path, dep: "depspec.Dep"):
    for d in dep.dirs:
        p = libs / d
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            try:
                p.unlink()
            except Exception:
                pass
    key = _dep_norm(dep.pypi)
    for info in list(libs.glob("*.dist-info")):
        try:
            text = (
                (info / "METADATA").read_text(encoding="utf-8", errors="replace")
                if (info / "METADATA").is_file()
                else ""
            )
        except Exception:
            text = ""
        m_name = re.search(r"^Name:\s*(.+)$", text, re.M)
        if m_name and _norm_pkg(m_name.group(1)) == key:
            shutil.rmtree(info, ignore_errors=True)


def install_pinned_wheels(
    libs: Path,
    with_ocr: bool = True,
    progress=None,
    cache_dir: Path | None = None,
    include_optional: bool = True,
) -> dict[str, str]:
    """按清单下载并解压 wheel 到 libs。

    fast path 必须通过 RECORD 深度完整性；半解压（有 dist-info 缺文件）会 purge 后重装。
    缓存必须通过 sha256 + ZIP 完整性。
    """
    libs.mkdir(parents=True, exist_ok=True)
    cache_dir = cache_dir or (base_dir() / ".cache" / "wheels")
    cache_dir.mkdir(parents=True, exist_ok=True)
    installed: dict[str, str] = {}

    for dep in depspec.deps_for(with_ocr=with_ocr, include_optional=include_optional):
        # 深度完整才跳过
        deep_errs = dep_install_complete(libs, dep)
        if not deep_errs:
            installed[dep.pypi] = dep.version
            continue

        # 不完整：purge 后重装
        _purge_dep_from_libs(libs, dep)

        label = f"下载 {dep.pypi}=={dep.version}"
        if progress:
            progress(label, 0, 0)
        try:
            info = pypi_wheel_info(dep.pypi, dep.version)
        except Exception as e:
            if not dep.required:
                if progress:
                    progress(f"跳过可选 {dep.pypi}: {e}", 0, 0)
                continue
            raise

        if dep.sha256 and info["sha256"].lower() != dep.sha256.lower():
            raise RuntimeError(
                f"{dep.pypi}=={dep.version} wheel digest 与清单不符：\n"
                f"  PyPI  {info['filename']} {info['sha256']}\n"
                f"  清单  {dep.sha256}\n"
                f"  请更新 translate_deps.py"
            )

        wheel_name = info["filename"]
        low = wheel_name.lower()
        if "win_amd64" not in low and "none-any" not in low:
            raise RuntimeError(f"拒绝非 win64 wheel: {wheel_name}")
        if "cp3" in low and "cp312" not in low and "abi3" not in low and "none-any" not in low:
            raise RuntimeError(f"拒绝非 cp312 wheel: {wheel_name}")

        cpath = cache_dir / wheel_name
        data = load_cached_artifact(
            cpath,
            expect_sha256=dep.sha256 or info["sha256"],
            min_size=max(100, (info.get("size") or 0) // 10) if info.get("size") else 1000,
            kind="whl",
        )
        if data is None:
            data = _http_download(
                info["url"], lambda d, t, lb=label: progress and progress(lb, d, t)
            )
            save_cached_artifact(
                cpath, data, expect_sha256=dep.sha256 or info["sha256"]
            )

        _extract_wheel(data, libs)
        if dep.pypi == "ctranslate2":
            _strip_cuda(libs / "ctranslate2", libs=libs)

        # 解压后再深验
        post = dep_install_complete(libs, dep)
        if post:
            raise RuntimeError(
                f"{dep.pypi}=={dep.version} 解压后仍不完整：\n" + "\n".join(post)
            )
        installed[dep.pypi] = dep.version

    # 仅清理误落的完整 wheel 归档；保留 size=0 的 delvewheel 占位 .whl
    # （numpy 等 RECORD 会声明该空文件，删掉会导致深验失败）
    for junk in libs.glob("*.whl"):
        try:
            if junk.stat().st_size > 0:
                junk.unlink()
        except Exception:
            pass

    return installed


def download_all(progress=None, with_ocr: bool = True):
    """下载离线组件到 translate_data/（与全离线版同布局）。

    含：外置 CPython + 钉死版本 libs + models + worker。
    安装过程写 .installing；成功才写 .runtime_ready。中断后 is_ready=False。
    ready 前强校验：embed 完整、worker 模块齐全、依赖版本。
    """
    root = _writable_root()
    root.mkdir(parents=True, exist_ok=True)
    libs = root / "libs"
    models = root / "models"
    py_dir = root / "py"
    libs.mkdir(parents=True, exist_ok=True)
    models.mkdir(parents=True, exist_ok=True)

    mark_installing(root)
    try:
        install_embed_python(py_dir, progress=progress)
        if not embed_python_complete(py_dir):
            raise RuntimeError("embed Python 安装后仍不完整")

        install_worker_bundle(root, progress=progress)
        miss = worker_bundle_complete(root)
        if miss:
            raise RuntimeError("worker 模块缺失：" + ", ".join(miss))

        install_pinned_wheels(libs, with_ocr=with_ocr, progress=progress)

        for name, url in _MODEL_URLS.items():
            dst = models / name
            if (dst / "model").is_dir() and (dst / "sentencepiece.model").is_file():
                continue
            if progress:
                progress(f"下载模型 {name}", 0, 0)
            data = _http_download(
                url, lambda d, t, n=name: progress and progress(f"下载模型 {n}", d, t)
            )
            _extract_argosmodel(data, dst)

        verrs = verify_libs_versions(libs, with_ocr=with_ocr)
        if verrs:
            raise RuntimeError("依赖版本校验失败：\n" + "\n".join(verrs))

        if not embed_python_complete(py_dir):
            raise RuntimeError("ready 前 embed 校验失败")
        miss = worker_bundle_complete(root)
        if miss:
            raise RuntimeError("ready 前 worker 校验失败：" + ", ".join(miss))

        write_versions_file(root, with_ocr=with_ocr)
        mark_ready(root)
    except Exception:
        clear_installing(root)
        raise

    ensure_runtime_path()
    if not is_ready():
        raise RuntimeError(
            "组件已下载但仍不可用："
            + (missing_summary() or "未知")
            + "\n请检查磁盘/权限，或改用「全离线版」完整解压。"
        )


# 兼容旧名
_WHEELS = [(d.pypi, d.version) for d in depspec.CORE_DEPS]
_OCR_WHEELS = [(d.pypi, d.version) for d in depspec.OCR_DEPS]
_pypi_wheel_url = pypi_wheel_url
_install_embed_python = lambda progress=None: install_embed_python(
    _writable_root() / "py", progress=progress
)
_install_worker_bundle = lambda progress=None: install_worker_bundle(
    _writable_root(), progress=progress
)
