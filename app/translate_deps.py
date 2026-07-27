"""翻译/OCR 离线依赖的唯一版本清单（全离线 pack 与精简版 download 共用）。

仅 pin win_amd64 / cp312（或 abi3 / py3-none-any）组合，禁止吞用本机
site-packages 的任意最新版（numpy 2.x 在部分 Wine 上会因 crealf 崩；
真实 Windows 上 PyInstaller 冻结加载原生库会 AV——故外置 CPython）。

元数据约束（须满足，见清单一致性测试）：
- rapidocr_onnxruntime==1.3.24:
    pyclipper>=1.2.0, opencv-python>=4.5.1.48, numpy<2.0.0,>=1.19.5,
    six>=1.15.0, Shapely!=2.0.4,>=1.7.1, PyYAML, Pillow, onnxruntime>=1.7.0
- onnxruntime==1.17.3:
    coloredlogs, flatbuffers, numpy>=1.21.6, packaging, protobuf, sympy
- sympy==1.12 → mpmath>=0.19
- coloredlogs==15.0.1 → humanfriendly>=9.1
"""
from __future__ import annotations

from typing import NamedTuple

# 外置 CPython（embeddable）
EMBED_PYTHON_VERSION = "3.12.10"
EMBED_PYTHON_URL = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
)
# 官方包 SHA256（缓存复用前必须校验；截断 ZIP 不得复用）
EMBED_PYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
EMBED_PYTHON_MIN_SIZE = 10_000_000  # ~11MB；仅作粗筛，真正靠 sha256+zip

# 标记文件：仅完整安装成功后写入；半成品不得 is_ready
INSTALLING_MARKER = ".installing"
READY_MARKER = ".runtime_ready"

# worker 纯 Python 模块（缺任一不得 ready）
WORKER_APP_MODULES: tuple[str, ...] = (
    "translate_engine.py",
    "translate_component.py",
    "translate_deps.py",
    "translate_worker.py",
    "translate_data_worker.py",
    "resources.py",
)
WORKER_ENTRY = "worker_main.py"


class Dep(NamedTuple):
    pypi: str
    version: str
    dirs: tuple[str, ...]  # 解压后 libs 下应出现的目录
    sha256: str  # 选定 win_amd64/cp312（或 pure）wheel 的官方 digest
    imports: tuple[str, ...] = ()  # 运行时 import 探针（比目录更严）
    required: bool = True
    for_ocr: bool = False


# 核心翻译栈（必须）
CORE_DEPS: tuple[Dep, ...] = (
    Dep(
        "numpy", "1.26.4", ("numpy", "numpy.libs"),
        "08beddf13648eb95f8d867350f6a018a4be2e5ad54c8d8caed89ebca558b2818",
        ("numpy",),
    ),
    Dep(
        "ctranslate2", "4.8.1", ("ctranslate2",),
        "49f96e861b57301f0b76a082109bde2cac8204a6b4fedc870883008271e82251",
        ("ctranslate2",),
    ),
    Dep(
        "sentencepiece", "0.2.0", ("sentencepiece",),
        "7a673a72aab81fef5ebe755c6e0cc60087d1f3a4700835d40537183c1703a45f",
        ("sentencepiece",),
    ),
    Dep(
        "PyYAML", "6.0.2", ("yaml",),
        "7e7401d0de89a9a855c839bc697c079a4af81cf878373abd7dc625847d25cbd8",
        ("yaml",),
    ),
)

