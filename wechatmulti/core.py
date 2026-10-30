# -*- coding: utf-8 -*-
"""微信多开 —— 核心逻辑。

这个包负责三件事：

1. **找路径**：本工具装在哪、实例放哪、聊天数据放哪，全部可配置。
2. **隔离资料**：给每个微信号准备独立的 ``USERPROFILE``、独立的配置根、
   独立的聊天数据目录 —— 这样同一台电脑上可以同时登录多个微信号，
   互不看到对方的聊天记录和文件。
3. **管住进程**：判断某个实例是不是已经在跑（读进程 PEB 里的
   ``USERPROFILE`` 来认领，不是看窗口标题），把微信窗口提到前台，
   算出左下角菜单按钮的位置。

关键事实（实测，改之前先看这里）：
  * 数据根 ini = ``<profile>\\AppData\\Roaming\\Tencent\\xwechat\\config\\51a1fffea11325a1e4104c6b3de47af7.ini``
    内容是一行绝对路径，指向 ``xwechat_files`` 的**父目录**。
  * 目标目录必须先建好，否则微信报 ``0x80070003``。
  * 启动器只认自己目录下的版本库子目录（``4.x.y``），所以实例目录里
    必须保留与官方一致的版本目录名。
  * 微信 4.x 是 Qt 自绘，没有可访问性树 —— 无法按控件名点击，
    只能把窗口提到前台 + 指出「≡」按钮的屏幕坐标。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field

__all__ = [
    "Instance", "ROOT", "TOOL_HOME", "load_instances", "save_instances",
    "next_letter", "create_instance", "delete_instance", "launch",
    "running_profiles", "wechat_windows", "jump_to_settings",
    "menu_button_point", "activate_window", "dir_size", "human",
    "make_desktop_entries", "guard_check", "tag_titles",
]

APP_NAME = "微信多开"


# ---------------------------------------------------------------- 路径

def _app_dir() -> str:
    """本工具（exe / 源码）所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(p: str) -> str:
    """统一成 Windows 反斜杠绝对路径。"""
    return os.path.normpath(p).replace("/", "\\")


def _home_dir() -> str:
    """默认的实例根目录。

    打包成 exe 后：和工具放在一起（装好即用，不用改）。
    源码运行：环境变量 ``WECHATMULTI_HOME`` 优先；
    否则 **D 盘优先**（``D:\\WeChatMulti``）—— 微信数据体积大，
    放 C 盘容易把系统盘塞满；D 盘不可用时才退回 ``~\\WeChatMulti``。
    """
    env = os.environ.get("WECHATMULTI_HOME", "").strip()
    if env:
        return _norm(env)
    if getattr(sys, "frozen", False):
        return _app_dir()
    # D 盘可用就用 D 盘
    if os.path.isdir("D:\\"):
        return _norm(r"D:\WeChatMulti")
    return _norm(os.path.join(os.path.expanduser("~"), "WeChatMulti"))


TOOL_HOME = _app_dir()
ROOT = _home_dir()
REGISTRY = os.path.join(ROOT, "instances.json")
GUIDE_STATE = os.path.join(ROOT, "guide_state.json")
DATA_ROOT = os.path.join(ROOT, "Data")
HELPER = os.path.join(TOOL_HOME, "tag_wx_windows.bat")

# 桌面文件夹名（放在桌面上的那个「微信多开」入口）
DESKTOP_FOLDER = APP_NAME

CONFIG_INI = "51a1fffea11325a1e4104c6b3de47af7.ini"


def set_paths(root: str = "", tool_home: str = "") -> None:
    """把核心逻辑重定向到指定目录（安装器用）。

    必须在工具文件部署好之后再调，GUARD / TAGGER 要重新探测一次。
    """
    global ROOT, REGISTRY, GUIDE_STATE, TOOL_HOME, DATA_ROOT, HELPER, GUARD, GUARD_IS_EXE
    global TAGGER, TAGGER_IS_EXE

    if root:
        ROOT = _norm(root)
        REGISTRY = os.path.join(ROOT, "instances.json")
        GUIDE_STATE = os.path.join(ROOT, "guide_state.json")
        DATA_ROOT = os.path.join(ROOT, "Data")
    if tool_home:
        TOOL_HOME = _norm(tool_home)
    GUARD, GUARD_IS_EXE = _pick("wxguard.exe", "wx_instance_guard.py")
    TAGGER, TAGGER_IS_EXE = _pick("wxtag.exe", "wx_tag_titles.py")
    HELPER = os.path.join(TOOL_HOME, "tag_wx_windows.bat")


