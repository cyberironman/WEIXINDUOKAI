# -*- coding: utf-8 -*-
"""``python -m wechatmulti`` 入口。

不带参数 = 打开管家；``--install`` = 打开安装器。
"""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--install", "install"):
        from .installer import main as install_main
        return install_main()
    from .gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
