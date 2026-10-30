# -*- coding: utf-8 -*-
"""给多开实例的微信窗口打上能区分的标题。

为什么需要它
------------
每个实例的窗口标题都叫「微信」，任务栏和 Alt-Tab 列表里根本分不清谁是谁。

判据
----
用每个进程的 **profile 目录**（从 PEB 环境块里读 ``USERPROFILE``）来认领 ——
这正是决定微信把配置写哪里的那个变量，所以它最权威。

**不能**用安装目录判断：实例 A 有时也是从 B 目录里的启动器起来的（补丁启动器
可以放在任意位置），用安装目录会认错人。

    USERPROFILE = <默认用户目录>          → 主号，保持原样
    USERPROFILE = <根目录>\\<字母>\\Profile → 微信<字母>

用法
----
    python -m wechatmulti.tagger                 打一次就退出
    python -m wechatmulti.tagger --watch SEC     持续 SEC 秒反复打
    python -m wechatmulti.tagger --restore       把主号标题还原成 WeChat
"""
import ctypes
import re
import sys
import time
from ctypes import wintypes

# PyInstaller --noconsole 打包后 sys.stdout/stderr 为 None，print 会抛 AttributeError。
if sys.stdout is None:
    sys.stdout = open(sys.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(sys.devnull, "w", encoding="utf-8", errors="ignore")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.SetWindowTextW.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


# x64 下 PEB / RTL_USER_PROCESS_PARAMETERS 的偏移
PEB_PROCESS_PARAMETERS = 0x20
PARAMS_ENVIRONMENT = 0x80

TITLE_RE = re.compile(r"^((?:微信|WeChat)[A-Z]?)(\(\d+\))?$")
SUFFIX_RE = re.compile(r"(\(\d+\))?$")
# 实例 profile 目录形如  <任意路径>\<单个字母>\Profile
PROFILE_RE = re.compile(r"\\([A-Za-z])\\Profile$", re.IGNORECASE)


def _read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[: got.value]


def read_env(pid):
    """从目标进程的环境块里读出 {大写变量名: 值}。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return {}
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return {}
        peb = pbi.PebBaseAddress
        if not peb:
            return {}
        raw = _read_mem(h, peb + PEB_PROCESS_PARAMETERS, 8)
        if len(raw) < 8:
            return {}
        params = int.from_bytes(raw, "little")
        raw = _read_mem(h, params + PARAMS_ENVIRONMENT, 8)
        if len(raw) < 8:
            return {}
        envptr = int.from_bytes(raw, "little")
        if not envptr:
            return {}

        blob = b""
        for size in (65536, 32768, 16384, 8192, 4096):
            blob = _read_mem(h, envptr, size)
            if blob:
                break
        if not blob:
            return {}

        text = blob.decode("utf-16-le", "ignore")
        env = {}
        for item in text.split("\x00"):
            if "=" in item:
                k, v = item.split("=", 1)
                env[k.upper()] = v
        return env
    finally:
        kernel32.CloseHandle(h)


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def collect():
    """列出所有微信主窗口及其应得的标题。"""
    found = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        if not TITLE_RE.match(title):
            return True
        pid = pid_of(hwnd)
        env = read_env(pid)
        profile = env.get("USERPROFILE", "")
        m = PROFILE_RE.search(profile)
        if m:
            found.append((hwnd, pid, title, "微信" + m.group(1).upper(), profile))
        else:
            found.append((hwnd, pid, title, None, profile))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def apply(verbose=False):
    changed = 0
    for hwnd, pid, title, tag, profile in collect():
        if not tag or title.startswith(tag):
            continue
        suffix = SUFFIX_RE.search(title).group(0) or ""
        new = tag + suffix
        user32.SetWindowTextW(hwnd, new)
        changed += 1
        if verbose:
            print("  pid=%-6d %-10s -> %-10s  profile=%s" % (pid, title, new, profile))
    return changed


def restore_default(verbose=False):
    """把主号（默认 profile）的标题还原成 WeChat。"""
    changed = 0
    for hwnd, pid, title, tag, profile in collect():
        if tag:
            continue
        if not title.startswith("微信"):
            continue
        suffix = SUFFIX_RE.search(title).group(0) or ""
        user32.SetWindowTextW(hwnd, "WeChat" + suffix)
        changed += 1
        if verbose:
            print("  pid=%-6d %-10s -> WeChat      profile=%s" % (pid, title, profile))
    return changed


def main():
    verbose = "-v" in sys.argv
    if "--restore" in sys.argv:
        print("restored %d window(s)" % restore_default(verbose))
        return

    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        secs = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 60
        deadline = time.time() + secs
        total = 0
        while time.time() < deadline:
            total += apply(verbose)
            time.sleep(2)
        print("tagged %d change(s) over %ds" % (total, secs))
        return

    print("tagged %d window(s)" % apply(verbose))


if __name__ == "__main__":
    main()