def _pick(name: str, script: str) -> tuple:
    """优先用打包好的 exe（小白机器没 Python）；开发环境退回 .py。"""
    exe = os.path.join(TOOL_HOME, name)
    if os.path.isfile(exe):
        return exe, True
    return os.path.join(TOOL_HOME, script), False


GUARD, GUARD_IS_EXE = _pick("wxguard.exe", "wx_instance_guard.py")
TAGGER, TAGGER_IS_EXE = _pick("wxtag.exe", "wx_tag_titles.py")

# 仅在开发环境（没打包成 exe）用来找 Python 解释器
_PY_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python314", "python.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python313", "python.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python312", "python.exe"),
    shutil.which("python") or "",
]
_PYW_CANDIDATES = [p[:-9] + "w.exe" if p.endswith("python.exe") else p for p in _PY_CANDIDATES]


# ---------------------------------------------------------------- 进程探测

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class _PBI(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_long), ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_void_p), ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


def _read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[: got.value]


def read_userprofile(pid: int) -> str:
    """读目标进程 PEB 环境块里的 USERPROFILE —— 判断实例归属的唯一可靠依据。"""
    h = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # LIMITED_INFORMATION | VM_READ
    if not h:
        return ""
    try:
        pbi = _PBI()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return ""
        if not pbi.PebBaseAddress:
            return ""
        raw = _read_mem(h, pbi.PebBaseAddress + 0x20, 8)
        if len(raw) < 8:
            return ""
        params = int.from_bytes(raw, "little")
        raw = _read_mem(h, params + 0x80, 8)   # Environment，注意是 0x80 不是 0x88
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


def weixin_pids() -> list:
    """所有微信进程 PID。"""
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap in (0, wintypes.HANDLE(-1).value):
        return []
    pids = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower().startswith("weixin"):
                pids.append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def running_profiles() -> set:
    """当前正在跑的实例的 profile 目录（小写）。"""
    out = set()
    for pid in weixin_pids():
        p = read_userprofile(pid)
        if p:
            out.add(_norm(p).rstrip("\\").lower())
    return out


# ---------------------------------------------------------------- 实例清单

@dataclass
class Instance:
    letter: str
    data_root: str
    version: str = ""            # 版本库目录名，例如 4.1.15.56
    created_at: str = ""
    note: str = ""

    @property
    def home(self) -> str:
        return os.path.join(ROOT, self.letter)

    @property
    def profile(self) -> str:
        return os.path.join(self.home, "Profile")

    @property
    def version_dir(self) -> str:
        return os.path.join(self.home, self.version) if self.version else ""

    @property
    def launcher(self) -> str:
        return os.path.join(self.home, "Weixin.exe")

    @property
    def title(self) -> str:
        return APP_NAME + self.letter

    @property
    def data_inside_home(self) -> bool:
        """数据目录是否落在程序目录里 —— 是的话删实例会连带删掉聊天记录。"""
        h = os.path.normcase(os.path.normpath(self.home)).rstrip("\\") + "\\"
        d = os.path.normcase(os.path.normpath(self.data_root)).rstrip("\\") + "\\"
        return d.startswith(h)


def load_instances() -> list:
    if not os.path.exists(REGISTRY):
        return []
    try:
        raw = json.load(open(REGISTRY, "r", encoding="utf-8"))
    except Exception:
        return []
    out = []
    for item in raw:
        try:
            inst = Instance(**item)
        except TypeError:
            continue
        inst.data_root = _norm(inst.data_root)
        out.append(inst)
    return sorted(out, key=lambda i: i.letter)


