# -*- coding: utf-8 -*-
"""微信多开 —— 安装器。

设计目标（用户定稿）：
  * **只要选一次目录、只点一次按钮**。聊天记录放哪由程序自动安排，只读展示。
  * **不预置任何微信文件**。安装器自动找到你电脑上已经装好的官方微信，
    直接从它复制一份 —— 所以安装包很小，安装也快。
  * 装完之后的目录里，所有可执行文件都平铺放着，
    并且都能从「微信多开管家」的按钮点开。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import core, finder

# PyInstaller --noconsole 打包后 sys.stdout/stderr 为 None，print 会抛 AttributeError。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

APP_TITLE = "微信多开 安装程序"
BG = "#f5f6f8"
CARD = "#ffffff"
ACCENT = "#07c160"
WARN = "#fa5151"
TEXT = "#1a1a1a"
SUB = "#888888"
OK = "#0b7a3b"

MIN_FREE_EXTRA = 900 * 1024 * 1024      # 除程序文件外，还要留插件与聊天数据的余量

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def installer_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def drive_type(path: str) -> int:
    """2=可移动 3=本地 4=网络 5=光驱 6=内存盘"""
    try:
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
    except Exception:
        return 0


def check_path(path: str) -> str:
    """返回错误信息，空串表示通过。"""
    if not path:
        return "请选择安装位置。"
    low = path.lower()
    if low.startswith("\\\\") or low.startswith("//"):
        return "不要选网络位置（\\\\ 开头）。"
    if "onedrive" in low:
        return "不要选 OneDrive 同步目录 —— 微信会写不进去。"
    dt = drive_type(path)
    if dt == 2:
        return "不要选 U 盘 / 移动硬盘 —— 拔掉就没了，微信也会崩。"
    if dt == 4:
        return "不要选网络盘。"
    return ""


def peek_letter(root: str) -> str:
    """不改变全局状态地预读目标目录里的实例表，算出这次会用的代号。"""
    reg = os.path.join(root, "instances.json")
    items = []
    if os.path.isfile(reg):
        try:
            with open(reg, "r", encoding="utf-8") as fh:
                for raw in json.load(fh):
                    try:
                        items.append(core.Instance(**raw))
                    except TypeError:
                        pass
        except Exception:
            items = []
    return core.next_letter(items)


def free_space(path: str) -> int:
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe or "C:\\").free
    except OSError:
        return 0


# ---------------------------------------------------------------- 安装逻辑

def deploy_tools(root: str) -> str:
    """把工具 exe 平铺复制到安装目录根下，返回该目录。

    用户要求：其它 exe 允许单独躺在安装目录里，但必须都能从管家的按钮启动。
    所以不做子目录 —— 一眼看得见，管家「工具箱」里也逐个有按钮。
    """
    src = installer_dir()
    dst = root
    os.makedirs(dst, exist_ok=True)

    want = [n for n, _d, _k in core.TOOL_SPECS]
    alias = {"微信多开安装器.exe": ("setup.exe",),
             "微信多开管家.exe": ("wxmgr.exe",)}

    n, missing = 0, []
    for fn in want:
        s = os.path.join(src, fn)
        if not os.path.isfile(s):
            s = next((os.path.join(src, a) for a in alias.get(fn, ())
                      if os.path.isfile(os.path.join(src, a))), "")
        if not s:
            missing.append(fn)
            continue
        d = os.path.join(dst, fn)
        try:
            if os.path.exists(d) and os.path.samefile(s, d):
                n += 1          # 安装器就在安装目录里跑，自己拷自己
                continue
        except OSError:
            pass
        shutil.copy2(s, d)
        n += 1

    if "微信多开管家.exe" in missing:
        raise RuntimeError("安装包不完整：缺少 微信多开管家.exe。\n"
                           "请把整个安装包文件夹一起复制，不要只拷安装器。")
    if n == 0:
        raise RuntimeError("安装包不完整：找不到多开管家的程序文件。")
    return dst


def install_instance(root: str, data: str, letter: str, wechat, progress=None) -> core.Instance:
    """从已安装的官方微信复制出第一个实例。

    **不改微信的任何二进制文件。**
    启动器原样复制，靠环境隔离（独立 USERPROFILE + 独立数据根）实现多开。
    早期版本需要给启动器打二进制补丁绕过单实例检查，实测
    （微信 4.1.15.56，2026-10-30）新版本已不需要，且打补丁反而起不来。
    """
    os.makedirs(root, exist_ok=True)

    if progress:
        progress(0, 0, "准备程序文件")

    tool_home = deploy_tools(root)
    core.set_paths(root=root, tool_home=tool_home)

    inst = core.Instance(letter=letter, data_root=data, version=wechat.version,
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    home = inst.home
    if os.path.isdir(home) and os.listdir(home):
        raise RuntimeError("目标目录已存在且非空：%s" % home)
    os.makedirs(home, exist_ok=True)
    os.makedirs(inst.data_root, exist_ok=True)

    # 1) 启动器：原样复制官方那份，一个字节都不改
    if progress:
        progress(0, 0, "复制启动器")
    try:
        shutil.copy2(wechat.launcher, inst.launcher)
    except OSError as exc:
        raise RuntimeError("复制微信启动器失败：%s" % exc)

    # 2) 版本库
    core.copy_tree(wechat.version_dir, inst.version_dir, progress, "复制程序文件")

    # 3) profile + 数据根
    core.make_profile_skeleton(inst.profile)
    core.write_data_root(inst.profile, inst.data_root)

    # 4) 启动器脚本
    core.make_tag_helper()
    core.make_launcher(inst)

    core.save_instances([i for i in core.load_instances() if i.letter != letter] + [inst])
    return inst


# ---------------------------------------------------------------- GUI

class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x660")
        self.resizable(False, False)
        self.configure(bg=BG)

        self.wechat = finder.find_wechat()
        self.installed = None

        self._build()
        self._precheck()

    # ---------------------------------------------------------- UI
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=28, pady=(22, 2))
        tk.Label(head, text="微信多开", font=("Microsoft YaHei UI", 18, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(head, text="原版微信 · 官方支持多开 · 资料隔离更方便",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB).pack(
            side="left", padx=12, pady=(8, 0))

        trust = tk.Frame(self, bg="#e6f7e6")
        trust.pack(fill="x", padx=28, pady=(0, 6))
        tk.Label(trust, text="✓ 无毒无害 · 合法合规 · 数据完全隔离 · 持续跟随官方版本更新",
                 font=("Microsoft YaHei UI", 9), bg="#e6f7e6", fg=OK,
                 justify="center").pack(anchor="center", pady=4)

        tk.Label(self,
                 text="我们用的是原版微信客户端，官方本身就允许多开。\n"
                      "这个工具只是帮你把每个号的资料隔离开，让你不必每次都重复扫码登录。\n"
                      "下面只需要选一次位置，聊天记录放哪由程序自动安排。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg="#555555",
                 justify="left").pack(anchor="w", padx=28, pady=(10, 14))

        # 1) 用哪份微信
        f0 = tk.Frame(self, bg=BG)
        f0.pack(fill="x", padx=28)
        tk.Label(f0, text="① 用你电脑上已安装的微信", font=("Microsoft YaHei UI", 10, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        self.wechat_lbl = tk.Label(f0, text="", font=("Microsoft YaHei UI", 9),
                                   bg=BG, fg="#333333", anchor="w", justify="left")
        self.wechat_lbl.pack(anchor="w", pady=(4, 0))
        row0 = tk.Frame(f0, bg=BG)
        row0.pack(fill="x", pady=(4, 0))
        tk.Button(row0, text="手动指定…", command=self.pick_wechat,
                  font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                  bg=CARD, padx=12, pady=3, cursor="hand2").pack(side="left")

        # 2) 程序位置
        f1 = tk.Frame(self, bg=BG)
        f1.pack(fill="x", padx=28, pady=(14, 0))
        tk.Label(f1, text="② 装到哪里", font=("Microsoft YaHei UI", 10, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        row1 = tk.Frame(f1, bg=BG)
        row1.pack(fill="x", pady=(6, 0))
        self.root_var = tk.StringVar(value=self._default_root())
        ttk.Entry(row1, textvariable=self.root_var,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
        tk.Button(row1, text="浏览…", command=self.pick_root,
                  font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                  bg=CARD, padx=12, pady=3, cursor="hand2").pack(side="left", padx=(8, 0))

        # 3) 数据位置 —— 只读展示，跟着上面自动走，不让用户再选第二次
        f2 = tk.Frame(self, bg=BG)
        f2.pack(fill="x", padx=28, pady=(14, 0))
        tk.Label(f2, text="③ 聊天记录会存在这里", font=("Microsoft YaHei UI", 10, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        self.data_lbl = tk.Label(f2, text="", font=("Microsoft YaHei UI", 9),
                                 bg=BG, fg="#333333", anchor="w")
        self.data_lbl.pack(anchor="w", pady=(4, 0))
        tk.Label(f2, text="跟着上面的位置自动安排，不用再选。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB).pack(anchor="w")
        tk.Label(f2, text="⚠ 别把安装位置选到 U 盘、移动硬盘或 OneDrive 里。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=WARN).pack(anchor="w")

        self.root_var.trace_add("write", lambda *_a: self._on_root_change())

        # 说明
        info = tk.Frame(self, bg="#fffdf5", highlightbackground="#f0e0b0",
                        highlightthickness=1)
        info.pack(fill="x", padx=28, pady=(16, 0))
        self.info_lbl = tk.Label(
            info, text="", font=("Microsoft YaHei UI", 9), bg="#fffdf5",
            fg="#7a5c00", justify="left")
        self.info_lbl.pack(anchor="w", padx=12, pady=9)

        # 进度
        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", padx=28, pady=(18, 6))
        self.hint = tk.Label(self, text="", font=("Microsoft YaHei UI", 9),
                             bg=BG, fg=SUB, anchor="w", justify="left")
        self.hint.pack(fill="x", padx=28)

        # 装完必做的 3 步
        tip = tk.Frame(self, bg="#eef7ff", highlightbackground="#b6dcff",
                       highlightthickness=1)
        tip.pack(fill="x", padx=28, pady=(16, 0))
        tk.Label(tip, text="装完记得做这 3 步（不做的话，多开迟早会失效）",
                 font=("Microsoft YaHei UI", 9, "bold"), bg="#eef7ff",
                 fg="#0b5fa5").pack(anchor="w", padx=12, pady=(9, 3))
        tk.Label(tip,
                 text="① 微信左下角「≡」→ 设置 → 通用设置，关掉「有更新时自动升级微信」\n"
                      "② 同一页把「自动下载小于 20M 的文件」改成 0\n"
                      "③ 弹「发现新版本」时点「忽略本次更新」，千万别点「立即升级」",
                 font=("Microsoft YaHei UI", 9), bg="#eef7ff", fg="#0b5fa5",
                 justify="left").pack(anchor="w", padx=12, pady=(0, 9))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=28, pady=(16, 20), side="bottom")
        self.go = tk.Button(btns, text="开始安装", command=self.start,
                            font=("Microsoft YaHei UI", 10, "bold"), bg=ACCENT,
                            fg="white", relief="flat", bd=0, pady=8, padx=26,
                            cursor="hand2")
        self.go.pack(side="right")
        self.cancel = tk.Button(btns, text="取消", command=self.destroy,
                                font=("Microsoft YaHei UI", 9), bg=CARD,
                                relief="flat", bd=0, pady=8, padx=18, cursor="hand2")
        self.cancel.pack(side="right", padx=8)

        self._refresh_wechat()
        self._refresh_derived()

    # ---------------------------------------------------------- 交互
    def _default_root(self) -> str:
        # 优先用非系统盘（微信数据多，放系统盘容易满）
        for drive in ("D:", "E:", "F:", "G:"):
            if os.path.isdir(drive + "\\"):
                return drive + "\\WeChatMulti"
        return os.path.join(os.path.expanduser("~"), "WeChatMulti")

    def _refresh_wechat(self):
        if not self.wechat:
            self.wechat_lbl.config(
                text="✗ 没找到已安装的微信。\n"
                     "请先到微信官网装一次官方客户端，再回来点「手动指定…」。",
                fg=WARN)
            return
        self.wechat_lbl.config(
            text="✓ 找到微信 %s\n   %s" % (self.wechat.version, self.wechat.root),
            fg=OK)

    def pick_wechat(self):
        d = filedialog.askdirectory(title="选择微信安装目录")
        if not d:
            return
        info = finder._describe(d)
        if not info:
            messagebox.showwarning(APP_TITLE,
                                   "这个目录里没找到微信。\n\n"
                                   "要选的是装了微信的那个文件夹，\n"
                                   "里面应该有 Weixin.exe 和 4.x.x.x 这样的版本目录。")
            return
        self.wechat = info
        self._refresh_wechat()
        self._precheck()

    def pick_root(self):
        d = filedialog.askdirectory(title="选择安装位置")
        if d:
            self.root_var.set(os.path.normpath(d))

    def _refresh_derived(self):
        root = self.root_var.get().strip()
        letter = peek_letter(root) if root else "A"
        self.data_lbl.config(text=os.path.join(root, "Data", letter))

    def _on_root_change(self):
        self._refresh_derived()
        self._precheck()

    def _precheck(self):
        if not self.wechat:
            self.info_lbl.config(text="✗ 还没有可用的微信客户端。", fg=WARN)
            self.go.config(state="disabled")
            return
        need = core.dir_size(self.wechat.version_dir)
        free = free_space(self.root_var.get().strip() or "C:\\")
        ok = free >= need + MIN_FREE_EXTRA
        self.info_lbl.config(
            text="需要复制约 %s 程序文件（约 10–60 秒）。\n"
                 "装好后第一次启动，微信还会自动下载约 580 MB 插件。\n"
                 "%s 目标盘可用空间：%s"
                 % (core.human(need),
                    "✓" if ok else "✗ 空间可能不够，",
                    core.human(free)),
            fg="#7a5c00" if ok else WARN)
        self.go.config(state="normal" if ok else "disabled")

    def start(self):
        """一次点完：只校验选定的安装位置，其余全部自动派生，不再弹确认。"""
        root = os.path.normpath(self.root_var.get().strip())
        if not root:
            messagebox.showwarning(APP_TITLE, "请选择安装位置。")
            return
        err = check_path(root)
        if err:
            messagebox.showwarning(APP_TITLE, err)
            return
        if not self.wechat:
            messagebox.showwarning(APP_TITLE, "还没找到可用的微信客户端。")
            return

        letter = peek_letter(root)
        data = os.path.join(root, "Data", letter)

        self.go.config(state="disabled", text="正在安装…")
        self.cancel.config(state="disabled")

        def progress(done, total, label):
            pct = done * 100.0 / total if total else 0
            self.after(0, lambda: (self.bar.configure(value=pct),
                                   self.hint.config(
                                       text="%s  %.0f%%  (%s / %s)"
                                            % (label, pct, core.human(done),
                                               core.human(total)))))

        def work():
            try:
                inst = install_instance(root, data, letter, self.wechat, progress)
                self.after(0, lambda: self.done(inst))
            except Exception as exc:
                self.after(0, lambda: self.fail(exc))

        threading.Thread(target=work, daemon=True).start()

    def done(self, inst):
        self.installed = inst
        self.bar.configure(value=100)
        self.hint.config(text="安装完成。")

        self.go.config(state="normal", text="启动微信", command=self.launch)
        self.cancel.config(state="normal", text="打开管家", command=self.open_mgr)

        messagebox.showinfo(
            APP_TITLE,
            "装好了！\n\n"
            "程序目录：%s\n聊天数据：%s\n桌面入口：桌面上的「%s」文件夹\n\n"
            "我们用的是原版微信客户端，官方本身就支持多开，"
            "我们只是帮你把资料隔离，让你不必每次都重复扫码登录。\n\n"
            "接下来请先做 3 件事（管家第一次打开会自动提示）：\n"
            "1. 关闭「有更新时自动升级微信」\n"
            "2. 把「自动下载小于 20M 的文件」改成 0\n"
            "3. 看到升级弹窗点「忽略」，千万别点「立即升级」"
            % (inst.home, inst.data_root, core.DESKTOP_FOLDER))

    def fail(self, exc):
        self.go.config(state="normal", text="开始安装")
        self.cancel.config(state="normal")
        messagebox.showerror(APP_TITLE, "安装失败：\n\n%s" % exc)

    def launch(self):
        if self.installed:
            core.launch(self.installed)
        self.destroy()

    def open_mgr(self):
        exe = os.path.join(core.TOOL_HOME, "微信多开管家.exe")
        if not os.path.isfile(exe):
            exe = os.path.join(core.TOOL_HOME, "wxmgr.exe")
        try:
            os.startfile(exe)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "打不开管家：%s" % exc)
            return
        self.destroy()


def cli() -> int:
    """测试/静默安装用。"""
    args = {}
    it = iter(sys.argv[1:])
    for a in it:
        if a.startswith("--"):
            k = a[2:]
            args[k] = next(it, "") if k != "no-desktop" else True

    root = args.get("root", "") or os.path.join(os.path.expanduser("~"), "WeChatMulti")
    wechat = finder.find_wechat()
    if not wechat:
        print("没找到已安装的微信")
        return 2
    letter = peek_letter(root)
    data = os.path.join(root, "Data", letter)

    def progress(done, total, label):
        sys.stdout.write("\r  %s %.0f%% (%s/%s)   "
                         % (label, done * 100.0 / total if total else 0,
                            core.human(done), core.human(total)))
        sys.stdout.flush()

    inst = install_instance(root, data, letter, wechat, progress)
    print("\n装好了：%s  数据=%s  工具=%s"
          % (inst.home, inst.data_root, core.TOOL_HOME))
    return 0


def main():
    if len(sys.argv) > 1:
        sys.exit(cli())
    app = Installer()
    app.mainloop()


if __name__ == "__main__":
    main()
