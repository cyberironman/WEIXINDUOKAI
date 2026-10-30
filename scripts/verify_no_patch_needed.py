# -*- coding: utf-8 -*-
"""验证：微信 4.x 多开到底还需不需要二进制补丁？（严谨版）

**这个脚本绝对安全**
  * 所有文件读写都在 ``_sandbox_verify/`` 目录里；
  * 只**读取**已有微信进程的归属，绝不 kill 用户的任何微信进程；
  * 只 kill 自己沙箱里启动、且 USERPROFILE 精确匹配的进程；
  * 判据是「有没有带沙箱 profile 的新进程」，与别的实例无关。

要回答的问题
------------
微信 4.x 的单实例检查有两条绕法：
  A. 改二进制 —— 早期版本给启动器打三个跳转补丁；
  B. 改环境 —— 把 USERPROFILE 指到独立目录，让微信认为「这是新用户」。

2026-10-30 的对照实验发现：官方 4.1.15.56 启动器**原样**就能拉起独立实例，
打补丁的反而起不来 → 说明 B 已足够，A 在新版本上是有害的。

严谨版修正了初版实验的三个问题
--------------------------------
1. **版本库要完整** —— 初版只复制了 ``Weixin.dll``，缺依赖库，
   启动流程走不完，结论不可信。这里复制整个版本库目录。
2. **等待要够长** —— 初版只等 20 秒，微信首启要 30 秒以上才落盘。
3. **顺序要反过来再跑一遍** —— 初版先跑 orig 再跑 patched，
   可能被「前一个用例残留的进程/锁」影响。这里 patched 先跑、orig 后跑，
   再交换一次，两轮结果一致才算数。

用法：python scripts/verify_no_patch_needed.py
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wechatmulti import core, finder, patcher, sandbox   # noqa: E402

SB = sandbox.sandbox_root("verify")
WAIT = 40


def build(tag: str, launcher_bytes: bytes, info):
    """在沙箱里搭一个**完整**的实例。返回 (home, profile, data_root)。"""
    home = os.path.join(SB, tag)
    for p in (home, os.path.join(SB, "Data", tag)):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "Weixin.exe"), "wb") as fh:
        fh.write(launcher_bytes)
    # 完整版本库 —— 少一个文件启动流程就走不完
    print("    复制版本库 %s（约 870 MB）…" % info.version)
    core.copy_tree(info.version_dir, os.path.join(home, info.version),
                   None, "复制程序文件")

    prof = os.path.join(home, "Profile")
    core.make_profile_skeleton(prof)
    data = os.path.join(SB, "Data", tag)
    os.makedirs(data, exist_ok=True)
    core.write_data_root(prof, data)
    return home, prof, data


def survey(prof, data, before):
    """本沙箱实例的进程 / 窗口 / 落盘情况。"""
    pids = [p for p in sandbox.watch_sandbox_pids(prof, set()) if p not in before]
    xw = os.path.join(prof, "AppData", "Roaming", "Tencent", "xwechat")
    n_xw = len(os.listdir(xw)) if os.path.isdir(xw) else 0
    n_data = 0
    if os.path.isdir(data):
        for _r, _d, fs in os.walk(data):
            n_data += len(fs)
    wins = 0
    for hwnd in core.wechat_windows():
        _ = hwnd
        wins += 1
    return pids, n_xw, n_data


def kill_only_sandbox(pids):
    """只关自己沙箱的进程。"""
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)


def run_case(tag, launcher_bytes, info):
    home, prof, data = build(tag, launcher_bytes, info)
    before = set(core.weixin_pids())
    bat = sandbox.spawn_instance(home, prof)
    subprocess.Popen(["cmd.exe", "/c", "start", "/min", "", bat])
    time.sleep(WAIT)
    pids, n_xw, n_data = survey(prof, data, before)
    print("    新进程 = %d 个 %s" % (len(pids), pids))
    print("    profile 微信配置条目 = %d" % n_xw)
    print("    数据根文件数 = %d" % n_data)
    kill_only_sandbox(pids)
    return len(pids), n_xw, n_data


def main():
    info = finder.find_wechat()
    if not info:
        print("!! 本机没找到已安装的微信")
        return 1
    print("官方微信：%s @ %s" % (info.version, info.root))
    print("沙箱目录：%s" % SB)
    print("（只读现有微信进程，不 kill 用户实例）\n")

    official = open(info.launcher, "rb").read()

    patched_bytes = None
    try:
        sites = patcher.find_sites(official)
        b = bytearray(official)
        for site in patcher.SITES:
            off = sites[site.name]
            b[off:off + len(site.after)] = site.after
        patched_bytes = bytes(b)
    except patcher.PatchError as exc:
        print("（补丁特征在这版上匹配不到，跳过 patched 对照：%s）\n" % exc)

    results = {}
    # 两轮，顺序相反，排除「前一个用例残留」的影响
    orders = [("patched", "orig"), ("orig", "patched")] if patched_bytes else [("orig",)]
    for rnd, order in enumerate(orders, 1):
        print("=" * 62)
        print("第 %d 轮，顺序：%s" % (rnd, " → ".join(order)))
        for tag in order:
            data = official if tag == "orig" else patched_bytes
            print("  [%s] 启动 …" % tag)
            res = run_case("%s_r%d" % (tag, rnd), data, info)
            results.setdefault(tag, []).append(res)

    print("\n" + "=" * 62)
    print("汇总（每行 = 一轮：新进程数 / 配置条目 / 数据文件数）")
    for tag, runs in results.items():
        for i, r in enumerate(runs, 1):
            print("  %-8s 第%d轮  新进程=%-3d 配置=%-4d 数据=%d" % (tag, i, r[0], r[1], r[2]))

    orig_ok = all(r[0] > 0 for r in results.get("orig", []))
    patch_ok = all(r[0] > 0 for r in results.get("patched", []))
    print("\n结论：")
    print("  原版官方启动器 %s" % ("✅ 每轮都成功拉起独立实例" if orig_ok else "❌ 没起来"))
    if patched_bytes:
        print("  打了旧补丁的启动器 %s" % ("✅ 能用" if patch_ok else "❌ 起不来（补丁在新版上是有害的）"))
    if orig_ok and not patch_ok:
        print("\n  → 4.1.15.56 不需要二进制补丁。隔离 profile + 隔离数据根就够了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
