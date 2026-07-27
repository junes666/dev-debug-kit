"""离线中英翻译引擎（纯逻辑，无 Qt）。

依赖 ctranslate2 + sentencepiece，从 translate_data/libs 或开发 venv 加载。
全程离线；原生扩展**每个进程只 import 一次**（禁止 del sys.modules 后再载，
否则 pybind11 会报 cannot load module more than once per process）。
"""
from __future__ import annotations

import os
import re
import sys
import threading

from app import translate_component as tc

_CJK = r"一-鿿㐀-䶿　-〿＀-￯"

_CT2 = None
_SPM = None
_DEPS_LOCK = threading.Lock()
_DEPS_ERROR = None  # 首次失败缓存，避免反复打崩点


def is_cjk_char(ch):
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"


def detect_lang(text):
    for ch in text:
        if is_cjk_char(ch):
            return "zh"
    return "en"


def _detok(tokens):
    return "".join(tokens).replace("▁", " ").strip()


def _collapse_cjk_spaces(s):
    return re.sub(r"(?<=[%s])\s+(?=[%s])" % (_CJK, _CJK), "", s).strip()


def _normalize_cjk_punct(s):
    table = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：",
             ";": "；", "(": "（", ")": "）"}
    return "".join(table.get(c, c) for c in s)


def _split_sentences(line, is_zh):
    if is_zh:
        parts = re.split(r"(?<=[。！？!?；;])", line)
    else:
        parts = re.split(r"(?<=[.!?;])\s+", line)
    return [p.strip() for p in parts if p.strip()]


def _prep_env():
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("KMP_AFFINITY", "disabled")
    os.environ.setdefault("KMP_WARNINGS", "0")


def _load_deps():
    """惰性加载离线原生库：路径先就绪，然后整进程只 import 一次。"""
    global _CT2, _SPM, _DEPS_ERROR
    with _DEPS_LOCK:
        if _CT2 is not None and _SPM is not None:
            return _CT2, _SPM
        if _DEPS_ERROR is not None:
            raise RuntimeError(_DEPS_ERROR)

        _prep_env()

        # 必须在任何 import 之前固定搜索路径（不可事后 purge 重载）
        if not tc.deps_available():
            _DEPS_ERROR = (
                "离线翻译运行库未就绪。\n"
                "请使用「全离线版」（自带 translate_data），\n"
                "或点「下载离线组件」安装到程序目录（仅需联网一次，之后离线）。"
            )
            raise RuntimeError(_DEPS_ERROR)

        tc.ensure_runtime_path()

        # 若本进程已成功导入过，直接复用（勿再 import / 勿 purge）
        if "sentencepiece" in sys.modules and "ctranslate2" in sys.modules:
            _SPM = sys.modules["sentencepiece"]
            _CT2 = sys.modules["ctranslate2"]
            return _CT2, _SPM

        try:
            # numpy 可选：稳住 OpenMP；失败不阻断
            try:
                import numpy  # noqa: F401
            except Exception:
                pass
            # 顺序：先 sentencepiece，再 ctranslate2（ct2 可能间接依赖 spm）
            import sentencepiece as spm
            import ctranslate2 as ct2
        except Exception as e:
            _DEPS_ERROR = (
                "加载离线翻译库失败（sentencepiece/ctranslate2）。\n"
                "请确认 translate_data/libs 完整，或重新解压全离线版。\n"
                f"详情：{e}"
            )
            raise RuntimeError(_DEPS_ERROR) from e

        _CT2, _SPM = ct2, spm
        return _CT2, _SPM


