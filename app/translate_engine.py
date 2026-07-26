"""离线中英翻译引擎（纯逻辑，无 Qt 依赖）。

移植自 offline-translator-web/core.py 的 Engine（去掉 OCR）。ctranslate2 / sentencepiece
惰性导入（原生库，打包后在部分 Windows 上加载可能硬崩，故绝不在模块顶层 import）。

本模块被两处复用：
  - 主程序（translate_tool）用于探测/直接调用；
  - 独立子进程（translate_worker）里跑，隔离原生崩溃，主程序永不闪退。
"""
from __future__ import annotations

import os
import re
import threading

# 关键环境变量：必须在加载 ct2/numpy 的 OpenMP 之前设置
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("KMP_AFFINITY", "disabled")
os.environ.setdefault("KMP_WARNINGS", "0")

_CJK = r"一-鿿㐀-䶿　-〿＀-￯"


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


_CT2 = None
_SPM = None


def _load_deps():
    """惰性导入原生依赖（可能触发 DLL 加载/崩溃，只在真正翻译时调用）。"""
    global _CT2, _SPM
    if _CT2 is None or _SPM is None:
        import ctranslate2 as _c
        import sentencepiece as _s
        _CT2, _SPM = _c, _s
    return _CT2, _SPM


class OCR:
    """图片文字识别（rapidocr_onnxruntime）。惰性加载，供图片翻译用。"""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._engine is None:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR()
            return self._engine

    def recognize(self, image_path: str) -> str:
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
    DIRS = {("zh", "en"): "zh_en", ("en", "zh"): "en_zh"}

    def __init__(self, models_root: str):
        self._cache = {}
        self._lock = threading.Lock()
        self.root = models_root

    def available(self):
        return [d for d in ("zh_en", "en_zh")
                if os.path.isdir(os.path.join(self.root, d, "model"))]

    def _load(self, name):
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            ct2, spm = _load_deps()
            base = os.path.join(self.root, name)
            t = ct2.Translator(os.path.join(base, "model"), device="cpu", intra_threads=4)
            # 先用 LoadFromSerializedProto(bytes)（Python 读文件，对非 ASCII 路径安全）；
            # 若该版本 sentencepiece 有 pybind11 兼容问题则回退 Load(路径)。
            sp = spm.SentencePieceProcessor()
            sp_path = os.path.join(base, "sentencepiece.model")
            try:
                with open(sp_path, "rb") as f:
                    sp.LoadFromSerializedProto(f.read())
            except Exception:
                sp.Load(sp_path)
            self._cache[name] = (t, sp)
            return t, sp

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
