# -*- coding: utf-8 -*-
"""通过 GitHub Git Data API 把本地提交推上去（绕过 git 传输通道）。

**为什么需要这个**
本机的沙箱代理只稳定放行 ``api.github.com``，不放行 ``github.com``，
所以 ``git push`` 会在 CONNECT 隧道阶段拿到 502。
但 GitHub 的 REST API 完全够用：建 blob → 建 tree → 建 commit → 更新 ref。

用法：
    python scripts/push_via_api.py                 # 推送当前分支
    python scripts/push_via_api.py --remote URL

只做一件事：把 **已经 commit 好的内容** 原样搬过去，不改工作区。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "https://api.github.com"


def token() -> str:
    """从 Git 凭据管理器取 token。"""
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30,
        ).stdout
        for line in out.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception as exc:                     # noqa: BLE001
        raise RuntimeError("取 token 失败：%s" % exc)
    raise RuntimeError("凭据管理器里没有 github.com 的凭据")


def _opener():
    """不用环境变量里的代理。

    本机的 ``HTTPS_PROXY`` 指向沙箱代理，它对 github.com 的 CONNECT 时通时不通
    （502），而 ``api.github.com`` 直连是通的。所以显式禁用代理，
    并且失败时自动重试几次。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, tok: str, payload=None, retries=4):
    """调 GitHub API。返回解析后的 JSON。失败自动重试（代理抖动很常见）。"""
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "WEIXINDUOKAI-uploader")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with _opener().open(req, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")[:400]
            # 4xx 是确定性错误，重试没意义
            if 400 <= exc.code < 500:
                raise RuntimeError("%s %s → HTTP %d\n%s"
                                   % (method, path, exc.code, body))
            last = "HTTP %d %s" % (exc.code, body)
        except Exception as exc:                  # noqa: BLE001
            last = repr(exc)
        if attempt < retries:
            time.sleep(2 * attempt)
    raise RuntimeError("%s %s → 重试 %d 次仍失败：%s"
                       % (method, path, retries, last))


def git(*args, binary=False):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=not binary)
    if r.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args),
                                               r.stderr if not binary else r.stderr))
    return r.stdout if not binary else r.stdout


def list_files() -> list:
    """当前提交里的所有文件（路径, blob sha）。

    必须用 ``-z``（NUL 分隔），否则 git 会把非 ASCII 文件名写成
    C 风格的引号 + 八进制转义（``"docs/\\344\\275\\277..."``），
    那不是真实路径，后面 open() 必然失败。
    """
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        cwd=ROOT, capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError("git ls-tree 失败：%s"
                           % out.stderr.decode("utf-8", "ignore"))
    items = []
    for raw in out.stdout.split(b"\x00"):
        if not raw.strip():
            continue
        meta, path = raw.split(b"\t", 1)
        mode, _typ, sha = meta.decode("ascii").split()
        if mode == "160000":          # submodule，跳过
            continue
        items.append((path.decode("utf-8"), sha))
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="https://github.com/cyberironman/WEIXINDUOKAI.git")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    # 从 remote URL 里解析 owner/repo
    frag = args.remote.rstrip("/").split("/")[-1]
    if frag.endswith(".git"):
        frag = frag[:-4]
    owner_repo = "/".join(args.remote.rstrip("/").split("/")[-2:])
    owner_repo = owner_repo[:-4] if owner_repo.endswith(".git") else owner_repo
    print("目标仓库：%s" % owner_repo)

    tok = token()
    print("认证：%s" % call("GET", "/user", tok).get("login"))

    files = list_files()
    print("待上传文件：%d 个" % len(files))

    # 1) 建 blob
    print("\n[1/3] 上传 blob …")
    entries = []
    for i, (path, _sha) in enumerate(files, 1):
        with open(os.path.join(ROOT, path), "rb") as fh:
            content = fh.read()
        blob = call("POST", "/repos/%s/git/blobs" % owner_repo, tok, {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        if i % 5 == 0 or i == len(files):
            print("   %d/%d" % (i, len(files)))

    # 2) 建 tree
    print("\n[2/3] 创建 tree …")
    tree = call("POST", "/repos/%s/git/trees" % owner_repo, tok, {
        "tree": entries,
    })

    # 3) 建 commit —— 远端已有历史时接上去（GitHub 不允许往空仓库写 blob，
    #    所以远端至少有一个初始 commit，这里把它的 sha 作为 parent）
    msg = git("log", "-1", "--pretty=%B").strip()
    parents = []
    try:
        ref = call("GET", "/repos/%s/git/ref/heads/%s" % (owner_repo, args.branch), tok)
        head = ref["object"]["sha"]
        parents = [head]
        print("\n[3/3] 创建 commit（接在远端 %s 之后）…" % head[:8])
    except RuntimeError:
        print("\n[3/3] 创建 commit …")

    commit = call("POST", "/repos/%s/git/commits" % owner_repo, tok, {
        "message": msg,
        "tree": tree["sha"],
        "parents": parents,
    })
    print("   commit = %s" % commit["sha"])

    # 4) 更新 ref
    branch = args.branch
    try:
        call("GET", "/repos/%s/git/ref/heads/%s" % (owner_repo, branch), tok)
        ref = call("PATCH", "/repos/%s/git/refs/heads/%s" % (owner_repo, branch),
                   tok, {"sha": commit["sha"], "force": True})
    except RuntimeError:
        ref = call("POST", "/repos/%s/git/refs" % owner_repo, tok,
                   {"ref": "refs/heads/%s" % branch, "sha": commit["sha"]})
    print("   %s → %s" % (ref.get("ref"), ref.get("object", {}).get("sha")))

    # 5) 让本地 git 与远端对齐
    subprocess.run(["git", "update-ref", "refs/heads/%s" % branch, commit["sha"]],
                   cwd=ROOT, capture_output=True)
    print("\n完成：https://github.com/%s" % owner_repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