class OCR:
    """离线图片 OCR。与翻译共用进程时不 purge 已加载的 numpy 等。"""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()
        self._error = None

    def _get(self):
        with self._lock:
            if self._engine is not None:
                return self._engine
            if self._error is not None:
                raise RuntimeError(self._error)
            if not tc.ocr_available():
                self._error = (
                    "离线 OCR 未就绪。全离线版应自带 OCR 库；"
                    "精简版请下载离线组件，或改用全离线包。"
                )
                raise RuntimeError(self._error)
            _prep_env()
            # 只补路径，绝不 purge（否则会 cannot load module more than once）
            tc.ensure_runtime_path()
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR()
            except Exception as e:
                self._error = f"加载离线 OCR 失败：{e}"
                raise RuntimeError(self._error) from e
            return self._engine

    def recognize(self, image_path: str) -> str:
        if not image_path or not os.path.isfile(image_path):
            raise FileNotFoundError(f"OCR 图片不存在：{image_path!r}")
        import numpy as np
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        res, _ = self._get()(np.array(img))
        if not res:
            return ""
        items = []
        for box, txt, score in res:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append((min(ys), min(xs), txt))
        items.sort(key=lambda it: (round(it[0] / 12.0), it[1]))
        return "\n".join(it[2] for it in items)


class Engine:
    """离线神经翻译（Argos 中英模型 + ctranslate2）。"""

    DIRS = {("zh", "en"): "zh_en", ("en", "zh"): "en_zh"}

    def __init__(self, models_root: str | None = None):
        self._cache = {}
        self._lock = threading.Lock()
        self.root = models_root or str(tc.models_root())

    def available(self):
        return [
            d for d in ("zh_en", "en_zh")
            if os.path.isdir(os.path.join(self.root, d, "model"))
            and os.path.isfile(os.path.join(self.root, d, "sentencepiece.model"))
        ]

    def _load_sp(self, spm, sp_path: str):
        sp = spm.SentencePieceProcessor()
        raw = open(sp_path, "rb").read()
        # 不同版本 LoadFromSerializedProto 返回 True/False 或 None
        loaded = False
        try:
            ret = sp.LoadFromSerializedProto(raw)
            loaded = (ret is True) or (ret is None and sp.GetPieceSize() > 0)
        except Exception:
            loaded = False
        if not loaded:
            ret = sp.Load(sp_path)
            if ret is False:
                raise RuntimeError(f"无法加载 sentencepiece：{sp_path}")
            # ret True/None 且能取到词表即成功
            try:
                if sp.GetPieceSize() <= 0:
                    raise RuntimeError(f"sentencepiece 词表为空：{sp_path}")
            except Exception:
                pass
        return sp

    def _load(self, name):
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            ct2, spm = _load_deps()
            base = os.path.join(self.root, name)
            model_dir = os.path.join(base, "model")
            sp_path = os.path.join(base, "sentencepiece.model")
            if not os.path.isdir(model_dir):
                raise FileNotFoundError(f"模型目录不存在：{model_dir}")
            if not os.path.isfile(sp_path):
                raise FileNotFoundError(f"分词模型不存在：{sp_path}")

            translator = ct2.Translator(model_dir, device="cpu", intra_threads=4)
            sp = self._load_sp(spm, sp_path)
            self._cache[name] = (translator, sp)
            return translator, sp

    def resolve(self, text, source="auto", target="auto"):
        f = source if source in ("zh", "en") else detect_lang(text)
        t = target if target in ("zh", "en") else ("en" if f == "zh" else "zh")
        if f == t:
            t = "en" if f == "zh" else "zh"
        return f, t

    def translate(self, text, source="auto", target="auto"):
        f, t = self.resolve(text, source, target)
        name = self.DIRS.get((f, t))
        if name is None:
            raise ValueError("暂不支持 %s -> %s" % (f, t))
        tr, sp = self._load(name)
        to_zh = (t == "zh")
        out_lines = []
        for line in text.split("\n"):
            if not line.strip():
                out_lines.append("")
                continue
            sents = _split_sentences(line, f == "zh")
            batch = [sp.encode(s, out_type=str) for s in sents]
            results = tr.translate_batch(batch, beam_size=4, max_batch_size=8)
            pieces = [_detok(r.hypotheses[0]) for r in results]
            joined = ("" if to_zh else " ").join(pieces)
            if to_zh:
                joined = _normalize_cjk_punct(_collapse_cjk_spaces(joined))
            out_lines.append(joined)
        return {"from": f, "to": t, "translated": "\n".join(out_lines)}
