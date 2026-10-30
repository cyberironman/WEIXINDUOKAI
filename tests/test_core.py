# -*- coding: utf-8 -*-
"""核心逻辑单元测试。

全部测试都在临时目录里跑，**不碰用户真实的微信实例**。

运行：
    python -m pytest tests/ -v
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechatmulti import core, patcher, sandbox          # noqa: E402


# ---------------------------------------------------------------- 路径规划

def test_instance_home_and_data_layout(tmp_path):
    """实例目录和数据根必须分开 —— 否则删实例会连聊天记录一起删。"""
    core.set_paths(root=str(tmp_path), tool_home=str(tmp_path))
    inst = core.Instance(letter="A", data_root=r"D:\SomeChat\Data\A", version="4.1.15.56")
    assert inst.home.endswith(os.path.join("A"))
    assert inst.version_dir.endswith(os.path.join("A", "4.1.15.56"))
    assert inst.launcher.endswith(os.path.join("A", "Weixin.exe"))
    assert inst.profile.endswith(os.path.join("A", "Profile"))
    assert inst.data_root == r"D:\SomeChat\Data\A"
    assert inst.data_inside_home is False


def test_data_inside_home_detected(tmp_path):
    """数据根落在实例目录里要被认出来（危险情况）。"""
    core.set_paths(root=str(tmp_path), tool_home=str(tmp_path))
    inst = core.Instance(letter="A", data_root=str(tmp_path / "A" / "Data"),
                         version="4.1.15.56")
    assert inst.data_inside_home is True


def test_next_letter():
    items = [core.Instance(letter=x, data_root="d", version="v") for x in ("A", "B")]
    assert core.next_letter(items) == "C"
    assert core.next_letter([]) == "A"


# ---------------------------------------------------------------- 数据根

def test_write_data_root(tmp_path):
    prof = tmp_path / "Profile"
    core.make_profile_skeleton(str(prof))
    data = str(tmp_path / "Data" / "A")
    path = core.write_data_root(str(prof), data)
    assert os.path.isfile(path)
    content = open(path, "rb").read().decode("ascii")
    assert content == data.rstrip("\\")
    # 反斜杠形式，不能是斜杠
    assert "/" not in content


def test_make_profile_skeleton(tmp_path):
    prof = str(tmp_path / "Profile")
    core.make_profile_skeleton(prof)
    for sub in (r"AppData\Roaming", r"AppData\Local\Temp", "Desktop", "Documents"):
        assert os.path.isdir(os.path.join(prof, sub))


# ---------------------------------------------------------------- 启动脚本

def test_make_launcher_sets_isolation(tmp_path):
    """启动脚本必须把 USERPROFILE 等指到本实例，且路径用反斜杠。"""
    core.set_paths(root=str(tmp_path), tool_home=str(tmp_path))
    inst = core.Instance(letter="Q", data_root=str(tmp_path / "Data" / "Q"),
                         version="4.1.15.56")
    os.makedirs(inst.home, exist_ok=True)
    core.make_profile_skeleton(inst.profile)
    bat = core.make_launcher(inst)
    text = open(bat, "rb").read().decode("gbk", "ignore")
    assert "USERPROFILE" in text
    assert inst.profile in text
    # 启动器用 %WX_HOME% 变量引用（相对写法更稳，换目录不用重写）
    assert "%WX_HOME%\\Weixin.exe" in text
    # 路径一律用反斜杠
    assert "/" not in text.split("rem  install")[0]


def test_make_launcher_no_percent_format_bug(tmp_path):
    """批处理模板不能用 % 格式化 —— bat 里满是 %VAR%，用 % 格式必炸。"""
    core.set_paths(root=str(tmp_path), tool_home=str(tmp_path))
    inst = core.Instance(letter="W", data_root=str(tmp_path / "Data" / "W"),
                         version="4.1.15.56")
    os.makedirs(inst.home, exist_ok=True)
    core.make_profile_skeleton(inst.profile)
    bat = core.make_launcher(inst)
    text = open(bat, "rb").read().decode("gbk", "ignore")
    # 出现 "%~1" 这种参数引用是正常的；出现 "%%" 说明被 % 格式化过
    assert "%%" not in text


# ---------------------------------------------------------------- 实例清单

def test_instances_roundtrip(tmp_path):
    core.set_paths(root=str(tmp_path), tool_home=str(tmp_path))
    items = [core.Instance(letter="A", data_root=r"D:\d\A", version="4.1.15.56",
                            note="测试")]
    core.save_instances(items)
    back = core.load_instances()
    assert len(back) == 1
    assert back[0].letter == "A"
    assert back[0].data_root == r"D:\d\A"
    assert back[0].note == "测试"


# ---------------------------------------------------------------- 补丁器

def test_patcher_mask_holes():
    """重定位指令后面 4 字节位移要被挖空。

    ``48 8d`` 是两字节前缀（REX.W + lea），位移紧随其后，
    所以被挖空的是下标 2,3,4,5。
    """
    holes = patcher._mask_holes(b"\x48\x8d\x8d\x60\x01\x00\x00")
    assert set(holes) == {2, 3, 4, 5}
    # 一字节前缀（call rel32）的位移在 1,2,3,4
    holes2 = patcher._mask_holes(b"\xe8\x01\x02\x03\x04\x90")
    assert set(holes2) == {1, 2, 3, 4}


def test_patcher_sites_have_equal_length():
    """等长替换 —— 不改指令边界、不改跳转目标。"""
    for site in patcher.SITES:
        assert len(site.before) == len(site.after)


def test_patcher_needs_patch_is_false():
    """当前版本不需要打补丁。"""
    assert patcher.NEEDED is False


# ---------------------------------------------------------------- 安全护栏

def test_sandbox_rejects_real_paths():
    """真实实例路径必须被拒绝。"""
    for p in (r"D:\WeChatMulti\B", r"C:\Program Files\Tencent\Weixin", r"E:\WEIXIN"):
        with pytest.raises(RuntimeError):
            sandbox.assert_sandbox(p)


def test_sandbox_accepts_sandbox_paths():
    sb = sandbox.sandbox_root("unit_test")
    try:
        assert sandbox.assert_sandbox(sb)
        assert sandbox.assert_sandbox(os.path.join(sb, "sub", "deep"))
    finally:
        import shutil
        shutil.rmtree(sb, ignore_errors=True)


def test_sandbox_module_has_no_kill_helper():
    """护栏模块不许提供 kill 能力 —— 只读不杀。"""
    src = open(sandbox.__file__, "rb").read().decode("utf-8")
    assert "taskkill" not in src
    assert "TerminateProcess" not in src


# ---------------------------------------------------------------- 定位器

def test_finder_parses_version():
    """从版本库目录名里解析版本号。"""
    import re
    assert re.match(r"^\d+\.\d+\.\d+", "4.1.15.56")
    assert not re.match(r"^\d+\.\d+\.\d+", "Profile")


# ---------------------------------------------------------------- 窗口识别

def test_settings_titles_are_not_main_windows():
    """「设置」等标题必须被认定为非主窗口。

    微信的「设置」窗口类名和主窗口完全一样（都是 Qt51514QWindowIcon），
    只看类名会把设置页当主窗口 —— 箭头就会画到设置页上，
    表现为「指引挡住别的微信窗口」。
    """
    for t in ("设置", "設定", "Settings", "关于微信", "迁移与备份",
              "通用设置", "检查更新"):
        assert t in core.NON_MAIN_TITLES, "%r 应该被列为非主窗口标题" % t


def test_main_title_whitelist_does_not_swallow_wechat():
    """微信真正的窗口标题（WeChat / 微信）不能被误判成对话框。"""
    for t in ("WeChat", "微信", "微信A", "微信B"):
        assert t not in core.NON_MAIN_TITLES


def test_rects_overlap():
    """浮层避让用的矩形相交判断。"""
    from wechatmulti import gui
    a = (0, 0, 100, 100)
    assert gui._rects_overlap(a, (50, 50, 150, 150))
    assert gui._rects_overlap(a, (100, 100, 200, 200))     # 贴边算交叠
    assert not gui._rects_overlap(a, (500, 500, 600, 600), pad=0)


def test_pick_overlay_pos_avoids_wechat_windows():
    """浮层必须避开所有微信窗口 —— 用户抱怨过它挡住右边那排微信。"""
    from wechatmulti import gui
    # 微信窗口铺满屏幕中间大半区域
    occupied = [(578, 130, 1603, 945)]
    pos = gui.pick_overlay_pos(300, 92, (615, 904), occupied, 1920, 1080)
    box = (pos[0], pos[1], pos[0] + 300, pos[1] + 92)
    assert not gui._rects_overlap(box, occupied[0]), \
        "浮层 %s 压住了微信窗口 %s" % (box, occupied[0])


def test_pick_overlay_pos_stays_on_screen():
    """无论锚点在哪，浮层都不能跑到屏幕外。"""
    from wechatmulti import gui
    sw, sh = 1920, 1080
    for anchor in ((0, 0), (1919, 1079), (960, 540), (-32000, -32000)):
        pos = gui.pick_overlay_pos(300, 92, anchor, [], sw, sh)
        assert 0 <= pos[0] <= sw - 300
        assert 0 <= pos[1] <= sh - 92
