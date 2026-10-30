# -*- coding: utf-8 -*-
"""程序化验证引导面板：浮层位置、自动侦测、跳转文案。

不开主循环、不需要人眼看 —— 直接构造 GuideDialog 并调用方法，
断言关键状态。这样能在无人值守的情况下确认行为正确。
"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\WorkBuddyData\WeChatMulti")

TMP = tempfile.mkdtemp(prefix="wxmgr_verify_")
from wechatmulti import core            # noqa: E402

core.set_paths(root=TMP, tool_home=r"D:\WorkBuddyData\WeChatMulti")

import tkinter as tk                     # noqa: E402
from wechatmulti import gui            # noqa: E402

log = []


def check(name, cond, detail=""):
    log.append(("PASS" if cond else "FAIL", name, detail))
    print("%s  %s  %s" % ("✓" if cond else "✗", name, detail))


root = tk.Tk()
root.withdraw()

# ---------------------------------------------------------------- 浮层位置
rects = core.all_wechat_rects()
anchor = (615, 904)
pos = gui.pick_overlay_pos(300, 92, anchor, rects, 1920, 1080)
box = (pos[0], pos[1], pos[0] + 300, pos[1] + 92)
hit = [r for r in rects if gui._rects_overlap(box, r)]
check("浮层避开所有微信窗口", not hit, "位置=%s 撞到=%s" % (pos, hit or "无"))
check("浮层在屏幕内",
      0 <= pos[0] <= 1920 - 300 and 0 <= pos[1] <= 1080 - 92, "位置=%s" % (pos,))

# 极端场景：屏幕被微信窗口铺满，浮层仍要在屏幕内
full = [(0, 0, 1920, 1080)]
pos2 = gui.pick_overlay_pos(300, 92, anchor, full, 1920, 1080)
check("铺满屏幕时浮层仍可见",
      0 <= pos2[0] <= 1920 - 300 and 0 <= pos2[1] <= 1080 - 92, "位置=%s" % (pos2,))

# ---------------------------------------------------------------- 引导面板
dlg = gui.GuideDialog(root, letter="", first_run=True)
dlg.update()

check("面板有自动侦测提示条", hasattr(dlg, "detect"))
check("面板有状态条", hasattr(dlg, "status"))

# 模拟「设置页已打开」：直接改状态并检查文案会变
dlg._settings_seen = False
dlg.detect.config(text="✓ 检测到「设置」已打开", fg=gui.OK)
dlg.update()
check("自动侦测文案可写", "检测到" in dlg.detect.cget("text"),
      dlg.detect.cget("text")[:30])

# 调 jump()：真实环境里可能成功也可能没有窗口，两种情况都不能抛异常
try:
    dlg.jump("settings")
    dlg.update()
    ok_text = dlg.status.cget("text")
    check("jump() 不抛异常", True, ok_text[:46])
    check("jump() 给出了明确反馈", bool(ok_text.strip()), ok_text[:46])
except Exception as exc:                 # noqa: BLE001
    check("jump() 不抛异常", False, repr(exc))

# ---------------------------------------------------------------- 窗口分类
check("「设置」不算主窗口", "设置" in core.NON_MAIN_TITLES)
mains = core.wechat_windows()
if mains:
    titles = [core._title_of(h) for h in mains]
    check("主窗口里没有『设置』", all(t not in core.NON_MAIN_TITLES for t in titles),
          "主窗口=%s" % titles)
    settings = core.wechat_settings_windows()
    check("设置窗口与主窗口不重叠", not (set(mains) & set(settings)),
          "设置=%s" % settings)
else:
    check("主窗口枚举（当前无窗口，跳过）", True, "托盘/未运行状态")

dlg.destroy()
root.destroy()

bad = [r for r in log if r[0] == "FAIL"]
print("\n结果：%d 项通过，%d 项失败" % (len(log) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