def save_instances(items) -> None:
    os.makedirs(ROOT, exist_ok=True)
    data = [asdict(i) for i in sorted(items, key=lambda x: x.letter)]
    with open(REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def next_letter(items) -> str:
    """从已用的最大字母往后接。"""
    used = {i.letter.upper() for i in items}
    if not used:
        return "A"
    last = max(used)
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch > last and ch not in used:
            return ch
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch not in used:
            return ch
    return "Z"


# ---------------------------------------------------------------- 引导状态

def load_guide_state() -> dict:
    """{"global": bool, "instances": [字母, ...]} —— 哪些实例已经看过三步设置。"""
    if not os.path.isfile(GUIDE_STATE):
        return {"global": False, "instances": []}
    try:
        with open(GUIDE_STATE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return {"global": False, "instances": []}
    if not isinstance(d, dict):
        return {"global": False, "instances": []}
    d.setdefault("global", False)
    d.setdefault("instances", [])
    return d


def save_guide_state(state: dict) -> None:
    os.makedirs(ROOT, exist_ok=True)
    try:
        with open(GUIDE_STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def guide_pending(letter: str = "") -> bool:
    """这个实例（或全局）是不是还没做过三步设置。"""
    st = load_guide_state()
    if not st.get("global"):
        return True
    if letter:
        return letter.upper() not in {x.upper() for x in st.get("instances", [])}
    return False


def mark_guide_done(letter: str = "") -> None:
    st = load_guide_state()
    st["global"] = True
    if letter:
        used = {x.upper() for x in st.get("instances", [])}
        if letter.upper() not in used:
            st.setdefault("instances", []).append(letter.upper())
    save_guide_state(st)


# ---------------------------------------------------------------- 微信窗口定位

user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetForegroundWindow.restype = wintypes.HANDLE
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# 「≡」在客户区里的位置：左 29px、离底 33px（1355x874 客户区实测）。
MENU_BTN_LEFT = 29
MENU_BTN_BOTTOM = 33


def _class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title_of(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


# 微信里那些**不是主窗口**的 Qt 窗口标题。
# 它们的类名同样是 Qt51514QWindowIcon，光看类名分不出来 —— 必须按标题排除，
# 否则「设置」窗口会被当成主窗口：箭头画到设置窗口上、切窗口切到设置页上，
# 用户看到的就是「指引挡住了别的微信窗口」。
NON_MAIN_TITLES = (
    "设置", "設定", "Settings",
    "关于微信", "關於微信", "About",
    "迁移与备份", "遷移與備份",
    "聊天记录迁移", "聊天記錄遷移",
    "通用设置", "通用設定",
    "文件管理", "快捷按键", "消息通知", "隐私", "音视频通话",
    "截图", "外观", "账号与安全",
    "小程序", "公众号",
    "日志", "崩溃报告",
    "检查更新", "发现新版本", "版本更新",
    "确认", "提示", "警告",
)


def is_main_window(hwnd) -> bool:
    """这个 Qt 窗口是不是微信的**主**窗口（不是设置页、不是各种对话框）。"""
    title = _title_of(hwnd).strip()
    if title in NON_MAIN_TITLES:
        return False
    # 最小化 / 隐藏到托盘的窗口：矩形不可信（-32000 或 144x20），
    # 不能拿尺寸判断，只能靠标题判定。
    if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
        return True
    # 尺寸兜底：主窗口至少 400x400，对话框一般更小或更扁
    cr = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(cr)):
        if (cr.right - cr.left) < 400 or (cr.bottom - cr.top) < 400:
            return False
    return True


def _wechat_hwnds(letter: str = "", main_only: bool = True,
                  include_hidden: bool = True) -> list:
    """枚举属于某实例的微信顶层窗口。

    ``include_hidden`` 必须为 True —— 微信关窗口时是「最小化到托盘」，
    此时 ``IsWindowVisible`` 返回 False，但窗口对象还在。
    只认可见窗口的话，「点蓝色字 → 切到设置」在托盘状态下会完全没反应。
    排序：可见的排前面，隐藏的排后面。
    """
    profs = {}
    for pid in weixin_pids():
        p = read_userprofile(pid)
        if p:
            profs[pid] = os.path.normcase(os.path.normpath(p)).rstrip("\\")

    want = ""
    if letter:
        want = os.path.normcase(
            os.path.normpath(os.path.join(ROOT, letter.upper(), "Profile"))).rstrip("\\")

    visible, hidden = [], []

    def cb(hwnd, _lp):
        # 只认 Qt 窗口（弹出菜单是 ToolSaveBits）
        if not _class_of(hwnd).endswith("QWindowIcon"):
            return True
        vis = bool(user32.IsWindowVisible(hwnd))
        if not vis and not include_hidden:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        prof = profs.get(pid.value)
        if not prof:
            return True
        if want and prof != want:
            return True
        if main_only and not is_main_window(hwnd):
            return True
        (visible if vis else hidden).append(hwnd)
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return visible + hidden


def wechat_windows(letter: str = "") -> list:
    """找出某个实例（不传则任意实例）的微信**主**窗口。

    判据：类名是 Qt 窗口 + PEB 的 USERPROFILE 匹配 + 不是设置/对话框窗口。
    最后一条很关键 —— 「设置」窗口的类名和主窗口完全一样。
    """
    return _wechat_hwnds(letter, main_only=True)


def wechat_settings_windows(letter: str = "") -> list:
    """找出某实例的「设置」窗口。用来判断「是不是已经进到设置页了」。"""
    return [h for h in _wechat_hwnds(letter, main_only=False)
            if _title_of(h).strip() in ("设置", "設定", "Settings")]


def activate_window(hwnd) -> bool:
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    if tid_fg and tid_fg != tid_me:
        user32.AttachThreadInput(tid_me, tid_fg, True)
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if tid_fg and tid_fg != tid_me:
        user32.AttachThreadInput(tid_me, tid_fg, False)
    return user32.GetForegroundWindow() == hwnd


def menu_button_point(hwnd) -> tuple:
    """左下角「≡」的屏幕坐标。"""
    cr = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(cr)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x + MENU_BTN_LEFT, pt.y + cr.bottom - MENU_BTN_BOTTOM)


def jump_to_settings(letter: str = "") -> tuple:
    """把用户送到「设置」页。

    三级行为，按可靠性降级：

    1. **设置窗口已经开着** → 直接把它提到前台。这是真正的「跳转进设置」，
       零风险、零猜测。
    2. **设置窗口没开** → 把微信主窗口提到前台，返回「≡」的屏幕坐标，
       由界面画箭头指出来（用户点两下就到）。
    3. 主窗口都没有 → 报错让用户先启动实例。

    为什么不自己做「点 ≡ → 点设置」：
    微信 4.x 是 Qt 自绘，菜单项没有可访问性树，程序只能按坐标盲点。
    菜单顺序、DPI 缩放、字体任一变化都可能点到「退出登录」这种项上 ——
    那比让用户自己点两下严重得多。所以只做「已经开着就切过去」，
    没开就精确指路。

    返回 (ok, 消息, 锚点坐标或 None, 是否已经进入设置)
    """
    # 一级：设置页已经开着 → 直接切过去
    setting_wins = wechat_settings_windows(letter)
    if setting_wins:
        activate_window(setting_wins[0])
        return (True, "设置", None, True)

    # 二级：把主窗口提到前台并指出「≡」
    wins = wechat_windows(letter)
    if not wins:
        if letter:
            return (False,
                    "%s%s 现在没有打开的窗口，先点「启动」把它开起来。"
                    % (APP_NAME, letter.upper()), None, False)
        return (False, "现在没有打开的微信窗口，先启动一个实例。", None, False)
    hwnd = wins[0]
    activate_window(hwnd)
    return (True, _title_of(hwnd), menu_button_point(hwnd), False)


def all_wechat_rects() -> list:
    """**所有**微信窗口（主窗口 + 设置页 + 各实例）的屏幕矩形。

    给浮层避让用 —— 用户抱怨过「指引挡住了微信三的设置按钮」。
    所以避让名单必须是全部微信窗口，而不是只排除目标实例：
    目标实例自己的「≡」按钮同样不能被盖住。
    """
    rects = []
    for hwnd in _wechat_hwnds("", main_only=False):
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            continue
        if r.left <= -30000:            # 最小化，跳过
            continue
        rects.append((r.left, r.top, r.right, r.bottom))
    return rects


# ---------------------------------------------------------------- 校验 / 复制

def md5_of(path: str) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_source(home: str) -> tuple:
    """检查一个实例目录能不能当复制源。

    源目录里必须已经有：官方启动器（**原样，不打补丁**）+ 版本库目录。
    """
    exe = os.path.join(home, "Weixin.exe")
    if not os.path.isfile(exe):
        return False, "缺少 Weixin.exe：%s" % exe
    try:
        with open(exe, "rb") as fh:
            buf = fh.read()
    except OSError as exc:
        return False, "读不出启动器：%s" % exc
    if not buf:
        return False, "启动器是空文件：%s" % exe
    versions = [d for d in os.listdir(home)
                if os.path.isdir(os.path.join(home, d))
                and os.path.isfile(os.path.join(home, d, "Weixin.dll"))]
    if not versions:
        return False, "缺少版本库目录（里面应该有 Weixin.dll）"
    return True, "源可用"


def copy_tree(src: str, dst: str, progress=None, label: str = "") -> None:
    """带进度回调的目录复制。progress(done_bytes, total_bytes)。"""
    total = 0
    for dirpath, _dirs, files in os.walk(src):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass

    done = 0
    os.makedirs(dst, exist_ok=True)
    for dirpath, _dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for fn in files:
            s = os.path.join(dirpath, fn)
            d = os.path.join(target_dir, fn)
            try:
                size = os.path.getsize(s)
            except OSError:
                continue
            with open(s, "rb") as fsrc, open(d, "wb") as fdst:
                while True:
                    buf = fsrc.read(4 * 1024 * 1024)
                    if not buf:
                        break
                    fdst.write(buf)
                    done += len(buf)
                    if progress and total:
                        progress(done, total, label)
            try:
                shutil.copystat(s, d, follow_symlinks=False)
            except OSError:
                pass


# ---------------------------------------------------------------- 数据根 / profile

def write_data_root(profile: str, data_root: str) -> str:
    """写数据根 ini。返回写入的文件路径。"""
    cfg = os.path.join(profile, "AppData", "Roaming", "Tencent", "xwechat", "config")
    os.makedirs(cfg, exist_ok=True)
    path = os.path.join(cfg, CONFIG_INI)
    payload = _norm(data_root).rstrip("\\")
    with open(path, "wb") as fh:
        fh.write(payload.encode("ascii"))
    return path


def make_profile_skeleton(profile: str) -> None:
    """profile 目录必须提前建好，否则微信 SHGetKnownFolderPath 返回 0x80070003。"""
    for sub in (
        r"AppData\Roaming",
        r"AppData\Local\Temp",
        "Desktop",
        "Documents",
    ):
        os.makedirs(os.path.join(profile, sub), exist_ok=True)


# ---------------------------------------------------------------- 启动器脚本

def _probe_block(candidates, var) -> str:
    lines = []
    for i, cand in enumerate(candidates):
        if not cand:
            continue
        prefix = "if exist" if i == 0 else "if not defined %s if exist" % var
        lines.append('%s "%s" set "%s=%s"' % (prefix, cand, var, cand))
    return "\n".join(lines)


LAUNCHER_TEMPLATE = r"""@echo off
rem ==========================================================
rem  WeChat 4.x side-by-side launcher - instance {LETTER}
rem
rem  Config root = SHGetKnownFolderPath(RoamingAppData), which
rem  expands %USERPROFILE%\AppData\Roaming from THIS process
rem  env block. Overriding USERPROFILE gives a fully isolated
rem  WeChat instance. The launcher is the single-instance
rem  patched build, so every instance can coexist.
rem
rem  install  : {HOME}
rem  profile  : {HOME}\Profile
rem  data root: {DATA}
rem ==========================================================

set "WX_HOME={HOME}"

if not exist "%WX_HOME%\Weixin.exe" goto :missing

set "USERPROFILE=%WX_HOME%\Profile"
set "APPDATA=%WX_HOME%\Profile\AppData\Roaming"
set "LOCALAPPDATA=%WX_HOME%\Profile\AppData\Local"
set "TEMP=%WX_HOME%\Profile\AppData\Local\Temp"
set "TMP=%TEMP%"
set "HOMEDRIVE={DRIVE}:"
set "HOMEPATH={HOMEPATH}"

rem  Guard: a Weixin.exe whose USERPROFILE equals this profile is already up.
{guard_block}

:tag
call "{helper}" --watch 40
exit /b 0

:missing
echo.
echo   Not found: %WX_HOME%\Weixin.exe
echo   The instance {LETTER} install folder is incomplete.
echo.
pause
exit /b 1
"""


def _guard_block() -> str:
    """启动器里的防重复判断段。有 exe 就直接调，没有才退回 python。

    用 replace 而不是 % 格式化 —— 批处理里满是 %VAR%，一用 % 格式就得处处写成 %%，
    很容易漏。replace 没这个问题。
    """
    if GUARD_IS_EXE:
        tpl = (
            '"@GUARD@" "%WX_HOME%\\Profile" >nul 2>&1\n'
            'if errorlevel 1 (\n'
            '    start "" "%WX_HOME%\\Weixin.exe"\n'
            ')'
        )
        return tpl.replace("@GUARD@", GUARD)

    tpl = (
        'set "WX_PY="\n'
        '@PROBE@'
        '\n\nif not defined WX_PY (\n'
        '    start "" "%WX_HOME%\\Weixin.exe"\n'
        '    goto :tag\n'
        ')\n\n'
        '"%WX_PY%" "@GUARD@" "%WX_HOME%\\Profile" >nul 2>&1\n'
        'if errorlevel 1 (\n'
        '    start "" "%WX_HOME%\\Weixin.exe"\n'
        ')'
    )
    return tpl.replace("@PROBE@", _probe_block(_PY_CANDIDATES, "WX_PY")).replace("@GUARD@", GUARD)


def make_launcher(inst: Instance) -> str:
    # HOMEDRIVE/HOMEPATH 指向 profile，某些 API 会读这两个值。
    prof = inst.profile
    drive = os.path.splitdrive(prof)[0] or "C:"
    homepath = prof[len(drive):]
    text = LAUNCHER_TEMPLATE
    for key, val in (
        ("{LETTER}", inst.letter),
        ("{HOME}", inst.home),
        ("{DATA}", _norm(inst.data_root)),
        ("{DRIVE}", drive.rstrip(":")),
        ("{HOMEPATH}", homepath),
        ("{helper}", HELPER),
    ):
        text = text.replace(key, val)
    text = text.replace("{guard_block}", _guard_block())

    # 文件名用 ASCII：桌面上的入口 bat 要能纯文本引用它，避免编码踩坑。
    path = os.path.join(inst.home, "launch_%s.bat" % inst.letter)
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk", "strict")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def make_tag_helper() -> str:
    """生成 tag_wx_windows.bat（静默打标题，供启动器调用）。"""
    if TAGGER_IS_EXE:
        tpl = (
            "@echo off\n"
            "rem  Silent helper: tag every WeChat window with its instance letter.\n"
            "rem  Usage: tag_wx_windows.bat [--watch SECONDS]\n"
            'start "" "@TAGGER@" %*\n'
            "exit /b 0\n"
        )
        body = tpl.replace("@TAGGER@", TAGGER)
    else:
        tpl = (
            "@echo off\n"
            "rem  Silent helper: tag every WeChat window with its instance letter.\n"
            "rem  Usage: tag_wx_windows.bat [--watch SECONDS]\n"
            "setlocal\n\n"
            'set "PYW="\n'
            '@PROBE@'
            "\n\nif not defined PYW (\n    exit /b 1\n)\n\n"
            'if "%~1"=="" (\n'
            '    start "" "%PYW%" "@TAGGER@"\n'
            '    exit /b 0\n'
            ')\n\n'
            'start "" "%PYW%" "@TAGGER@" %*\n'
            'exit /b 0\n'
        )
        body = (tpl.replace("@PROBE@", _probe_block(_PYW_CANDIDATES, "PYW"))
                    .replace("@TAGGER@", TAGGER))
    data = body.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk", "strict")
    with open(HELPER, "wb") as fh:
        fh.write(data)
    return HELPER


# ---------------------------------------------------------------- 桌面入口

FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def desktop_dir() -> str:
    """真实桌面目录。用 SHGetKnownFolderPath，能自动跟随 OneDrive 桌面重定向。"""
    try:
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
            ]

        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(FOLDERID_Desktop).bytes_le, 16)
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ptr = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0:
            val = ptr.value
            ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.c_void_p(
                ctypes.cast(ptr, ctypes.c_void_p).value))
            if val:
                return val
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _write_bat(path: str, lines) -> str:
    text = "@echo off\r\n" + "\r\n".join(lines) + "\r\nexit /b 0\r\n"
    with open(path, "wb") as fh:
        fh.write(text.encode("gbk", "strict"))
    return path