# OCR 栈 + 传递依赖（全部 required；与 PyPI requires_dist 对齐）
# 注意：Shapely!=2.0.4 → 钉 2.0.3（非 2.0.4）
OCR_DEPS: tuple[Dep, ...] = (
    Dep(
        "onnxruntime", "1.17.3", ("onnxruntime",),
        "58672cf20293a1b8a277a5c6c55383359fcdf6119b2f14df6ce3b140f5001c39",
        ("onnxruntime",), for_ocr=True,
    ),
    Dep(
        "coloredlogs", "15.0.1", ("coloredlogs",),
        "612ee75c546f53e92e70049c9dbfcc18c935a2b9a53b66085ce9ef6a6e5c0934",
        ("coloredlogs",), for_ocr=True,
    ),
    Dep(
        "humanfriendly", "10.0", ("humanfriendly",),
        "1697e1a8a8f550fd43c2865cd84542fc175a61dcb779b6fee18cf6b6ccba1477",
        ("humanfriendly",), for_ocr=True,
    ),
    Dep(
        "flatbuffers", "24.3.25", ("flatbuffers",),
        "8dbdec58f935f3765e4f7f3cf635ac3a77f83568138d6a2311f524ec96364812",
        ("flatbuffers",), for_ocr=True,
    ),
    Dep(
        "packaging", "24.0", ("packaging",),
        "2ddfb553fdf02fb784c234c7ba6ccc288296ceabec964ad2eae3777778130bc5",
        ("packaging",), for_ocr=True,
    ),
    Dep(
        "protobuf", "4.25.3", ("google",),
        "209ba4cc916bab46f64e56b85b090607a676f66b473e6b762e6f1d9d591eb2e8",
        ("google.protobuf",), for_ocr=True,
    ),
    Dep(
        "sympy", "1.12", ("sympy",),
        "c3588cd4295d0c0f603d0f2ae780587e64e2efeedb3521e46b9bb1d08d184fa5",
        ("sympy",), for_ocr=True,
    ),
    Dep(
        "mpmath", "1.3.0", ("mpmath",),
        "a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c",
        ("mpmath",), for_ocr=True,
    ),
    Dep(
        "opencv-python-headless", "4.9.0.80", ("cv2",),
        "a8056c2cb37cd65dfcdf4153ca16f7362afcf3a50d600d6bb69c660fc61ee29c",
        ("cv2",), for_ocr=True,
    ),
    Dep(
        "pyclipper", "1.3.0.post5", ("pyclipper",),
        "2ce6e0a6ab32182c26537965cf521822cd11a28a7ffcef48635a94c6ca8559ef",
        ("pyclipper",), for_ocr=True,
    ),
    Dep(
        "shapely", "2.0.3", ("shapely", "shapely.libs", "Shapely.libs"),
        "6f555fe3304a1f40398977789bc4fe3c28a11173196df9ece1e15c5bc75a48db",
        ("shapely",), for_ocr=True,
    ),
    Dep(
        "Pillow", "10.3.0", ("PIL", "pillow.libs"),
        "412444afb8c4c7a6cc11a47dade32982439925537e483be7c0ae0cf96c4f6a0b",
        ("PIL",), for_ocr=True,
    ),
    Dep(
        "six", "1.16.0", ("six.py", "six"),  # pure：可能是 six.py 文件
        "8abb2f1d86890a2dfb989f9a77cfcfd3e47c2a354b01111771326f8aa26e0254",
        ("six",), for_ocr=True,
    ),
    Dep(
        "rapidocr_onnxruntime", "1.3.24", ("rapidocr_onnxruntime",),
        "4282ff0b8db05ad2a53afc8d0ef2e7d879c53022fc61a4f8e84c58d737822cd2",
        ("rapidocr_onnxruntime",), for_ocr=True,
    ),
)

# 兼容旧名：不再有“可选 OCR 依赖”——onnxruntime 声明的全是硬依赖
OPTIONAL_OCR_DEPS: tuple[Dep, ...] = ()

ALL_DEPS: tuple[Dep, ...] = CORE_DEPS + OCR_DEPS

# 运行时 import 探针
IMPORT_PROBES_CORE = tuple(
    imp for d in CORE_DEPS for imp in d.imports
)
IMPORT_PROBES_OCR = tuple(
    imp for d in OCR_DEPS for imp in d.imports
)

# rapidocr 显式排除的版本（一致性测试用）
SHAPELY_EXCLUDED = frozenset({"2.0.4"})

MANIFEST_VERSION = "2"


def deps_for(with_ocr: bool = True, include_optional: bool = True) -> list[Dep]:
    """include_optional 保留参数兼容；当前 OCR 依赖全部 required。"""
    out = list(CORE_DEPS)
    if with_ocr:
        out.extend(OCR_DEPS)
        if include_optional:
            out.extend(OPTIONAL_OCR_DEPS)
    return out


def expected_versions(with_ocr: bool = True) -> dict[str, str]:
    return {d.pypi: d.version for d in deps_for(with_ocr, include_optional=False)}


def expected_digests(with_ocr: bool = True) -> dict[str, str]:
    """pypi 名 -> wheel sha256。"""
    return {d.pypi: d.sha256 for d in deps_for(with_ocr, include_optional=True) if d.sha256}


def dep_by_pypi(name: str) -> Dep | None:
    key = name.replace("-", "_").lower()
    for d in ALL_DEPS:
        if d.pypi.replace("-", "_").lower() == key:
            return d
    return None
