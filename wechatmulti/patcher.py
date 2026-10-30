# -*- coding: utf-8 -*-
"""单实例限制补丁器 —— **历史模块，当前版本不使用**。

⚠️ **状态：已停用。** 保留它只是为了记录早期方案、并给老版本留个后路。
当前版本（0.1.0）**不打任何二进制补丁**，原因见下。

为什么停用
----------
早期版本必须给微信启动器打二进制补丁，绕过「本机只允许一个实例」的检查。
补丁点是三个跳转：

============  ======================  ==========================
补丁点        原始指令                 改后
============  ======================  ==========================
①             ``jne  +0x1a``          ``jmp  +0x1a``（无条件跳转）
②             ``test al,al / jnz +9``  ``mov al,1 / jmp +9``（永远走「已存在」分支）
③             ``jnz  <rel32>``         ``nop / jmp <rel32>``（跳到同一目标）
============  ======================  ==========================

三处都是**等长替换**，不改变任何指令边界、也不改变跳转目标。

但实测（微信 4.1.15.56，2026-10-30，在 ``_sandbox_verify/`` 沙箱里两轮交叉验证）：

* **官方启动器原样就能拉起独立实例** —— 主实例在跑的情况下照样起得来；
* 打补丁的版本**同样**能起来，但没有任何额外收益。

也就是说，新版微信的单实例判定已经改为**基于用户目录**，而不是全局进程检查。
只要把 ``USERPROFILE`` 指到独立目录，微信就认为「这是一个新用户」，直接放行。

⚠️ 而且这三个补丁特征是**取自 4.1.15.13 旧壳**的。新版壳里同样的字节上下文
已经不是同一段逻辑，把旧特征往新版上套属于「改坏启动流程」——
早期「打完补丁启动即崩」的结论，其实是被实验污染的误判
（当时本机有多个微信实例在跑，单实例门把新启动全挡住了）。

所以现在：**原样复制官方启动器，靠环境隔离实现多开。**
好处是合规风险更低、不再随微信版本失效、仓库不含任何腾讯文件。

保留本模块的原因
----------------
万一将来某个旧版微信确实需要补丁（比如 4.1.15 之前），
这里的版本自适应匹配逻辑（掩码挖空重定位位移）仍然有价值。

合规声明
--------
本模块只修改**用户自己电脑上那一份**微信客户端的启动器，
不重新分发任何腾讯的文件，不上传任何数据。
是否使用、以及由此产生的后果，由使用者自行承担。
"""
from __future__ import annotations

import os
import shutil

__all__ = [
    "PatchSite", "SITES", "PatchError", "find_sites", "is_patched",
    "patch_bytes", "patch_launcher", "NEEDED",
]

#: 当前版本是否需要打补丁 —— **False**。保留常量是为了让调用方
#: （以及未来的兼容性判断）有个明确的开关，而不是靠「import 成功与否」。
NEEDED = False


class PatchError(RuntimeError):
    """补丁无法定位或目标文件不合法。"""


# 会让「相对位移」随编译而变的指令前缀：前缀之后的 4 字节位移要挖空
_REL_PREFIX = [
    b"\xe8", b"\xe9",                                     # call rel32 / jmp rel32
    b"\x0f\x85", b"\x0f\x84", b"\x0f\x8c", b"\x0f\x8e",   # jcc rel32
    b"\x0f\x8d", b"\x0f\x8f", b"\x0f\x83", b"\x0f\x86",
    b"\x0f\x87", b"\x0f\x89", b"\x0f\x8a", b"\x0f\x8b",
    b"\x48\x8d", b"\x48\x8b", b"\x48\x89", b"\x48\x83",   # lea/mov/cmp [rip+x]
    b"\x4c\x8d", b"\x4c\x8b", b"\x4d\x8b",
    b"\x44\x8b", b"\x44\x8d", b"\x45\x8b",
]


def _mask_holes(seg: bytes) -> set:
    """返回 seg 里「必须挖空」的字节下标集合。

    挖空规则：遇到重定位型指令前缀，就把它后面跟的 4 字节位移挖掉。
    """
    holes = set()
    n = len(seg)
    for i in range(n):
        for pre in _REL_PREFIX:
            if seg[i:i + len(pre)] != pre:
                continue
            d = i + len(pre)
            if d + 4 <= n:
                holes.update(range(d, d + 4))
            break
    return holes


