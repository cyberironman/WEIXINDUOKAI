# -*- coding: utf-8 -*-
"""判断某个多开实例是不是已经在运行。

为什么需要它
------------
早期版本靠「独占打开实例的日志文件」来判断，假设微信会一直锁着那个文件。
实测微信并不总持锁 → 守卫会误报「没在跑」→ 于是同一个 profile 被开了两个进程
→ 微信的数据库有损坏风险。

权威判据
--------
枚举所有 ``Weixin.exe`` 进程，从**每个进程的 PEB 环境块**里读出
``USERPROFILE``，跟这个实例自己的 profile 目录比。
``USERPROFILE`` 正是决定微信把配置写哪里的那个变量，所以它说了算。

用法
----
    python -m wechatmulti.guard <profile_root>

退出码
------
    0  实例正在运行
    1  实例没在运行
    2  参数不对
"""
import ctypes
import sys
from ctypes import wintypes

# PyInstaller --noconsole 打包后 sys.stdout/stderr 为 None，print 会抛 AttributeError。
# 兜底到空设备，保证退出码逻辑不受影响。
if sys.stdout is None:
    sys.stdout = open(sys.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(sys.devnull, "w", encoding="utf-8", errors="ignore")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260

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
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        # 注意：x64 下这里是 8 字节指针。写成 DWORD 会让整个结构体少 4 字节，
        # Process32First 直接返回 ERROR_BAD_LENGTH（24），枚举结果永远是空的。
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_long),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_void_p),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


# x64 下 PEB / RTL_USER_PROCESS_PARAMETERS 的偏移
PEB_PROCESS_PARAMETERS = 0x20
PARAMS_ENVIRONMENT = 0x80


def _read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[: got.value]


def read_userprofile(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return ""
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return ""
        if not pbi.PebBaseAddress:
            return ""
        raw = _read_mem(h, pbi.PebBaseAddress + PEB_PROCESS_PARAMETERS, 8)
        if len(raw) < 8:
            return ""
        params = int.from_bytes(raw, "little")
        raw = _read_mem(h, params + PARAMS_ENVIRONMENT, 8)
        if len(raw) < 8:
            return ""
        envptr = int.from_bytes(raw, "little")
        if not envptr:
            return ""
        blob = b""
        for size in (65536, 32768, 16384, 8192, 4096):
            blob = _read_mem(h, envptr, size)
            if blob:
                break
        if not blob:
            return ""
        for item in blob.decode("utf-16-le", "ignore").split("\x00"):
            if item.upper().startswith("USERPROFILE="):
                return item.split("=", 1)[1]
        return ""
    finally:
        kernel32.CloseHandle(h)


def weixin_pids():
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, wintypes.HANDLE(-1).value):
        return []
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            # 用前缀匹配而不是等号：微信改过外壳名（Weixin / WeixinX 等），
            # 精确匹配一旦对不上就会误判「没在跑」→ 同 profile 双开 → DB 损坏。
            name = entry.szExeFile.lower()
            if name.startswith("weixin"):
                pids.append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _norm(p):
    """归一成反斜杠，否则 Git Bash / MSYS 传进来的 D:/a/b 与 PEB 里的 D:\\a\\b 比不上。"""
    return p.replace("/", "\\").rstrip("\\").lower()


def main():
    if len(sys.argv) < 2:
        print("usage: python -m wechatmulti.guard <profile_root>")
        return 2
    want = _norm(sys.argv[1])

    for pid in weixin_pids():
        profile = _norm(read_userprofile(pid))
        if profile and profile == want:
            print("running pid=%d profile=%s" % (pid, sys.argv[1]))
            return 0

    print("not running: %s" % sys.argv[1])
    return 1


if __name__ == "__main__":
    sys.exit(main())
