"""代理转发开关（主应用）。

默认开启，保持 Docker / web.py / 多开实例等既有部署的行为不变：
它们都是独立进程，没有界面去点开关。

只有桌面应用会在启动时显式调用 set_enabled(False)，实现"打开桌面端时代理
默认关闭，点击「开启代理」后才转发"。状态只存在内存中，因此每次启动都回到
关闭状态。
"""

_enabled = True


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> bool:
    global _enabled
    _enabled = bool(value)
    return _enabled
