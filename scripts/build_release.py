# -*- coding: utf-8 -*-
"""出一份可发布的发行包（zip + sha256）。

用法：
    python scripts\\build_release.py

产物：
    dist\\WeChatMulti-v<版本>-win64.zip
    dist\\WeChatMulti-v<版本>-win64.zip.sha256

包里装的是**绿色版**：解压即用，可放 U 盘。
安装器（``微信多开安装器.exe``）也在里面，用户先跑它选目录，
或者直接解压后跑 ``微信多开管家.exe``。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")

sys.path.insert(0, ROOT)
from wechatmulti import __version__          # noqa: E402

# 打包进 zip 的内容：exe + 文档
WANT_EXES = ["微信多开管家.exe", "微信多开安装器.exe", "wxguard.exe", "wxtag.exe"]
WANT_DOCS = ["README.md", "CHANGELOG.md", "LICENSE"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    tag = "v%s" % __version__
    name = "WeChatMulti-%s-win64" % tag
    os.makedirs(DIST, exist_ok=True)
    zip_path = os.path.join(DIST, name + ".zip")

    missing = [e for e in WANT_EXES if not os.path.isfile(os.path.join(DIST, e))]
    if missing:
        print("!! dist\\ 里缺少：%s" % "、".join(missing))
        print("   先跑 python scripts\\build_exe.py")
        return 1

    if os.path.exists(zip_path):
        os.remove(zip_path)

    print("打包 %s …" % name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for e in WANT_EXES:
            zf.write(os.path.join(DIST, e), "%s/%s" % (name, e))
        for d in WANT_DOCS:
            src = os.path.join(ROOT, d)
            if os.path.isfile(src):
                zf.write(src, "%s/%s" % (name, d))
        # 使用说明
        guide = os.path.join(ROOT, "docs", "使用指南.md")
        if os.path.isfile(guide):
            zf.write(guide, "%s/使用指南.md" % name)

    digest = sha256(zip_path)
    with open(zip_path + ".sha256", "w", encoding="utf-8") as fh:
        fh.write("%s  %s\n" % (digest, name + ".zip"))

    size = os.path.getsize(zip_path) / 1048576.0
    print("\n完成：")
    print("  %-52s %8.1f MB" % (zip_path, size))
    print("  sha256 = %s" % digest)
    print("\n下一步：把 zip 和 .sha256 一起传到 GitHub Releases。")
    print("  gh release create %s \"%s\" --title \"%s\"" % (tag, zip_path, tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
