# -*- coding: utf-8 -*-
"""自动定位本机已安装的官方微信客户端。

查找顺序（先注册表，再常见目录）：
  1. ``HKCU\\Software\\Tencent\\Weixin\\InstallPath``
  2. ``HKLM\\SOFTWARE\\Tencent\\Weixin\\InstallPath``（含 32/64 位视图）
  3. ``%ProgramFiles%\\Tencent\\Weixin`` 等常见目录
  4. ``%LOCALAPPDATA%`` / ``%APPDATA%`` 下的用户级安装

找到之后识别出：
  * ``launcher`` —— 外壳启动器 ``Weixin.exe``
  * ``version_dir`` —— 版本库目录（形如 ``4.1.15.56``，里面是 ``Weixin.dll``）
  * ``version`` —— 版本号字符串
"""
from __future__ import annotations

import os
import re

__all__ = ["WeChatInstall", "find_wechat", "list_candidates"]

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")
_DLL_NAME = "Weixin.dll"
_LAUNCHER_NAME = "Weixin.exe"


class WeChatInstall:
    """一个微信客户端安装位置。"""

    def __init__(self, root: str, launcher: str, version_dir: str, version: str):
        self.root = root
        self.launcher = launcher
        self.version_dir = version_dir
        self.version = version

    @property
    def dll(self) -> str:
        return os.path.join(self.version_dir, _DLL_NAME)

    def __repr__(self):
        return "<WeChatInstall %s @ %s>" % (self.version, self.root)


# ---------------------------------------------------------------- 注册表

def _reg_paths() -> list:
    """从注册表里读微信的安装路径。"""
    out = []
    try:
        import winreg
    except ImportError:
        return out

    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin", 0),
    ]
    for root, path, view in keys:
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as key:
                value, _kind = winreg.QueryValueEx(key, "InstallPath")
        except OSError:
            continue
        if value:
            out.append(os.path.normpath(value))
    return out


# ---------------------------------------------------------------- 常见目录

def _common_dirs() -> list:
    out = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    for base in (pf, pf86):
        if base:
            out.append(os.path.join(base, "Tencent", "Weixin"))
    for base in (local, roaming):
        if base:
            out.append(os.path.join(base, "Tencent", "Weixin"))
            out.append(os.path.join(base, "WeChat"))
    return out


# ---------------------------------------------------------------- 识别

def _version_dirs(root: str) -> list:
    """列出根目录下的版本库目录（新版本优先）。"""
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not _VERSION_RE.match(name):
            continue
        if os.path.isfile(os.path.join(path, _DLL_NAME)):
            found.append((name, path))
    found.sort(key=lambda x: _ver_key(x[0]), reverse=True)
    return found


def _ver_key(v: str) -> tuple:
    try:
        return tuple(int(x) for x in _VERSION_RE.match(v).group(0).split("."))
    except Exception:
        return (0,)


def _describe(root: str) -> "WeChatInstall | None":
    root = os.path.normpath(root)
    launcher = os.path.join(root, _LAUNCHER_NAME)
    if not os.path.isfile(launcher):
        return None
    vdirs = _version_dirs(root)
    if not vdirs:
        return None
    name, path = vdirs[0]
    return WeChatInstall(root, launcher, path, name)


def list_candidates() -> list:
    """所有候选安装位置（去重，保持顺序）。"""
    seen, out = set(), []
    for path in _reg_paths() + _common_dirs():
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        info = _describe(path)
        if info:
            out.append(info)
    return out


def find_wechat() -> "WeChatInstall | None":
    """返回本机已安装的官方微信；找不到返回 None。"""
    cands = list_candidates()
    return cands[0] if cands else None
