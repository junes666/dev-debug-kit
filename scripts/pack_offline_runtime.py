#!/usr/bin/env python3
"""装配全离线 translate_data：真 CPython + 钉死版本 libs + models + worker。

依赖版本唯一来源：app.translate_deps（与精简版 download_all 共用）。
**禁止**直接复制 Wine site-packages 里的任意最新 numpy/onnxruntime。

用法:
  python scripts/pack_offline_runtime.py dist/开发调试 --models models
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 保证能 import app.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import translate_component as tc  # noqa: E402
from app import translate_deps as depspec  # noqa: E402


def _p(*args):
    try:
        print(*args, flush=True)
    except UnicodeEncodeError:
        print(*(str(a).encode("ascii", "replace").decode("ascii") for a in args), flush=True)


def pack(app_dir: Path, models_src: Path, with_ocr: bool = True):
    td = app_dir / "translate_data"
    libs = td / "libs"
    models = td / "models"
    py_dir = td / "py"
    cache = ROOT / ".cache"

    td.mkdir(parents=True, exist_ok=True)
    tc.mark_installing(td)

    try:
        _p("1) embed CPython", depspec.EMBED_PYTHON_VERSION)
        tc.install_embed_python(py_dir, cache_dir=cache)

        _p("2) pinned wheels ->", libs)
        installed = tc.install_pinned_wheels(
            libs,
            with_ocr=with_ocr,
            cache_dir=cache / "wheels",
            include_optional=True,
        )
        for k, v in sorted(installed.items()):
            _p("  ", f"{k}=={v}")

        verrs = tc.verify_libs_versions(libs, with_ocr=with_ocr)
        if verrs:
            _p("VERSION ERRORS:")
            for e in verrs:
                _p("  -", e)
            raise SystemExit(1)

        _p("3) models")
        models.mkdir(parents=True, exist_ok=True)
        for pair in ("zh_en", "en_zh"):
            src = models_src / pair
            if not src.is_dir():
                raise SystemExit(f"missing model {src}")
            dst = models / pair
            if dst.exists():
                import shutil
                shutil.rmtree(dst)
            import shutil
            shutil.copytree(src, dst)
            _p("  model", pair, "OK")

        _p("4) worker bundle")
        tc.install_worker_bundle(td, src_app=ROOT / "app")
        miss = tc.worker_bundle_complete(td)
        if miss:
            raise SystemExit("worker bundle incomplete: " + ", ".join(miss))
        if not tc.embed_python_complete(py_dir):
            raise SystemExit("embed python incomplete after install")

        tc.write_versions_file(
            td,
            with_ocr=with_ocr,
            extra={"source": "pack_offline_runtime", "packages_installed": installed},
        )
        tc.mark_ready(td)
    except Exception:
        tc.clear_installing(td)
        raise

    # 终检
    need = [
        py_dir / "python.exe",
        td / "worker_main.py",
        td / "app" / "translate_engine.py",
        td / "app" / "translate_component.py",
        td / "app" / "translate_deps.py",
        libs / "ctranslate2",
        libs / "sentencepiece",
        libs / "numpy",
        models / "zh_en" / "model",
        models / "zh_en" / "sentencepiece.model",
        models / "en_zh" / "model",
        models / "en_zh" / "sentencepiece.model",
        td / depspec.READY_MARKER,
        td / "VERSIONS.json",
    ]
    if with_ocr:
        need += [
            libs / "onnxruntime",
            libs / "rapidocr_onnxruntime",
            libs / "cv2",
            libs / "flatbuffers",
            libs / "packaging",
            libs / "google",
        ]
    bad = [str(p) for p in need if not p.exists()]
    if bad:
        _p("INCOMPLETE:", bad)
        sys.exit(1)

    # 打印 VERSIONS
    ver = json.loads((td / "VERSIONS.json").read_text(encoding="utf-8"))
    _p("VERSIONS.json packages:", json.dumps(ver.get("packages", {}), ensure_ascii=True))
    _p("translate_data ready (pinned external CPython worker)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir", type=Path)
    ap.add_argument("--models", default=None)
    ap.add_argument("--no-ocr", action="store_true")
    # 兼容旧参数（忽略）
    ap.add_argument("--site", default=None, help="(ignored) 不再从 site-packages 拷贝")
    ap.add_argument("--win-python", default=None, help="(ignored)")
    args = ap.parse_args()
    app_dir = args.app_dir.resolve()
    if not app_dir.is_dir():
        raise SystemExit(f"app_dir missing: {app_dir}")
    models = Path(args.models) if args.models else ROOT / "models"
    if args.site:
        _p("NOTE: --site ignored; using pinned wheels from translate_deps")
    pack(app_dir, models, with_ocr=not args.no_ocr)


if __name__ == "__main__":
    main()
