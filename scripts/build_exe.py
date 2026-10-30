# -*- coding: utf-8 -*-
"""把项目打包成 4 个独立的 exe。

产物（都在 dist\\）：

  微信多开管家.exe        主界面（tkinter）
  微信多开安装器.exe      新增实例 / 修复启动器
  wxguard.exe             防重复启动（控制台）
  wxtag.exe               窗口打标（控制台）

用法：
    python scripts\\build_exe.py            # 全部打包
    python scripts\\build_exe.py --clean    # 先清 build/ 与 dist/

注意
----
**不要给 PyInstaller 加 ``--clean``。** 它会在 ``build\\`` 下批量删除
中间文件（``localpycs`` 一类，一次五十几个），某些环境的批量删除保护
会直接拦下来，让构建以非零码失败。改成自己清目录更稳。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")

# (输出 exe 名, 入口脚本, 是否控制台程序, 图标占位)
TARGETS = [
    ("微信多开管家.exe", os.path.join(ROOT, "wechatmulti", "gui.py"), False),
    ("微信多开安装器.exe", os.path.join(ROOT, "wechatmulti", "installer.py"), False),
    ("wxguard.exe", os.path.join(ROOT, "wechatmulti", "guard.py"), True),
    ("wxtag.exe", os.path.join(ROOT, "wechatmulti", "tagger.py"), True),
]

# 入口脚本里不是「直接跑」的模块，需要走 __main__ 分发
ENTRY_PATCH = {
    "gui.py": "from wechatmulti.gui import main; main()",
    "installer.py": "from wechatmulti.installer import main; main()",
    "guard.py": "from wechatmulti.guard import main; sys.exit(main())",
    "tagger.py": "from wechatmulti.tagger import main; sys.exit(main())",
}


def clean() -> None:
    for d in (DIST, BUILD):
        if os.path.isdir(d):
            print("清理", d)
            shutil.rmtree(d, ignore_errors=True)


def make_entry(src: str, kind: str) -> str:
    """生成 PyInstaller 用的入口脚本。

    直接拿模块当入口不行 —— 那些模块是「被导入」的，没有顶层 main 调用。
    这里生成一个临时入口文件，内容是几行 import + 调用。
    """
    tmp = os.path.join(HERE, "_entry_%s.py" % os.path.splitext(os.path.basename(src))[0])
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# -*- coding: utf-8 -*-\n")
        fh.write("import sys\n")
        fh.write("sys.path.insert(0, r'%s')\n" % ROOT)
        fh.write(ENTRY_PATCH[kind] + "\n")
    return tmp


def build_one(name: str, entry: str, console: bool) -> bool:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name", name,
        "--distpath", DIST,
        "--workpath", BUILD,
        "--paths", ROOT,
    ]
    cmd.append("--console" if console else "--noconsole")
    cmd.append(entry)
    print("\n=== 打包 %s ===" % name)
    print(" ", " ".join(cmd[:8]), "…")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        print("!! %s 打包失败 rc=%d" % (name, proc.returncode))
        return False
    out = os.path.join(DIST, name)
    if not os.path.isfile(out):
        print("!! %s 没生成" % name)
        return False
    print("   -> %s  (%.1f MB)" % (out, os.path.getsize(out) / 1048576.0))
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="先清 build/ 与 dist/")
    args = ap.parse_args()

    if args.clean:
        clean()
    os.makedirs(DIST, exist_ok=True)

    entries = []
    for name, src, console in TARGETS:
        kind = os.path.basename(src)
        entries.append((name, make_entry(src, kind), console))

    ok = True
    for name, entry, console in entries:
        if not build_one(name, entry, console):
            ok = False
        finally_entry = entry
        try:
            os.remove(finally_entry)
        except OSError:
            pass

    print("\n" + "=" * 56)
    if ok:
        print("全部打包完成，产物在：")
        for name, _e, _c in TARGETS:
            p = os.path.join(DIST, name)
            if os.path.isfile(p):
                print("  %-24s %8.1f MB   %s" % (
                    name, os.path.getsize(p) / 1048576.0,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))))
    else:
        print("有目标打包失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
