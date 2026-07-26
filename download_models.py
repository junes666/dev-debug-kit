# -*- coding: utf-8 -*-
"""
下载中英翻译模型到 ./models/（只需运行一次，需要联网；下载后即可完全离线使用）。
OCR 模型随 rapidocr_onnxruntime 包自带，无需另外下载。

用法:  python download_models.py
"""
import io
import os
import shutil
import zipfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")

# (目标目录名, .argosmodel 下载地址)
PAIRS = [
    ("zh_en", "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel"),
    ("en_zh", "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"),
]


def fetch(url):
    print("  下载:", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    os.makedirs(MODELS, exist_ok=True)
    for name, url in PAIRS:
        dst = os.path.join(MODELS, name)
        if os.path.isdir(os.path.join(dst, "model")):
            print("[跳过] 已存在:", name)
            continue
        print("[处理]", name)
        data = fetch(url)
        zf = zipfile.ZipFile(io.BytesIO(data))
        # .argosmodel 内是 translate-xx_yy-1_9/{model, sentencepiece.model, ...}
        top = zf.namelist()[0].split("/")[0]
        tmp = os.path.join(MODELS, "_tmp_" + name)
        shutil.rmtree(tmp, ignore_errors=True)
        zf.extractall(tmp)
        src = os.path.join(tmp, top)
        os.makedirs(dst, exist_ok=True)
        shutil.copytree(os.path.join(src, "model"), os.path.join(dst, "model"))
        shutil.copy(os.path.join(src, "sentencepiece.model"),
                    os.path.join(dst, "sentencepiece.model"))
        shutil.rmtree(tmp, ignore_errors=True)
        print("  完成:", dst)
    print("\n全部完成！现在可以运行:  python translator.py")


if __name__ == "__main__":
    main()
