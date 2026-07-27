"""translate_data 旁路入口：由「真正的 CPython」运行，不经过 PyInstaller 冻结进程。

主程序（开发调试.exe）只负责 UI；翻译/OCR 子进程启动：
  translate_data/py/python.exe translate_data/worker_main.py

这样 sentencepiece / ctranslate2 / onnxruntime 在正常 CPython 里加载，
避免冻结解释器下的 access violation。
"""
from __future__ import annotations

# 此文件在源码树中；打包时复制为 translate_data/worker_main.py
# 逻辑与 translate_worker.serve 相同，但启动时强制锁定 translate_data 根目录。

import json
import os
import sys
from pathlib import Path


def _boot():
    # worker_main.py 位于 translate_data/
    here = Path(__file__).resolve().parent
    # 若从源码 app/translate_data_worker.py 跑，here 是 app/；打包后是 translate_data/
    if (here / "libs").is_dir() and (here / "models").is_dir():
        root = here
    elif (here.parent / "translate_data" / "libs").is_dir():
        root = here.parent / "translate_data"
    else:
        root = here

    os.environ["DEVDEBUG_TRANSLATE_DATA"] = str(root)
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("KMP_AFFINITY", "disabled")
    os.environ.setdefault("KMP_WARNINGS", "0")

    libs = root / "libs"
    if libs.is_dir():
        lp = str(libs)
        if lp not in sys.path:
            sys.path.insert(0, lp)
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(lp)
            except Exception:
                pass
            try:
                for dirpath, dirnames, filenames in os.walk(lp):
                    dirnames[:] = [d for d in dirnames if d.lower() not in ("tests", "test", "__pycache__")]
                    if any(f.lower().endswith((".dll", ".pyd")) for f in filenames):
                        try:
                            os.add_dll_directory(dirpath)
                        except Exception:
                            pass
            except Exception:
                pass
        os.environ["PATH"] = lp + os.pathsep + os.environ.get("PATH", "")

    # 让 `import app.*` 能找到：优先 translate_data 内嵌的 app 副本，其次源码树
    bundled_app_parent = root  # root/app/...
    src_parent = root.parent  # 项目根（开发时）
    for p in (bundled_app_parent, src_parent):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def main():
    _boot()
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

    def emit(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    try:
        from app import translate_component as tc
        tc.ensure_runtime_path()
        from app.translate_engine import Engine, OCR, _load_deps

        if not tc.is_ready():
            emit({"ready": False, "error": f"离线组件不完整：{tc.missing_summary()}"})
            return
        # 预加载翻译库；OCR 仍惰性（避免无关崩溃）
        _load_deps()
        eng = Engine(str(tc.models_root()))
        ocr = OCR()
    except Exception as e:
        emit({"ready": False, "error": f"翻译引擎初始化失败：{e}"})
        return

    emit({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            action = req.get("action", "translate")
            if action == "ocr":
                path = req.get("path") or ""
                if not path:
                    raise ValueError("OCR 请求缺少 path")
                text = ocr.recognize(path)
                emit({"ok": True, "text": text})
            else:
                r = eng.translate(
                    req.get("text", ""),
                    req.get("source", "auto"),
                    req.get("target", "auto"),
                )
                emit({"ok": True, "text": r["translated"]})
        except Exception as e:  # noqa: BLE001
            emit({"ok": False, "error": str(e) or repr(e)})


if __name__ == "__main__":
    main()
