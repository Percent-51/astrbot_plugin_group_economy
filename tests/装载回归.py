# -*- coding: utf-8 -*-
"""装载回归：把插件当 AstrBot 真的加载一次，再挨个命令问一遍。

为什么需要这一层
----------------
前三份测试都在测「逻辑」，但插件最常见的翻车方式是**根本没加载起来**：
相对导入写错、`Star` 基类没继承、装饰器参数不对、命令表里有个命令
忘了接分支。这些错在纯逻辑测试里一个都暴露不出来。

这里用假的 astrbot 模块替身，不装 AstrBot 也能把 main.py 完整跑一遍，
对每条命令断言「有回复、不是报错兜底」。

跑法：`python tests/装载回归.py`
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

PASS = 0
FAIL: list[str] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label}  {detail}")


def section(name: str) -> None:
    print(f"\n--- {name} ---")


# --------------------------------------------------------- astrbot 替身 ----
class _Logger:
    def debug(self, *a, **k):
        pass
    info = warning = error = debug


class _Filter:
    class EventMessageType:
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2

    @staticmethod
    def event_message_type(*_a, **_k):
        def deco(fn):
            return fn
        return deco


class _Star:
    def __init__(self, context=None):
        self.context = context


class _StarTools:
    @staticmethod
    def get_data_dir(_name):
        return Path(tempfile.mkdtemp(prefix="ge_test_"))


_REGISTERED: list[tuple] = []


def _register(*args, **_kwargs):
    def deco(cls):
        _REGISTERED.append((cls, args))
        return cls
    return deco


def _install_stubs() -> None:
    if "astrbot" in sys.modules:
        return
    root = types.ModuleType("astrbot")
    root.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = _Logger()
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.filter = _Filter
    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = _Star
    star.StarTools = _StarTools
    star.register = _register
    for name, mod in (
        ("astrbot", root), ("astrbot.api", api),
        ("astrbot.api.event", event), ("astrbot.api.star", star),
    ):
        sys.modules[name] = mod


_install_stubs()
if "ge" not in sys.modules:
    _pkg = types.ModuleType("ge")
    _pkg.__path__ = [str(_ROOT)]
    sys.modules["ge"] = _pkg
sys.path.insert(0, str(_ROOT))

import ge.main as ge_main  # noqa: E402


# ---------------------------------------------------------------- 加载 ----
section("加载")

check("模块导入成功", ge_main is not None)
check("有版本号", bool(ge_main.VERSION), ge_main.VERSION)
check("注册了插件类", len(_REGISTERED) == 1, _REGISTERED)
if _REGISTERED:
    cls, args = _REGISTERED[0]
    check("注册参数是 4 元组", len(args) == 4, args)
    check("注册名与目录名一致", args[0] == ge_main.PLUGIN_NAME, args[0])

plugin = ge_main.GroupEconomyPlugin.__new__(ge_main.GroupEconomyPlugin)
_Star.__init__(plugin, None)
plugin.config = {"initial_coins": 500, "daily_cap": 300, "checkin_reward": 50}
plugin.data_dir = Path(tempfile.mkdtemp(prefix="ge_data_"))
plugin.store = ge_main.Store(plugin.data_dir / "economy.json", initial_coins=500)
plugin._boot = asyncio.Event()
plugin._boot_task = None
plugin.llm = None
asyncio.run(plugin.store.load())
plugin._boot.set()
check("存档初始化成功", plugin.store.data.get("groups") == {}, plugin.store.data)

# ---------------------------------------------------------------- 解析 ----
section("命令解析")

check("冒号分割", ge_main.GroupEconomyPlugin._parse("银行：存款 100") == ("银行", "存款 100"),
      ge_main.GroupEconomyPlugin._parse("银行：存款 100"))
check("空格分割", ge_main.GroupEconomyPlugin._parse("炸弹 猜 50") == ("炸弹", "猜 50"),
      ge_main.GroupEconomyPlugin._parse("炸弹 猜 50"))
check("全角斜杠前缀", ge_main.GroupEconomyPlugin._parse("/签到") == ("签到", ""),
      ge_main.GroupEconomyPlugin._parse("/签到"))
check("空消息不炸", ge_main.GroupEconomyPlugin._parse("") == ("", ""))

unmapped = [k for k in ge_main.ALIASES.values() if k not in ge_main.SUPPORTED]
check("别名全部指向已支持命令", not unmapped, unmapped)

# ---------------------------------------------------------------- 路由 ----
section("命令路由")


class FakeEvent:
    def __init__(self, text: str, gid: str = "g1", uid: str = "u1", name: str = "甲"):
        self._text, self._gid, self._uid, self._name = text, gid, uid, name
        self.stopped = False

    def get_message_str(self):
        return self._text

    def get_group_id(self):
        return self._gid

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text


async def ask(text: str, uid: str = "u1", name: str = "甲", gid: str = "g1") -> str:
    out = ""
    async for item in plugin.on_message(FakeEvent(text, gid, uid, name)):
        out = item
    return out


ERROR_HINT = "算错了账"

cases = [
    ("经济", "菜单"),
    ("钱包", "钱包"),
    ("签到", "签到"),
    ("排行", "排行"),
    ("银行", "账户"),
    ("银行：规则", "银行规则"),
    ("银行：存款 100", "存款"),
    ("银行：取款 10", "取款"),
    ("银行：贷款 100", "贷款"),
    ("银行：还款", "还款"),
    ("银行：借高利贷 100", "借高利贷"),
    ("银行：还高利贷", "还高利贷"),
    ("红包", "红包列表"),
    ("红包：规则", "红包规则"),
    ("红包 发 100 3 恭喜", "发红包"),
    ("红包 抢", "抢红包"),
    ("彩票", "彩票盘口"),
    ("彩票：规则", "彩票规则"),
    ("彩票：刮 2", "刮刮乐"),
    ("彩票：买大 100", "下注"),
    ("彩票：注单", "注单"),
    ("炸弹", "炸弹状态"),
    ("炸弹：规则", "炸弹规则"),
    ("炸弹 开", "炸弹开局"),
    ("炸弹 猜 50", "炸弹猜数"),
    ("炸弹 弃", "炸弹弃局"),
    ("接龙", "接龙看"),
    ("接龙：规则", "接龙规则"),
    ("接龙 开 深夜的便利店", "接龙开局"),
    ("接龙 灯忽然灭了", "接一句"),
    ("接龙 收尾", "接龙收尾"),
    ("猜猜猜", "猜词状态"),
    ("猜猜猜：规则", "猜词规则"),
    ("猜猜猜 开 歌名", "猜词开局"),
    ("猜猜猜 提示", "猜词提示"),
    ("猜猜猜 放弃", "猜词放弃"),
    ("海龟汤", "汤面"),
    ("海龟汤：规则", "汤规则"),
    ("海龟汤 开", "汤开局"),
    ("海龟汤 已知", "汤已知"),
    ("海龟汤 揭晓", "汤揭晓"),
    ("剧场", "剧场无主题"),
    ("剧场：规则", "剧场规则"),
    ("剧场 抢最后一份", "剧场无模型"),
    ("帮助", "帮助别名"),
    ("/签到", "斜杠签到"),
    ("随便说句话", "非命令静默"),
]

for text, label in cases:
    try:
        reply = asyncio.run(ask(text))
    except Exception as exc:  # 任何异常都算失败，不该静默
        check(f"{label} 不抛异常", False, f"{type(exc).__name__}: {exc}")
        continue
    if label == "非命令静默":
        check("非命令不回复", reply == "", repr(reply))
        continue
    check(f"{label} 有回复", bool(reply), repr(reply))
    check(f"{label} 不是错误兜底", ERROR_HINT not in str(reply), str(reply)[:60])

# 私聊也能用（没有群号时用固定桶承载）
check("私聊可用", bool(asyncio.run(ask("钱包", gid=""))))

# 重复签到只给一次钱
first = asyncio.run(ask("签到"))
second = asyncio.run(ask("签到"))
check("重复签到有提示", "已经签到" in second, second[:30])
check("重复签到不再加钱", "签到成功" not in second, second[:30])

# 每日上限配置能生效
plugin.config["daily_cap"] = 10
asyncio.run(ask("钱包", uid="u9", name="上限哥"))
plugin.config["daily_cap"] = 10
out = asyncio.run(ask("签到", uid="u9", name="上限哥"))
check("上限较小时被截断", "只到账 10" in out, out[:60])
plugin.config["daily_cap"] = 300

# ---------------------------------------------------------------- 汇总 ----
print()
if FAIL:
    print(f"FAILED  {len(FAIL)} 条未通过 / {PASS + len(FAIL)} 条")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(1)

print(f"ALL PASS  {PASS} 条断言全过")
