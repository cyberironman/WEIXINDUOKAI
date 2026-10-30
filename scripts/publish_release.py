# -*- coding: utf-8 -*-
"""用 GitHub API 发布 Release 并上传资产（绕过 git 传输通道）。

用法：
    python scripts/publish_release.py v0.1.1
    python scripts/publish_release.py v0.1.1 --notes-file CHANGELOG_SECTION.md
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "https://api.github.com"
OWNER_REPO = "cyberironman/WEIXINDUOKAI"

sys.path.insert(0, HERE)
from push_via_api import call, token          # noqa: E402


def upload(upload_url: str, filepath: str, tok: str):
    """上传一个资产文件。upload_url 是 release 返回的模板地址。"""
    name = os.path.basename(filepath)
    base = upload_url.split("{")[0]
    url = "%s?name=%s&label=%s" % (base, urllib.parse.quote(name),
                                   urllib.parse.quote(name))
    with open(filepath, "rb") as fh:
        data = fh.read()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    last = None
    for attempt in range(1, 5):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", ctype)
        req.add_header("Content-Length", str(len(data)))
        req.add_header("User-Agent", "WEIXINDUOKAI-uploader")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")[:400]
            if 400 <= exc.code < 500:
                raise RuntimeError("上传 %s 失败 HTTP %d：%s" % (name, exc.code, body))
            last = "HTTP %d %s" % (exc.code, body)
        except Exception as exc:                 # noqa: BLE001
            last = repr(exc)
        time.sleep(2 * attempt)
    raise RuntimeError("上传 %s 重试仍失败：%s" % (name, last))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", help="例如 v0.1.1")
    ap.add_argument("--notes-file", default="")
    ap.add_argument("--draft", action="store_true")
    args = ap.parse_args()

    tok = token()
    print("认证：%s" % call("GET", "/user", tok).get("login"))

    notes = ""
    if args.notes_file and os.path.isfile(args.notes_file):
        notes = open(args.notes_file, "r", encoding="utf-8").read()

    ver = args.tag.lstrip("v")
    body = notes or (
        "## 微信多开 WeChatMulti %s\n\n"
        "Windows 上让多个微信号同时在线的小工具。\n"
        "**完全使用原版微信客户端，不改任何二进制文件。**\n\n"
        "### 下载\n\n"
        "下载下面的 `WeChatMulti-%s-win64.zip`，解压到任意位置，"
        "双击 `微信多开安装器.exe` 即可。\n\n"
        "### 使用前请确认\n\n"
        "- 已经装好官方微信 4.x（4.1.15 及以后实测通过）\n"
        "- 安装目录选本地磁盘，**不要**放 OneDrive / 网络盘 / U 盘\n"
        "- 每个实例需要约 870 MB 空间\n" % (args.tag, ver)
    )

    print("\n创建 release %s …" % args.tag)
    try:
        rel = call("POST", "/repos/%s/releases" % OWNER_REPO, tok, {
            "tag_name": args.tag,
            "name": "微信多开 %s" % args.tag,
            "body": body,
            "draft": args.draft,
            "prerelease": False,
        })
        print("   release id = %s" % rel["id"])
    except RuntimeError as exc:
        if "already_exists" in str(exc):
            print("   该 tag 的 release 已存在，改为查现有：")
            rel = call("GET", "/repos/%s/releases/tags/%s" % (OWNER_REPO, args.tag), tok)
            print("   release id = %s" % rel["id"])
        else:
            raise

    dist = os.path.join(ROOT, "dist")
    files = sorted(
        os.path.join(dist, f) for f in os.listdir(dist)
        if args.tag.replace("v", "v") in f or f.startswith("WeChatMulti-%s" % ver)
    )
    if not files:
        print("!! dist 里没有匹配 %s 的文件" % args.tag)
        return 1

    existing = {a["name"] for a in rel.get("assets", [])}
    for f in files:
        name = os.path.basename(f)
        if name in existing:
            print("   跳过（已存在）：%s" % name)
            continue
        print("   上传 %s (%.1f MB) …" % (name, os.path.getsize(f) / 1048576.0))
        asset = upload(rel["upload_url"], f, tok)
        print("      → %s" % asset.get("browser_download_url", "?"))

    print("\n完成：%s" % rel.get("html_url", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
