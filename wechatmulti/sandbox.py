# -*- coding: utf-8 -*-
"""安全护栏 —— 任何实验脚本动手前先过这一关。

**背景**：2026-10-30 的一次对照实验，因为脚本里写了
``close_all_except()`` 去清理「别的微信进程」，把用户正在使用的
微信 A / B / C 三个实例全部杀掉了。虽然数据一个没丢（只是进程被杀，
重即恢复），但这是不可接受的 —— 实验脚本绝不能碰用户的真实环境。

**规矩**（从现在开始，强制）：

1. **实验只在自己的沙箱目录里跑** —— 路径必须带 ``_sandbox_`` 标记，
   例如 ``<项目目录>\\_sandbox_test\\``。
2. **绝不 kill 用户的微信进程** —— 本模块不提供任何「批量关进程」的函数。
3. 要判断「某个微信进程属于哪个实例」，只能**读**，不能**杀**。
4. 需要验证「实例能不能起来」时，用 ``spawn_instance()``：它启动的进程
   必然带沙箱 profile 的 USERPROFILE，判据是「有没有带沙箱 profile 的新进程」，
   而不是「别人的进程还在不在」。

本模块被 scripts/ 和 tests/ 里的所有实验脚本引用。
"""
from __future__ import annotations

import os
import sys

__all__ = [
    "SANDBOX_MARK", "sandbox_root", "assert_sandbox", "spawn_instance",
    "watch_sandbox_pids", "STOP",
]

# 沙箱目录名必须含这个标记，用来一眼识别「这是实验区，不是真实环境」
SANDBOX_MARK = "_sandbox_"

# 真实实例的路径特征 —— 出现在这里就说明脚本要动真家伙了，必须拒绝
# 注意：这里只写**通用特征**，不写具体用户名（那是个人信息，不能进公开仓库）。
# 「主实例的 Roaming 目录」用「当前用户」动态拼出来，而不是写死用户名。
REAL_INSTANCE_PREFIXES = (
    r"d:\wechatmulti\b",
    r"d:\wechatmulti\c",
    r"c:\program files\tencent\weixin",
    r"c:\program files (x86)\tencent\weixin",
    r"e:\weixin",
    r"e:\xwechat_files",
)


def _user_roaming() -> str:
    """当前用户 Roaming 目录（小写）。用于动态构造禁止列表。"""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        home = os.path.expanduser("~")
        appdata = os.path.join(home, "AppData", "Roaming")
    return os.path.normcase(os.path.abspath(appdata))


def _forbidden_prefixes() -> tuple:
    """禁止操作的前缀列表 = 静态列表 + 当前用户的 Roaming 目录。"""
    extra = []
    roaming = _user_roaming()
    if roaming:
        extra.append(roaming)
        extra.append(os.path.join(roaming, "tencent"))
    return REAL_INSTANCE_PREFIXES + tuple(extra)


def sandbox_root(name: str = "test") -> str:
    """返回一个带沙箱标记的实验根目录，并确保它存在。"""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        SANDBOX_MARK + name)
    os.makedirs(root, exist_ok=True)
    return root


def assert_sandbox(path: str) -> str:
    """断言 path 在沙箱里。不是沙箱就抛异常，**不做任何删除**。

    判定方式：路径的**任意一段**目录名里带 ``_sandbox_`` 标记即算沙箱
    （沙箱根目录下的子目录也算）。这样既拦得住真实实例，
    又不会误伤沙箱内部的正常工作目录。
    """
    low = os.path.normcase(os.path.abspath(path))
    parts = low.replace("/", "\\").split("\\")
    if not any(SANDBOX_MARK in p for p in parts if p):
        raise RuntimeError(
            "拒绝操作：%r 不在沙箱里。\n"
            "实验脚本只允许操作路径中带 %r 的目录。\n"
            "真实实例（%s）绝不能被实验脚本碰。"
            % (path, SANDBOX_MARK, "、".join(REAL_INSTANCE_PREFIXES))
        )
    for pref in _forbidden_prefixes():
        if low.startswith(pref):
            raise RuntimeError("拒绝操作：%r 指向真实实例目录。" % path)
    return low


def spawn_instance(home: str, profile: str, weixin: str = "") -> str:
    """在沙箱里启动一个隔离实例，返回生成的 .bat 路径。

    这是**唯一**允许的启动入口 —— 它只设置本实例自己的环境变量，
    不会去动别的任何进程。
    """
    assert_sandbox(home)
    assert_sandbox(profile)
    exe = weixin or os.path.join(home, "Weixin.exe")
    bat = os.path.join(home, "_launch_sandbox.bat")
    env = {
        "USERPROFILE": profile,
        "APPDATA": os.path.join(profile, "AppData", "Roaming"),
        "LOCALAPPDATA": os.path.join(profile, "AppData", "Local"),
        "TEMP": os.path.join(profile, "AppData", "Local", "Temp"),
        "TMP": os.path.join(profile, "AppData", "Local", "Temp"),
    }
    lines = ["@echo off"]
    for k, v in env.items():
        lines.append('set "%s=%s"' % (k, v))
    lines.append('cd /d "%s"' % home)
    lines.append('start "" "%s"' % exe)
    with open(bat, "wb") as fh:
        fh.write(("\r\n".join(lines) + "\r\n").encode("gbk"))
    return bat


def watch_sandbox_pids(profile: str, before: set) -> list:
    """列出**属于本沙箱 profile** 的新进程。

    只读，绝不杀任何进程。
    """
    from . import core  # 延迟导入，避免循环

    want = os.path.normcase(os.path.normpath(profile)).rstrip("\\")
    out = []
    for pid in core.weixin_pids():
        up = core.read_userprofile(pid)
        if not up:
            continue
        if os.path.normcase(os.path.normpath(up)).rstrip("\\") == want:
            out.append(pid)
    return out


STOP = "STOP"   # 显式常量，提醒阅读者：本模块不提供 kill 能力
