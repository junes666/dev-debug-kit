"""翻译子进程入口：隔离 ctranslate2 原生崩溃，主程序永不闪退。

协议：stdin 每行一个 JSON 请求 {text, source, target}；stdout 每行一个 JSON 响应
{ok, text} 或 {ok:false, error}。启动后先输出 {"ready":true}。
原生崩溃由 faulthandler 打到 stderr（主程序会捕获并展示，便于定位）。

由 main.py 在收到命令行参数 --translate-worker 时调用 serve()。
"""
from __future__ import annotations

import json
import os
import sys


def serve():
    try:
        import faulthandler
        faulthandler.enable()   # 原生段错误时向 stderr 打印 C 调用栈
    except Exception:
        pass

    # 精简版：把下载到外置目录的组件加入搜索路径
    try:
        from app import translate_component as tc
        tc._add_libs_to_path()
        models_root = str(tc.models_root())
    except Exception:
        models_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")

    try:
        from app.translate_engine import Engine
        eng = Engine(models_root)
    except Exception as e:  # 引擎/依赖导入失败也别静默崩，回报错误
        _emit({"ready": False, "error": f"翻译引擎初始化失败：{e}"})
        return

    _emit({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            r = eng.translate(req.get("text", ""), req.get("source", "auto"), req.get("target", "auto"))
            _emit({"ok": True, "text": r["translated"]})
        except Exception as e:  # noqa: BLE001
            _emit({"ok": False, "error": str(e) or repr(e)})


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
