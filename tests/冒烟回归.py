# -*- coding: utf-8 -*-
"""冒烟回归：不依赖 AstrBot 与任何第三方库，CI 里直接 `python tests/冒烟回归.py`。

覆盖内核三层：store（原子写 / 编码回退 / 取不到就建）、
wallet（日上限截断 / 零和转移 / 余额保护）、utils（展示格式化）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from core import utils                      # noqa: E402
from core.store import Store, blank_user    # noqa: E402
from core.wallet import Wallet, total_wealth  # noqa: E402

PASS = 0
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label}  {detail}")


def section(name: str) -> None:
    print(f"\n--- {name} ---")


# ---------------------------------------------------------------- utils ----
section("utils")

check("fmt_coins 小值原样", utils.fmt_coins(999) == "999", utils.fmt_coins(999))
check("fmt_coins 万级", utils.fmt_coins(12345) == "1.23万", utils.fmt_coins(12345))
check("fmt_coins 亿级", utils.fmt_coins(123456789) == "1.23亿", utils.fmt_coins(123456789))
check("fmt_coins 负数不崩", utils.fmt_coins(-5000) == "-5000", utils.fmt_coins(-5000))
check("duration_cn 天", "天" in utils.duration_cn(90000), utils.duration_cn(90000))
check("duration_cn 分钟", utils.duration_cn(120) == "2分钟", utils.duration_cn(120))
check("today 是 10 位日期", len(utils.today()) == 10, utils.today())

# --------------------------------------------------------------- wallet ----
section("wallet")

u = blank_user("测试")
w = Wallet(u, daily_cap=100)

g1, c1 = w.earn(60)
check("首次 earn 全额到账", (g1, c1) == (60, False), f"{g1},{c1}")
check("earn 后余额", w.coins == 60, w.coins)
check("earned_today 记录", w.earned_today == 60, w.earned_today)

g2, c2 = w.earn(80)
check("超上限被截断", (g2, c2) == (40, True), f"{g2},{c2}")
check("截断后余额", w.coins == 100, w.coins)
check("截断后剩余额度 0", w.remaining_today == 0, w.remaining_today)

g3, c3 = w.earn(10)
check("额度用尽不再发放", (g3, c3) == (0, True), f"{g3},{c3}")

g4, c4 = w.earn(500, ignore_cap=True)
check("ignore_cap 绕过上限", (g4, c4) == (500, False), f"{g4},{c4}")
check("ignore_cap 不计入 earned", w.earned_today == 100, w.earned_today)

w.add(50)
check("add 零和转移不加额度", w.coins == 650 and w.earned_today == 100, f"{w.coins},{w.earned_today}")

before = w.coins
check("spend 余额不足返回 False", w.spend(99999) is False)
check("spend 失败不动账", w.coins == before, w.coins)
check("spend 正常扣款", w.spend(150) is True and w.coins == before - 150, w.coins)
check("spend 记录支出", u["stats"]["spent"] == 150, u["stats"]["spent"])

w2 = Wallet(blank_user("甲"), 300)
w2.add(100)
taken = w2.force_deduct(250)
check("force_deduct 实际扣款等于余额", taken == 100, taken)
check("force_deduct 后归零", w2.coins == 0, w2.coins)
check("force_deduct 对空账户返回 0", w2.force_deduct(10) == 0)

check("daily_cap=0 视为不限", Wallet(blank_user(), 0).earn(99999)[1] is False)

# 破产
w3 = Wallet(blank_user("乙"), 300)
check("初始破产次数 0", w3.bankrupt_times == 0)
w3.mark_bankrupt()
w3.mark_bankrupt()
check("破产计数累加", w3.bankrupt_times == 2, w3.bankrupt_times)

# 总资产
u4 = blank_user("丙")
u4["coins"] = 500
u4["bank"]["dep"] = 1000
check("总资产 = 现金 + 存款", total_wealth(u4) == 1500, total_wealth(u4))
u4["bank"]["loan"] = {"principal": 300, "interest": 30}
check("总资产扣除负债", total_wealth(u4) == 1170, total_wealth(u4))

# ---------------------------------------------------------------- store ----
section("store")

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "economy.json"
    s = Store(p)

    import asyncio

    asyncio.run(s.load())
    check("空文件加载得到空结构", s.data.get("groups") == {}, s.data)

    g = s.group("123")
    check("group 自动创建", isinstance(g, dict) and "users" in g, list(g))
    uu = s.user("123", "u1", "张三")
    check("user 自动创建并记名", uu["name"] == "张三", uu.get("name"))
    check("user 默认 coins 为 0", uu["coins"] == 0)
    check("user bank 字段齐全", set(blank_user()["bank"]) <= set(uu["bank"]), list(uu["bank"]))

    # 同名再取不重建
    same = s.user("123", "u1")
    check("重复取 user 返回同一对象", same is uu)

    # 改名
    renamed = s.user("123", "u1", "李四")
    check("传 name 会更新昵称", renamed["name"] == "李四", renamed["name"])

    uu["coins"] = 777
    s.save()
    check("落盘文件存在", p.is_file())

    raw = p.read_text(encoding="utf-8")
    check("落盘是合法 JSON", json.loads(raw)["groups"]["123"]["users"]["u1"]["coins"] == 777)
    check("落盘无 CRLF", "\r\n" not in raw)

    # 重新加载
    s2 = Store(p)
    asyncio.run(s2.load())
    check("重载后数据还在", s2.user("123", "u1")["coins"] == 777)

    # BOM 容错
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    s3 = Store(p)
    asyncio.run(s3.load())
    check("带 BOM 的存档可读", s3.user("123", "u1")["coins"] == 777)

    # 坏文件不炸
    p.write_text("{坏掉的 json", encoding="utf-8")
    s4 = Store(p)
    asyncio.run(s4.load())
    check("坏存档降级为空结构", s4.data.get("groups") == {}, s4.data)

    # 原子写不留 .tmp
    p.write_text("{}", encoding="utf-8")
    s5 = Store(p)
    asyncio.run(s5.load())
    s5.user("g", "u")
    s5.save()
    leftovers = list(Path(td).glob("*.tmp"))
    check("原子写不留临时文件", not leftovers, [x.name for x in leftovers])

    # 事务
    async def _tx():
        s6 = Store(p)
        await s6.load()
        async with s6.transaction():
            s6.user("g", "u2")["coins"] = 42

    asyncio.run(_tx())
    s7 = Store(p)
    asyncio.run(s7.load())
    check("事务写入可读回", s7.user("g", "u2")["coins"] == 42, s7.user("g", "u2")["coins"])

# --------------------------------------------------------------- 汇总 ----
print()
if FAIL:
    print(f"FAILED  {len(FAIL)} 条未通过 / {PASS + len(FAIL)} 条")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(1)

print(f"ALL PASS  {PASS} 条断言全过")