def make_desktop_entries(instances=None) -> str:
    """在桌面建「微信多开」文件夹，放管家入口 + 各实例入口。返回该目录。"""
    folder = os.path.join(desktop_dir(), DESKTOP_FOLDER)
    os.makedirs(folder, exist_ok=True)

    tool_exe = os.path.join(TOOL_HOME, "微信多开管家.exe")
    if not os.path.isfile(tool_exe):
        tool_exe = os.path.join(TOOL_HOME, "wxmgr.exe")
    if os.path.isfile(tool_exe):
        _write_bat(os.path.join(folder, "微信多开管家.bat"),
                   ['start "" "%s"' % tool_exe])

    for inst in (instances if instances is not None else load_instances()):
        launcher = os.path.join(inst.home, "launch_%s.bat" % inst.letter)
        if os.path.isfile(launcher):
            _write_bat(os.path.join(folder, "%s.bat" % inst.title),
                       ['call "%s"' % launcher])
    return folder


# ---------------------------------------------------------------- 实例生命周期

def create_instance(letter: str, data_root: str, source_home: str, progress=None) -> Instance:
    """从 source_home 复制出一个新实例。

    source_home 必须是一个已经打好补丁的实例目录（里面是启动器 + 版本库）。
    """
    ok, msg = validate_source(source_home)
    if not ok:
        raise RuntimeError(msg)

    src_ver = [d for d in os.listdir(source_home)
               if os.path.isdir(os.path.join(source_home, d))
               and os.path.isfile(os.path.join(source_home, d, "Weixin.dll"))]
    version = src_ver[0] if src_ver else ""

    inst = Instance(letter=letter, data_root=_norm(data_root), version=version,
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    home = inst.home
    if os.path.isdir(home) and os.listdir(home):
        raise RuntimeError("目标目录已存在且非空：%s" % home)

    os.makedirs(home, exist_ok=True)
    os.makedirs(inst.data_root, exist_ok=True)

    # 1) 启动器
    shutil.copy2(os.path.join(source_home, "Weixin.exe"), inst.launcher)
    # 2) 版本库（目录名必须和官方一致，启动器按名字找）
    copy_tree(os.path.join(source_home, version),
              inst.version_dir, progress, "复制程序文件")
    # 3) profile 骨架 + 数据根
    make_profile_skeleton(inst.profile)
    write_data_root(inst.profile, inst.data_root)
    # 4) 启动器脚本
    make_tag_helper()
    make_launcher(inst)

    items = [i for i in load_instances() if i.letter != letter]
    items.append(inst)
    save_instances(items)
    return inst


def delete_instance(letter: str, remove_data: bool = False) -> None:
    items = load_instances()
    target = next((i for i in items if i.letter == letter), None)
    if not target:
        return
    home = target.home
    inside = target.data_inside_home

    # 数据目录在程序目录里面时，删程序目录必然连带删掉聊天记录。
    # 想保数据就把数据目录先挪出去，这里只负责不假装「保住了」。
    if inside and not remove_data:
        raise RuntimeError(
            "实例 %s 的聊天数据在程序目录里面：\n%s\n\n"
            "删掉程序目录会一起删掉聊天记录。\n"
            "如果只是想卸载程序、想保留记录，请先把数据目录移动到别处再删。"
            % (target.title, target.data_root))

    if os.path.isdir(home):
        shutil.rmtree(home, ignore_errors=True)
    if remove_data and not inside and os.path.isdir(target.data_root):
        shutil.rmtree(target.data_root, ignore_errors=True)
    save_instances([i for i in items if i.letter != letter])


def launch(inst: Instance) -> None:
    bat = make_launcher(inst)
    # /min：批处理自己那个控制台窗口最小化，别在用户眼前闪一下黑框。
    subprocess.Popen(["cmd.exe", "/c", "start", "/min", "", bat], shell=False)


def tag_titles() -> None:
    if TAGGER_IS_EXE:
        subprocess.Popen([TAGGER], shell=False)
        return
    pyw = next((p for p in _PYW_CANDIDATES if p and os.path.isfile(p)), None)
    if not pyw:
        return
    subprocess.Popen([pyw, TAGGER], shell=False)


CREATE_NO_WINDOW = 0x08000000


def guard_check(profile: str) -> int:
    """调 wxguard.exe 判断某个 profile 是否在跑。0=在跑，1=没跑，-1=调不起来。"""
    if GUARD_IS_EXE:
        try:
            r = subprocess.run([GUARD, profile],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW)
            return r.returncode
        except OSError:
            return -1
    want = _norm(profile).rstrip("\\").lower()
    return 0 if want in running_profiles() else 1


# ---------------------------------------------------------------- 工具清单
# 安装目录里允许单独放着的这些 exe（用户要求），
# 但每一个都必须能在管家界面里点到对应按钮 —— 不允许出现「双击才会用的黑盒」。
TOOL_SPECS = [
    ("微信多开管家.exe", "管家 · 主界面（本程序）", "self"),
    ("微信多开安装器.exe", "安装器 · 新增实例 / 修复启动器", "installer"),
    ("wxguard.exe", "wxguard · 检查实例是否正在运行", "guard"),
    ("wxtag.exe", "wxtag · 整理窗口标题", "tag"),
]


def tool_exes() -> list:
    """扫描安装目录，返回 [(文件名, 绝对路径, 说明, 种类)]。

    已知工具用固定说明，不认识的 exe 也一并列出（种类 other），
    保证「目录里任何一个 exe 都能从管家点开」这条不留死角。
    """
    known = {n: (d, k) for n, d, k in TOOL_SPECS}
    out, seen = [], set()
    try:
        names = sorted(os.listdir(TOOL_HOME))
    except OSError:
        names = []
    for fn in names:
        if not fn.lower().endswith(".exe") or fn in seen:
            continue
        seen.add(fn)
        desc, kind = known.get(fn, ("安装目录里的其它程序", "other"))
        out.append((fn, os.path.join(TOOL_HOME, fn), desc, kind))
    return out


def installer_path() -> str:
    for name in ("微信多开安装器.exe", "setup.exe", "安装器.exe"):
        p = os.path.join(TOOL_HOME, name)
        if os.path.isfile(p):
            return p
    return ""


def open_installer() -> bool:
    """从管家里拉起安装器（工具箱按钮用）。"""
    p = installer_path()
    if not p:
        return False
    try:
        os.startfile(p)
        return True
    except OSError:
        return False


def launch_tool(kind: str, exe: str = "") -> tuple:
    """工具箱统一入口。返回 (是否成功, 给用户看的一句话)。"""
    if kind == "installer":
        if not installer_path():
            return False, "没找到安装器，它应该和管家放在同一个目录：\n%s" % TOOL_HOME
        if open_installer():
            return True, "已打开安装器 —— 在里面点「开始安装」就会多出一个实例。"
        return False, "安装器启动失败，请手动双击：\n%s" % installer_path()

    if kind == "tag":
        tag_titles()
        return True, "已在后台整理窗口标题（微信窗口会显示成「微信A」「微信B」）。"

    if kind == "other" and exe:
        if not os.path.isfile(exe):
            return False, "文件不存在：\n%s" % exe
        try:
            os.startfile(exe)
            return True, "已启动：%s" % os.path.basename(exe)
        except OSError as exc:
            return False, "启动失败：%s\n\n%s" % (os.path.basename(exe), exc)

    return False, "这个工具没有对应的启动方式。"


def guard_report(items) -> str:
    """逐个实例跑 wxguard.exe，拼成一段给人看的文字。"""
    if not items:
        return "还没有任何实例。"
    lines = []
    for inst in items:
        rc = guard_check(inst.profile)
        if rc == 0:
            state = "运行中"
        elif rc == 1:
            state = "已停止"
        else:
            state = "检查失败（找不到 wxguard.exe）"
        lines.append("%s  %s\n     数据：%s" % (inst.title, state, inst.data_root))
    return "\n\n".join(lines)


def dir_size(path: str) -> int:
    total = 0
    for dirpath, _d, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return str(n)