class PatchSite:
    """一个补丁点。

    ``pre``  = 补丁点之前的 16 字节特征（取自已知可用的微信版本）
    ``post`` = 补丁点之后的 16 字节特征（用于二次确认，降低误匹配概率）
    ``before`` / ``after`` = 要替换的字节（等长）
    """

    def __init__(self, name: str, pre: bytes, before: bytes, after: bytes, post: bytes):
        self.name = name
        self.pre = pre
        self.before = before
        self.after = after
        self.post = post
        # 特征里的坑：只挖重定位位移（补丁点在 pre 之后，不在 pre 里面）
        self._pre_holes = _mask_holes(pre)
        self._post_holes = _mask_holes(post)

    # -- 内部：带掩码比较 ------------------------------------------------
    def _match_at(self, buf: bytes, pos: int) -> bool:
        """判断 pos 处是不是这个补丁点。

        补丁点那几字节允许是「原始」也允许是「已打补丁」两种形态 ——
        这样同一套特征对打过补丁的启动器一样能定位，
        从而可以判断一份启动器当前是什么状态。
        """
        for k, want in enumerate(self.pre):
            if k in self._pre_holes:
                continue
            if buf[pos + k] != want:
                return False
        mid = pos + len(self.pre)
        patched = bytes(buf[mid:mid + len(self.after)])
        if patched not in (self.before, self.after):
            return False
        base = mid + len(self.before)
        for k, want in enumerate(self.post):
            if k in self._post_holes:
                continue
            if buf[base + k] != want:
                return False
        return True

    def _scan(self, buf: bytes) -> list:
        span = len(self.pre) + len(self.before) + len(self.post)
        hits = []
        for i in range(0, max(0, len(buf) - span)):
            if self._match_at(buf, i):
                hits.append(i + len(self.pre))
        return hits

    def __repr__(self):
        return "<PatchSite %s>" % self.name


# --------------------------------------------------------------------------
# 三个补丁点。pre/post 取自微信 4.1.13 的启动器（已验证可用）。
# --------------------------------------------------------------------------
SITES = [
    PatchSite(
        name="single-instance-gate-1",
        pre=bytes.fromhex("48 8d 8d 60 01 00 00 e8 f2 98 00 00 90 45 84 ff"),
        before=bytes.fromhex("75"),
        after=bytes.fromhex("eb"),
        post=bytes.fromhex("1a 45 31 e4 48 8d 95 60 01 00 00 31 c9 e8 1b 1c"),
    ),
    PatchSite(
        name="single-instance-gate-2",
        pre=bytes.fromhex("41 5e 41 5f 5d c3 53 48 83 ec 20 e8 e6 fd ff ff"),
        before=bytes.fromhex("84 c0 75 09"),
        after=bytes.fromhex("b0 01 eb 09"),
        post=bytes.fromhex("89 c3 e8 9b fb ff ff 89 d8 48 83 c4 20 5b c3 cc"),
    ),
    PatchSite(
        name="single-instance-gate-3",
        pre=bytes.fromhex("00 fe ff ff ff 40 b6 01 48 83 3d 8f 2d 26 00 00"),
        before=bytes.fromhex("0f 85"),
        after=bytes.fromhex("90 e9"),
        post=bytes.fromhex("59 03 00 00 48 8b 0d 12 b3 28 00 48 85 c9 74 12"),
    ),
]


def find_sites(buf: bytes) -> dict:
    """在启动器字节里定位全部补丁点。返回 {name: 偏移}。

    每个点都要求**唯一命中**，命中 0 个或多个都会抛 PatchError ——
    宁可报错，也不要把用户微信改坏。
    """
    found = {}
    problems = []
    for site in SITES:
        hits = site._scan(buf)
        if len(hits) == 1:
            found[site.name] = hits[0]
        elif not hits:
            problems.append("%s：没找到（微信版本可能变化太大，需要更新特征）" % site.name)
        else:
            problems.append("%s：匹配到 %d 处，无法确定改哪里" % (site.name, len(hits)))
    if problems:
        raise PatchError("无法定位单实例检查点：\n  - " + "\n  - ".join(problems))
    return found


def is_patched(buf: bytes) -> bool:
    """这份启动器是不是已经打过补丁了。"""
    sites = find_sites(buf)
    return all(
        buf[sites[site.name]:sites[site.name] + len(site.after)] == site.after
        for site in SITES
    )


def patch_bytes(buf: bytes) -> bytes:
    """返回打好补丁的字节副本。"""
    if is_patched(buf):
        return buf
    sites = find_sites(buf)
    out = bytearray(buf)
    for site in SITES:
        off = sites[site.name]
        if bytes(out[off:off + len(site.before)]) != site.before:
            raise PatchError("%s：目标字节与预期不符" % site.name)
        out[off:off + len(site.after)] = site.after
    return bytes(out)


def patch_launcher(src: str, dst: str) -> dict:
    """把 ``src`` 微信启动器复制到 ``dst`` 并打好补丁。

    返回 ``{"offsets": {...}, "already": bool}``。
    """
    if not os.path.isfile(src):
        raise PatchError("找不到微信启动器：%s" % src)
    with open(src, "rb") as fh:
        buf = fh.read()

    already = is_patched(buf)
    out = patch_bytes(buf)

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(out)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass

    try:
        offsets = find_sites(buf)
    except PatchError:
        offsets = {}
    return {"offsets": offsets, "already": already, "size": len(out)}
