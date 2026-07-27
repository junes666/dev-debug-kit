"""翻译子进程：跑离线引擎，与主 UI 进程隔离（原生库若仍异常不影响主程序）。

协议：stdin 每行 JSON 请求；stdout 每行 JSON 响应。启动后先 {"ready":true}。
"""
from __future__ import annotations

import json
import os
import sys


def serve():
    # 必须在任何 import 原生库之前
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("KMP_AFFINITY", "disabled")
    os.environ.setdefault("KMP_WARNINGS", "0")

    try:
        import faulthandler
        faulthandler.enable()
    except Exception:
        pass

    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 冻结进程禁止加载原生扩展（Windows access violation）
    if getattr(sys, "frozen", False):
        _emit({
            "ready": False,
            "error": (
                "禁止在冻结进程中加载翻译/OCR 原生库。\n"
                "请使用 translate_data/py/python.exe + worker_main.py（全离线版自带）。"
            ),
        })
        return

    try:
        from app import translate_component as tc
        # 路径只设一次，之后本进程内原生库只加载一次
        tc.ensure_runtime_path()
        from app.translate_engine import Engine, OCR, _load_deps
        # 启动时预加载翻译库：失败立刻 ready:false，避免首条请求才炸
        if tc.is_ready():
            try:
                _load_deps()
            except Exception as e:
                _emit({"ready": False, "error": str(e)})
                return
        eng = Engine(str(tc.models_root()))
        ocr = OCR()
    except Exception as e:
        _emit({"ready": False, "error": f"翻译引擎初始化失败：{e}"})
        return

    _emit({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            action = req.get("action", "translate")
            if action == "ocr":
                path = req.get("path", "") or ""
                if not path:
                    raise ValueError("OCR 请求缺少 path")
                text = ocr.recognize(path)
                _emit({"ok": True, "text": text})
            else:
                r = eng.translate(
                    req.get("text", ""),
                    req.get("source", "auto"),
                    req.get("target", "auto"),
                )
                _emit({"ok": True, "text": r["translated"]})
        except Exception as e:  # noqa: BLE001
            _emit({"ok": False, "error": str(e) or repr(e)})


def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
