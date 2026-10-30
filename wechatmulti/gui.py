# -*- coding: utf-8 -*-
"""微信多开管家 —— 图形界面。

这是装好之后的唯一入口：启动微信、加新号、检查状态、整理标题，
都在这里点。安装目录里每一个 exe 都能从「工具箱」里启动。
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import core

APP_TITLE = "微信多开管家"
BG = "#f5f6f8"
CARD = "#ffffff"
ACCENT = "#07c160"          # 微信绿
WARN = "#fa5151"
OK = "#0b7a3b"
TEXT = "#1a1a1a"
SUB = "#888888"
LINK = "#1a73e8"            # 可点击文字

# 工具箱按钮文案：种类 -> (短标签, 宽度)。宽度按字数收紧，一行放得下。
TOOL_LABELS = {
    "installer": "安装器·新增",
    "guard": "检查状态",
    "tag": "整理标题",
    "other": "其它程序",
}
TOOL_WIDTHS = {"installer": 11, "guard": 10, "tag": 10, "other": 10}


class LinkLabel(tk.Label):
    """蓝色可点击文字，鼠标悬停变下划线。"""

    def __init__(self, master, text, command, bg=CARD, fg=LINK,
                 family="Microsoft YaHei UI", size=10, bold=False):
        self._weight = "bold" if bold else "normal"
        self._family, self._size = family, size
        super().__init__(master, text=text, bg=bg, fg=fg, cursor="hand2",
                         justify="left", anchor="w",
                         font=(family, size, self._weight))
        self._cmd = command
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda e: self.config(
            font=(self._family, self._size, self._weight, "underline")))
        self.bind("<Leave>", lambda e: self.config(
            font=(self._family, self._size, self._weight)))

    def _on_click(self, _e):
        try:
            self._cmd()
        except Exception:
            pass


class HintOverlay(tk.Toplevel):
    """置顶箭头浮层，指向微信左下角的「≡」。6 秒后自动消失，点一下也关。"""

    def __init__(self, master, x, y, text="点这里 → 设置 → 通用设置"):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.96)
        except tk.TclError:
            pass

        w, h = 310, 96
        bx = max(0, min(x + 26, self.winfo_screenwidth() - w - 4))
        by = max(0, min(y - h // 2, self.winfo_screenheight() - h - 4))
        self.geometry("%dx%d+%d+%d" % (w, h, bx, by))

        cv = tk.Canvas(self, width=w, height=h, bg="#f5f6f8",
                       highlightthickness=0)
        cv.pack()
        mid = h // 2
        cv.create_polygon(0, mid, 22, mid - 16, 22, mid + 16,
                          fill=WARN, outline=WARN)
        cv.create_rectangle(22, 4, w - 4, h - 4, fill="#fff1f0",
                            outline=WARN, width=2)
        cv.create_text(36, mid - 14, text=text, anchor="w",
                       font=("Microsoft YaHei UI", 10, "bold"), fill="#a8071a")
        cv.create_text(36, mid + 13, text="红框位置就是左下角的「≡」按钮",
                       anchor="w", font=("Microsoft YaHei UI", 9), fill="#a8071a")

        self.bind("<Button-1>", lambda e: self.destroy())
        self.after(6000, self.destroy)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x690")
        self.minsize(820, 620)
        self.configure(bg=BG)

        self.instances = core.load_instances()
        self.running = set()
        self._guide_open = False

        self._build()
        self.refresh()

        # 里程碑①：第一次运行
        if core.guide_pending():
            self.after(600, lambda: self.show_guide(first_run=True))

    def show_guide(self, letter: str = "", first_run: bool = False):
        """弹出三步设置。同一时间只允许一个，关掉后按实例记录。"""
        if self._guide_open:
            return
        self._guide_open = True
        try:
            dlg = GuideDialog(self, letter=letter, first_run=first_run)
            self.wait_window(dlg)
        finally:
            self._guide_open = False

    # ---------------------------------------------------------- UI
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(head, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(head, text="完全使用原版微信客户端 · 官方本就允许多开 · 我们只是帮你把资料隔离",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB).pack(side="left", padx=12, pady=(6, 0))

        trust = tk.Frame(self, bg="#e6f7e6")
        trust.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(trust, text="✓ 无毒无害 · 合法合规 · 数据完全隔离 · 持续跟随官方版本更新",
                 font=("Microsoft YaHei UI", 9), bg="#e6f7e6", fg=OK,
                 justify="center").pack(anchor="center", pady=4)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 6))
        bar2 = tk.Frame(self, bg=BG)
        bar2.pack(fill="x", padx=18, pady=(0, 8))

        def btn(parent, text, cmd, fg=TEXT, bg=CARD, width=10):
            b = tk.Button(parent, text=text, command=cmd, font=("Microsoft YaHei UI", 9),
                          bg=bg, fg=fg, relief="flat", width=width, cursor="hand2",
                          activebackground="#e8e8e8", bd=0, pady=6)
            b.pack(side="left", padx=(0, 8))
            return b

        # 第一行：实例操作
        btn(bar, "＋ 新增实例", self.on_add, fg="white", bg=ACCENT, width=12)
        btn(bar, "启动", self.on_launch, width=8)
        btn(bar, "打开文件夹", self.on_open, width=11)
        btn(bar, "删除", self.on_delete, fg=WARN, width=8)
        btn(bar, "刷新", self.refresh, width=8)

        # 第二行：工具箱 —— 安装目录里单独放着的每个 exe，这里都有对应按钮
        tk.Label(bar2, text="工具箱", font=("Microsoft YaHei UI", 9, "bold"),
                 bg=BG, fg=SUB).pack(side="left", padx=(2, 10))
        for _fn, _path, _desc, kind in core.tool_exes():
            if kind not in TOOL_LABELS or kind == "other":
                continue        # 不认识的 exe 统一收进「工具箱…」
            btn(bar2, TOOL_LABELS[kind], lambda k=kind: self.on_tool(k),
                width=TOOL_WIDTHS.get(kind, 10))
        btn(bar2, "工具箱…", self.on_toolbox, width=9)
        btn(bar2, "打开目录", self.on_open_root, width=10)

        # 常驻提醒条
        banner = tk.Frame(self, bg="#fff1f0", highlightbackground="#ffccc7",
                          highlightthickness=1)
        banner.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(banner, text="⚠", font=("Microsoft YaHei UI", 11, "bold"),
                 bg="#fff1f0", fg=WARN).pack(side="left", padx=(10, 4))
        tk.Label(banner,
                 text="微信弹出「发现新版本」时，千万不要点「立即升级」——升级后多开会立刻失效。",
                 font=("Microsoft YaHei UI", 9), bg="#fff1f0", fg="#a8071a").pack(side="left")
        tk.Button(banner, text="怎么处理", command=self.on_guide,
                  font=("Microsoft YaHei UI", 9), bg="#fff1f0", fg="#a8071a",
                  relief="flat", bd=0, cursor="hand2",
                  activebackground="#ffe1dd").pack(side="right", padx=8)

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=30,
                        background=CARD, fieldbackground=CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"),
                        background="#eef0f3", relief="flat")
        style.map("Treeview", background=[("selected", "#d6f5e3")],
                  foreground=[("selected", TEXT)])

        cols = ("letter", "title", "ver", "data", "state", "size")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for cid, text, width, anchor in (
            ("letter", "实例", 56, "center"),
            ("title", "窗口标题", 96, "center"),
            ("ver", "微信版本", 90, "center"),
            ("data", "数据存放位置", 300, "w"),
            ("state", "状态", 84, "center"),
            ("size", "占用", 84, "e"),
        ):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor, stretch=(cid == "data"))
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.on_launch())

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=(0, 14))
        tk.Button(foot, text="① 首次使用必看：3 步设置", command=self.on_guide,
                  font=("Microsoft YaHei UI", 9, "bold"), bg="#fff7e6", fg="#b26a00",
                  relief="flat", bd=0, pady=6, padx=10, cursor="hand2").pack(side="left")
        self.status = tk.Label(foot, text="", font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB)
        self.status.pack(side="left", padx=14)

    # ---------------------------------------------------------- 数据
    def selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        letter = self.tree.item(sel[0], "values")[0]
        return next((i for i in self.instances if i.letter == letter), None)

    def refresh(self):
        self.instances = core.load_instances()
        self.running = core.running_profiles()

        self.tree.delete(*self.tree.get_children())
        for inst in self.instances:
            is_up = inst.profile.rstrip("\\").lower() in self.running
            size = core.dir_size(inst.home) if os.path.isdir(inst.home) else 0
            self.tree.insert("", "end", values=(
                inst.letter,
                inst.title,
                inst.version or "—",
                inst.data_root,
                "运行中" if is_up else "已停止",
                core.human(size),
            ))
        n_up = sum(1 for i in self.instances
                   if i.profile.rstrip("\\").lower() in self.running)
        self.status.config(text="共 %d 个实例，%d 个运行中" % (len(self.instances), n_up))

    # ---------------------------------------------------------- 动作
    def on_launch(self):
        inst = self.selected()
        if not inst:
            messagebox.showinfo(APP_TITLE, "先在列表里选一个实例。")
            return
        if inst.profile.rstrip("\\").lower() in self.running:
            messagebox.showinfo(APP_TITLE, "%s 已经在运行了。" % inst.title)
            return
        core.launch(inst)
        self.after(4000, self.refresh)
        # 里程碑③：启动一个还没做过三步设置的实例（留 6 秒等微信起来）
        if core.guide_pending(inst.letter):
            self.after(6000, lambda: self._guide_if_pending(inst.letter))

    def _guide_if_pending(self, letter):
        if core.guide_pending(letter):
            self.show_guide(letter=letter)

    def on_open(self):
        inst = self.selected()
        if not inst:
            return
        os.startfile(inst.home)

    def on_delete(self):
        inst = self.selected()
        if not inst:
            messagebox.showinfo(APP_TITLE, "先在列表里选一个实例。")
            return
        if inst.profile.rstrip("\\").lower() in self.running:
            messagebox.showwarning(APP_TITLE, "先退出这个微信，再删除。")
            return

        if inst.data_inside_home:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "⚠ 实例「%s」的聊天记录就在程序目录里面：\n%s\n\n"
                    "删除会把程序目录和聊天记录一起删掉，且无法恢复。\n\n"
                    "只想卸载程序、想保留聊天记录的话，请点「否」，\n"
                    "然后把上面这个目录里的数据先挪到别处。\n\n确定要连聊天记录一起删除吗？"
                    % (inst.title, inst.data_root), icon="warning", default="no"):
                return
            remove_data = True
        else:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "删除实例「%s」？\n\n会删除程序目录：\n%s\n\n"
                    "聊天记录会保留：\n%s"
                    % (inst.title, inst.home, inst.data_root)):
                return
            remove_data = False

        try:
            core.delete_instance(inst.letter, remove_data=remove_data)
        except RuntimeError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.refresh()

    def on_tool(self, kind):
        """工具箱按钮统一入口：安装器 / wxguard / wxtag / 其它 exe。"""
        if kind == "guard":
            self.on_check()
            return
        if kind == "tag":
            ok, msg = core.launch_tool("tag")
            self.status.config(text=("✓ " if ok else "✗ ") + msg,
                               fg=OK if ok else WARN)
            self.after(1500, self.refresh)
            return
        ok, msg = core.launch_tool(kind)
        if not ok:
            messagebox.showwarning(APP_TITLE, msg)
        else:
            self.status.config(text="✓ " + msg, fg=OK)

    def on_toolbox(self):
        """列出安装目录里每一个 exe，逐个给按钮 —— 不留黑盒。"""
        ToolboxDialog(self)

    def on_open_root(self):
        try:
            os.startfile(core.ROOT)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "打不开目录：\n%s\n\n%s" % (core.ROOT, exc))

    def on_check(self):
        """调 wxguard.exe 逐个检查实例是不是在跑。"""
        messagebox.showinfo("实例状态", core.guard_report(self.instances))
        self.refresh()

    # ---------------------------------------------------------- 新增实例
    def on_add(self):
        if not self.instances:
            messagebox.showinfo(APP_TITLE,
                                "还没有任何实例。\n\n请先运行安装包装好第一个实例，"
                                "再用这里新增第二个。")
            return
        AddDialog(self, self.instances)

    # ---------------------------------------------------------- 引导
    def on_guide(self):
        GuideDialog(self)


class ToolboxDialog(tk.Toplevel):
    """安装目录里每个 exe 一行，各自一个「启动」按钮。

    为什么要有这个：这些 exe 允许单独躺在安装目录里，但要求
    「都必须能由管家的按钮启动」—— 那么就得有一处把目录扫干净、
    连以后多出来的程序也不遗漏的地方。
    """

    def __init__(self, master):
        super().__init__(master)
        self.master_app = master
        self.title("工具箱")
        self.geometry("680x420")
        self.minsize(620, 360)
        self.configure(bg=BG)
        self.transient(master)

        tk.Label(self, text="安装目录里的程序", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(self, text=core.TOOL_HOME, font=("Microsoft YaHei UI", 9),
                 bg=BG, fg=SUB).pack(anchor="w", padx=20)

        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        items = core.tool_exes()
        if not items:
            tk.Label(box, text="这个目录里没有找到任何 exe。", font=("Microsoft YaHei UI", 10),
                     bg=BG, fg=WARN).pack(anchor="w")
        for fn, path, desc, kind in items:
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=fn, font=("Microsoft YaHei UI", 10, "bold"),
                     bg=CARD, fg=TEXT, width=20, anchor="w").pack(side="left", padx=(10, 0), pady=8)
            tk.Label(row, text=desc, font=("Microsoft YaHei UI", 9),
                     bg=CARD, fg="#555555", anchor="w").pack(side="left", padx=8)
            if kind == "self":
                tk.Label(row, text="就是当前这个窗口", font=("Microsoft YaHei UI", 9),
                         bg=CARD, fg=SUB).pack(side="right", padx=10)
                continue
            tk.Button(row, text="启动", command=lambda k=kind, p=path: self.run(k, p),
                      font=("Microsoft YaHei UI", 9), bg=ACCENT, fg="white",
                      relief="flat", bd=0, padx=16, pady=3,
                      cursor="hand2").pack(side="right", padx=10)

        self.status = tk.Label(self, text="", font=("Microsoft YaHei UI", 9),
                               bg=BG, fg=SUB, anchor="w", justify="left")
        self.status.pack(fill="x", padx=20, pady=(8, 0))

        tk.Button(self, text="关闭", command=self.destroy,
                  font=("Microsoft YaHei UI", 9), bg=CARD, relief="flat", bd=0,
                  pady=6, padx=20, cursor="hand2").pack(anchor="e", padx=20, pady=12)

    def run(self, kind, path):
        if kind == "guard":
            messagebox.showinfo("实例状态", core.guard_report(self.master_app.instances))
            self.master_app.refresh()
            return
        ok, msg = core.launch_tool(kind, path)
        self.status.config(text=("✓ " if ok else "✗ ") + msg,
                           fg=OK if ok else WARN)
        if kind == "tag":
            self.master_app.after(1500, self.master_app.refresh)


class AddDialog(tk.Toplevel):
    def __init__(self, master, instances):
        super().__init__(master)
        self.master_app = master
        self.title("新增实例")
        self.geometry("640x470")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.instances = instances
        letter = core.next_letter(instances)
        # 数据目录默认放在程序目录外面：删实例时不会连带删掉聊天记录
        default_data = os.path.join(core.ROOT, "Data", letter)

        pad = {"padx": 22}
        tk.Label(self, text="新增一个微信实例", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w", pady=(18, 2), **pad)
        tk.Label(self, text="新实例从已有实例直接复制，不需要重新下载。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB).pack(anchor="w", **pad)

        # 复制源
        f1 = tk.Frame(self, bg=BG)
        f1.pack(fill="x", pady=(16, 0), **pad)
        tk.Label(f1, text="复制源", font=("Microsoft YaHei UI", 9, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        self.src_var = tk.StringVar()
        opts = ["%s  (%s)" % (i.letter, i.home) for i in instances]
        self.src_box = ttk.Combobox(f1, values=opts, state="readonly",
                                    font=("Microsoft YaHei UI", 9))
        self.src_box.current(0)
        self.src_box.pack(fill="x", pady=(4, 0))

        # 实例代号
        f2 = tk.Frame(self, bg=BG)
        f2.pack(fill="x", pady=(14, 0), **pad)
        tk.Label(f2, text="实例代号", font=("Microsoft YaHei UI", 9, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        self.letter_var = tk.StringVar(value=letter)
        ttk.Entry(f2, textvariable=self.letter_var, width=6,
                  font=("Microsoft YaHei UI", 10)).pack(side="left", padx=8)
        tk.Label(f2, text="窗口会显示为「微信%s」" % letter,
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB).pack(side="left")

        # 数据位置
        f3 = tk.Frame(self, bg=BG)
        f3.pack(fill="x", pady=(14, 0), **pad)
        tk.Label(f3, text="数据存放位置（聊天记录、图片、文件）",
                 font=("Microsoft YaHei UI", 9, "bold"), bg=BG, fg=TEXT).pack(anchor="w")
        row = tk.Frame(f3, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        self.data_var = tk.StringVar(value=default_data)
        ttk.Entry(row, textvariable=self.data_var,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="选择…", command=self.pick_dir,
                  font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                  bg=CARD, padx=12, pady=3, cursor="hand2").pack(side="left", padx=(8, 0))

        tk.Label(self,
                 text="⚠ 不要选 U 盘、移动硬盘或 OneDrive 同步目录 —— 会崩或写不进去。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=WARN).pack(anchor="w", pady=(6, 0), **pad)

        # 说明
        info = tk.Frame(self, bg="#fffdf5", highlightbackground="#f0e0b0", highlightthickness=1)
        info.pack(fill="x", pady=(14, 0), **pad)
        tk.Label(info,
                 text="需要复制约 870 MB 程序文件（约 10–60 秒）。\n"
                      "首次启动后，微信还会自动下载约 580 MB 插件，请耐心等待。",
                 font=("Microsoft YaHei UI", 9), bg="#fffdf5", fg="#7a5c00",
                 justify="left").pack(anchor="w", padx=12, pady=8)

        # 进度
        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", pady=(14, 4), **pad)
        self.hint = tk.Label(self, text="", font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB)
        self.hint.pack(anchor="w", **pad)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(10, 18), **pad)
        self.go = tk.Button(btns, text="开始创建", command=self.start,
                            font=("Microsoft YaHei UI", 10, "bold"), bg=ACCENT, fg="white",
                            relief="flat", bd=0, pady=7, padx=22, cursor="hand2")
        self.go.pack(side="right")
        tk.Button(btns, text="取消", command=self.destroy,
                  font=("Microsoft YaHei UI", 9), bg=CARD, relief="flat", bd=0,
                  pady=7, padx=18, cursor="hand2").pack(side="right", padx=8)

    def pick_dir(self):
        d = filedialog.askdirectory(title="选择数据存放位置")
        if d:
            self.data_var.set(os.path.normpath(d))

    def start(self):
        idx = self.src_box.current()
        src = self.instances[idx].home
        letter = self.letter_var.get().strip().upper()
        data = self.data_var.get().strip()

        if not letter or not letter.isalpha() or len(letter) != 1:
            messagebox.showwarning(APP_TITLE, "实例代号请填单个字母，例如 D。")
            return
        if any(i.letter == letter for i in self.instances):
            messagebox.showwarning(APP_TITLE, "代号 %s 已经用过了。" % letter)
            return
        if not data:
            messagebox.showwarning(APP_TITLE, "请选择数据存放位置。")
            return
        if data.lower().startswith(("\\\\", "//")):
            messagebox.showwarning(APP_TITLE, "不要选网络位置。")
            return

        self.go.config(state="disabled", text="正在创建…")

        def progress(done, total, label):
            pct = done * 100.0 / total if total else 0
            self.after(0, lambda: (self.bar.configure(value=pct),
                                   self.hint.config(
                                       text="%s  %.0f%%  (%s / %s)"
                                            % (label, pct, core.human(done),
                                               core.human(total)))))

        def work():
            try:
                inst = core.create_instance(letter, data, src, progress)
                self.after(0, lambda: self.done(inst))
            except Exception as exc:
                self.after(0, lambda: self.fail(exc))

        threading.Thread(target=work, daemon=True).start()

    def done(self, inst):
        app = self.master_app
        self.destroy()
        app.refresh()
        # 里程碑②：新建实例 —— 每个号的设置是各存一份的，所以必须重新讲一遍
        app.show_guide(letter=inst.letter)
        if messagebox.askyesno(
                APP_TITLE,
                "实例「%s」创建好了。\n\n数据会存放在：\n%s\n\n现在就启动吗？\n\n"
                "（第一次启动要下插件，可能要 1–3 分钟）" % (inst.title, inst.data_root)):
            core.launch(inst)

    def fail(self, exc):
        self.go.config(state="normal", text="开始创建")
        messagebox.showerror(APP_TITLE, "创建失败：\n\n%s" % exc)


class GuideDialog(tk.Toplevel):
    """三步设置引导。

    文字是蓝色可点的：点了会把对应实例的微信窗口提到前台，并在左下角「≡」
    位置画一个置顶红框箭头，告诉你该点哪。

    为什么不直接替用户点进设置：微信 4.x 是 Qt 自绘，没有可访问性树，
    程序只能按坐标盲点；多级盲点一旦错位（版本/缩放一变）可能把开关点反，
    那比让用户自己点一次更糟。所以做成「把你带到位置 + 指出来」。
    """

    STEPS = [
        ("1", "关掉「有更新时自动升级微信」",
         "微信左下角「≡」→ 设置 → 通用设置\n"
         "找到「有更新时自动升级微信」，把它关掉。\n\n"
         "关掉后微信会提示「微信将不会自动下载和安装最新版本，\n"
         "但不影响安全补丁等重要更新的安装」——这是正常的。",
         "settings"),
        ("2", "把「自动下载小于 20M 的文件」改成 0",
         "同一个「通用设置」页面，找到\n"
         "「自动下载小于 20M 的文件」，把数值改成 0。\n\n"
         "不关的话，别人发的大图、视频、表情包会全部自动存到硬盘。",
         "settings"),
        ("3", "看到升级弹窗，点「忽略」",
         "微信偶尔还会弹「发现新版本」。\n"
         "请点「忽略本次更新」或「稍后处理」，\n"
         "千万不要点「立即升级」。\n\n"
         "一旦升级，多开功能会立刻失效，需要重新装一次。",
         "front"),
    ]

    def __init__(self, master, letter: str = "", first_run: bool = False):
        super().__init__(master)
        self.letter = (letter or "").upper()
        self.title("首次使用必看" if first_run else "3 步设置")
        sw = self.winfo_screenwidth()
        self.geometry("720x700+%d+%d" % (max(20, sw - 748), 40))
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(master)
        self.attributes("-topmost", True)     # 不抢焦点，但一直看得见
        self.protocol("WM_DELETE_WINDOW", self.close)

        head = "先做完这 3 步，再开始用" if first_run else "3 步设置，做完就稳了"
        tk.Label(self, text=head, font=("Microsoft YaHei UI", 15, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Label(self,
                 text="每个微信号的设置是各存一份的，所以每加一个号都要再做一次。\n"
                      "蓝色文字可以点 —— 点了会把你带到微信里该点的位置。",
                 font=("Microsoft YaHei UI", 9), bg=BG, fg=SUB,
                 justify="left").pack(anchor="w", padx=24)

        for num, title, body, mode in self.STEPS:
            card = tk.Frame(self, bg=CARD)
            card.pack(fill="x", padx=24, pady=(12, 0))
            tk.Label(card, text=num, font=("Microsoft YaHei UI", 14, "bold"),
                     bg=CARD, fg=ACCENT, width=3).pack(side="left", anchor="n", pady=10)
            inner = tk.Frame(card, bg=CARD)
            inner.pack(side="left", fill="both", expand=True, pady=10, padx=(0, 10))
            LinkLabel(inner, title, lambda m=mode: self.jump(m), bg=CARD,
                      size=10, bold=True).pack(anchor="w")
            tk.Label(inner, text=body, font=("Microsoft YaHei UI", 9),
                     bg=CARD, fg="#555555", justify="left").pack(anchor="w", pady=(4, 0))
            link = ("→ 点这里：把微信窗口切到前面，我指给你看" if mode == "settings"
                    else "→ 点这里：把微信窗口切到前面")
            LinkLabel(inner, link, lambda m=mode: self.jump(m),
                      bg=CARD, size=9).pack(anchor="w", pady=(6, 0))

        self.status = tk.Label(self, text="", font=("Microsoft YaHei UI", 9),
                               bg=BG, fg=SUB, justify="left", anchor="w")
        self.status.pack(fill="x", padx=24, pady=(10, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=24, pady=(10, 18), side="bottom")
        tk.Button(btns, text="这个号已经设置好了" if self.letter else "我知道了",
                  command=self.close,
                  font=("Microsoft YaHei UI", 10, "bold"), bg=ACCENT, fg="white",
                  relief="flat", bd=0, pady=8, padx=26, cursor="hand2").pack(side="right")
        tk.Button(btns, text="稍后再说", command=self.close,
                  font=("Microsoft YaHei UI", 9), bg=CARD, relief="flat", bd=0,
                  pady=8, padx=16, cursor="hand2").pack(side="right", padx=8)

    def jump(self, mode="settings"):
        ok, msg, pt = core.jump_to_settings(self.letter)
        if not ok:
            self.status.config(text="✗ " + msg, fg=WARN)
            return
        if mode == "settings" and pt:
            HintOverlay(self, pt[0], pt[1])
            self.status.config(
                text="✓ 已把「%s」切到前面，看左下角红框指的位置。" % msg, fg=OK)
        else:
            self.status.config(text="✓ 已把「%s」切到前面。" % msg, fg=OK)

    def close(self):
        core.mark_guide_done(self.letter)
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
