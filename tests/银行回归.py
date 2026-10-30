# -*- coding: utf-8 -*-
"""银行回归：利息折算、逾期加倍、复利滚存、信用分。

这些是「算错了没人发现」的高危区——账目错一位，玩家不会报错，只会
默默觉得这插件在坑人。所以每一个常数都要有对应的断言钉住。

跑法：`python tests/银行回归.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from core import utils                                  # noqa: E402
from core.bank import (                                 # noqa: E402
    CREDIT_ON_TIME, CREDIT_OVERDUE_TICK, DEFAULT_CREDIT,
    DEPOSIT_DAILY_RATE, SETTLE_MAX_HOURS, SHARK_CAP, SHARK_DAILY_POOL,
    Bank, next_tier, tier_of,
)
from core.store import blank_group, blank_user          # noqa: E402
from core.wallet import Wallet, total_wealth            # noqa: E402

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


def make(coins: int = 1000, cap: int = 100000):
    """造一个「有钱、无债、信用默认」的账户。"""
    u = blank_user("测试")
    u["coins"] = coins
    g = blank_group()
    w = Wallet(u, cap)
    return Bank(u, g, w), u, g, w


H = 3600

# ------------------------------------------------------------------ 档位 --
section("档位与信用分")

check("新账户默认信用分", DEFAULT_CREDIT == 60, DEFAULT_CREDIT)
check("信用 60 → 熟客", tier_of(60)["label"] == "熟客", tier_of(60))
check("信用 60 额度 500", tier_of(60)["loan_cap"] == 500, tier_of(60))
check("信用 60 日息 6%", tier_of(60)["rate"] == 6.0, tier_of(60))
check("信用 0 → 路人", tier_of(0)["label"] == "路人", tier_of(0))
check("信用 1200 → 家人", tier_of(1200)["label"] == "家人", tier_of(1200))
check("信用 -50 也兜得住", tier_of(-50)["label"] == "路人", tier_of(-50))
check("下一档从 60 是常客", next_tier(60)["label"] == "常客", next_tier(60))
check("封顶后没有下一档", next_tier(1000) is None, next_tier(1000))

# ------------------------------------------------------------------ 存款 --
section("存款与取款")

b, u, g, w = make(1000)
check("初始存款 0", int(u["bank"]["dep"]) == 0)

text = b.deposit(400)
check("存款后现金 600", w.coins == 605, f"{w.coins}（含 5 开户礼）")
check("存款后存款 400", int(u["bank"]["dep"]) == 400, u["bank"]["dep"])
check("存款不记成支出", int(u["stats"]["spent"]) == 0, u["stats"]["spent"])
check("首次存款送开户礼", "开户礼" in text, text)
check("开户礼到账", w.coins == 605, w.coins)

before = total_wealth(u)
b.deposit(100)
check("存取不改变总资产", total_wealth(u) == before, f"{before} -> {total_wealth(u)}")

b2, u2, _, w2 = make(50)
b2.deposit(500)
check("余额不足时全额存入", w2.coins == 5 and int(u2["bank"]["dep"]) == 50,
      f"{w2.coins},{u2['bank']['dep']}（5 是开户礼）")

b3, u3, _, w3 = make(500)
b3.deposit(200)
b3.withdraw(50)
check("取款回到现金", w3.coins == 355, f"{w3.coins}（含 5 开户礼）")
check("取款后存款", int(u3["bank"]["dep"]) == 150, u3["bank"]["dep"])
check("取空不报错", "取出" in b3.withdraw(99999))
check("取空后存款 0", int(u3["bank"]["dep"]) == 0, u3["bank"]["dep"])
check("无存款时取款给提示", "还没存款" in b3.withdraw(10))

# ------------------------------------------------------------------ 利息 --
section("存款利息")

b, u, g, w = make(0)
u["coins"] = 0
u["bank"]["dep"] = 1000
u["bank"]["dep_ts"] = utils.ts() - 12 * H
notes = b.settle()
# 1000 * 1.5% * 12/24 = 7.5 → 7
check("12 小时利息 7", w.coins == 7, w.coins)
check("利息有播报", any("利息" in n for n in notes), notes)
check("结算后 dep_ts 归零", int(u["bank"]["dep_ts"]) >= utils.ts() - 2, u["bank"]["dep_ts"])

b, u, g, w = make(0)
u["coins"] = 0
u["bank"]["dep"] = 1000
u["bank"]["dep_ts"] = utils.ts() - 100 * H
b.settle()
expect = int(1000 * DEPOSIT_DAILY_RATE * SETTLE_MAX_HOURS / 24.0)
check("单次结算最多按 48h 计息", w.coins == expect, f"{w.coins} != {expect}")

# 利息计入每日上限
b, u, g, w = make(0, cap=10)
u["coins"] = 0
u["bank"]["dep"] = 100000
u["bank"]["dep_ts"] = utils.ts() - 48 * H
notes = b.settle()
check("利息受每日上限截断", w.coins == 10, w.coins)
check("截断有说明", any("未发放" in n for n in notes), notes)

# 时间倒流（存档回滚）不应生息
b, u, g, w = make(0)
u["coins"] = 0
u["bank"]["dep"] = 1000
u["bank"]["dep_ts"] = utils.ts() + 3600
b.settle()
check("dep_ts 在未来时不生息", w.coins == 0, w.coins)

# ------------------------------------------------------------------ 贷款 --
section("信用贷")

b, u, g, w = make(100)
text = b.borrow(300)
check("放款到账", w.coins == 400, w.coins)
check("贷款计入负债", total_wealth(u) == 100, total_wealth(u))
check("免息券生效", u["bank"]["loan"]["rate"] == 0.0, u["bank"]["loan"])
check("免息券用掉", u["bank"]["coupon"] is False)
check("免息有提示", "免息" in text, text)

b, u, g, w = make(100)
b.borrow(301)
check("超 300 不用免息券", u["bank"]["loan"]["rate"] == 6.0, u["bank"]["loan"]["rate"])
check("超 300 券还在", u["bank"]["coupon"] is True)

b, u, g, w = make(100)
check("低于起借额被拒", "起步价" in b.borrow(50))
b.borrow(500)
check("打满额度可以借", u["bank"]["loan"]["principal"] == 500, u["bank"]["loan"])
check("超额被拒", "最多还能借" in b.borrow(100))
check("超额不改变本金", u["bank"]["loan"]["principal"] == 500)

# 循环贷：追加后期限不重置、利率取高
b2, u2, g2, w2 = make(0)
b2.borrow(100)
first_due = u2["bank"]["loan"]["due_ts"]
b2.borrow(200)
loan = u2["bank"]["loan"]
check("追加后本金累加", loan["principal"] == 300, loan["principal"])
check("追加不重置期限", loan["due_ts"] == first_due, loan["due_ts"])
check("追加后利率抬到档位值（防免息套利）", loan["rate"] == 6.0, loan["rate"])

# 还款：余额不足
b, u, g, w = make(0)
b.borrow(200)
w.spend(150)                       # 借来就赌光，模拟真实场景
coins_before = w.coins
text = b.repay(0)
check("余额不足时全额还款被拒", "只有" in text, text)
check("被拒不动账", w.coins == coins_before and u["bank"]["loan"] is not None,
      f"{w.coins},{u['bank']['loan']}")
b.repay(50)
check("可以小额部分还款", u["bank"]["loan"]["principal"] == 150, u["bank"]["loan"])

# 还款：全额结清 + 信用分
b, u, g, w = make(100)
b.borrow(300)
credit_before = b.credit
text = b.repay(0)
check("还款后清账", u["bank"]["loan"] is None, u["bank"]["loan"])
check("按时还清加信用分", b.credit == min(1000, credit_before + CREDIT_ON_TIME), b.credit)
check("结清有提示", "结清" in text, text)
check("已结清再还提示无贷款", "没有贷款" in b.repay(100))

# ------------------------------------------------------------------ 逾期 --
section("逾期")

b, u, g, w = make(0)
u["bank"]["loan"] = {
    "principal": 1000, "rate": 6.0, "start_ts": utils.ts() - 2 * 86400,
    "due_ts": utils.ts() + 5 * 86400, "accrued": 0,
    "ts": utils.ts() - 86400, "overdue": False,
}
b.settle()
check("正常期 24h 利息 60", u["bank"]["loan"]["accrued"] == 60, u["bank"]["loan"]["accrued"])

b, u, g, w = make(0)
u["bank"]["loan"] = {
    "principal": 1000, "rate": 6.0, "start_ts": utils.ts() - 2 * 86400,
    "due_ts": utils.ts() - 86400, "accrued": 0,
    "ts": utils.ts() - 86400, "overdue": False,
}
credit0 = b.credit
b.settle()
check("逾期 24h 利息翻倍 = 120", u["bank"]["loan"]["accrued"] == 120, u["bank"]["loan"]["accrued"])
check("逾期扣信用分", b.credit == credit0 - CREDIT_OVERDUE_TICK, b.credit)
check("逾期只扣一次分", (b.settle(), b.credit)[1] == credit0 - CREDIT_OVERDUE_TICK, b.credit)

# 签到自动扣息
b, u, g, w = make(500)
u["bank"]["loan"] = {
    "principal": 1000, "rate": 6.0, "start_ts": utils.ts() - 2 * 86400,
    "due_ts": utils.ts() - 86400, "accrued": 300,
    "ts": utils.ts(), "overdue": True,
}
notes = b.settle(from_checkin=True)
check("逾期签到自动扣息", w.coins == 200, w.coins)
check("扣息有播报", any("自动扣息" in n for n in notes), notes)
check("扣息后剩余计息", u["bank"]["loan"]["accrued"] == 0, u["bank"]["loan"]["accrued"])

# 回滚熔断：ts 退回到很久以前，也最多按 48h 算
b, u, g, w = make(0)
u["bank"]["loan"] = {
    "principal": 1000, "rate": 6.0, "start_ts": utils.ts() - 30 * 86400,
    "due_ts": utils.ts() - 10 * 86400, "accrued": 0,
    "ts": utils.ts() - 30 * 86400, "overdue": False,
}
b.settle()
acc = u["bank"]["loan"]["accrued"]
check("回滚熔断：利息不超过 48h 全逾期值", acc <= 240, acc)

# ------------------------------------------------------------------ 高利贷 --
section("地下钱庄")

b, u, g, w = make(0)
check("高利贷说明不带金额", "上限 3000" in b.borrow_shark(0))
check("超上限被拒", "最多放 3000" in b.borrow_shark(SHARK_CAP + 1))
b.borrow_shark(1000)
check("放款到账", w.coins == 1000, w.coins)
check("高利贷记负债", total_wealth(u) == 0, total_wealth(u))
check("不能二贷", "不做二贷" in b.borrow_shark(100))

# 日池
b, u, g, w = make(0)
g["global"]["shark_pool"] = {"day": utils.today(), "issued": SHARK_DAILY_POOL - 100}
check("日池不足时拒绝", "额度用完" in b.borrow_shark(500))
g["global"]["shark_pool"] = {"day": "2000-01-01", "issued": SHARK_DAILY_POOL}
check("跨天日池重置", "放款" in b.borrow_shark(500))

# 复利：跨天把利息滚进本金
b, u, g, w = make(0)
u["bank"]["shark"] = {
    "principal": 1000, "accrued": 0, "start_ts": utils.ts() - 86400,
    "due_ts": utils.ts() + 2 * 86400, "ts": utils.ts() - 86400,
    "overdue": False, "fold_day": str(utils.now().date()),
}
b.settle()
acc1 = u["bank"]["shark"]["accrued"]
check("同日结算利息 120（12%×24h）", acc1 == 120, acc1)
b.settle()
check("结算幂等：不重复计息", u["bank"]["shark"]["accrued"] == 120, u["bank"]["shark"]["accrued"])

b, u, g, w = make(0)
u["bank"]["shark"] = {
    "principal": 1000, "accrued": 120, "start_ts": utils.ts() - 2 * 86400,
    "due_ts": utils.ts() + 2 * 86400, "ts": utils.ts() - 86400,
    "overdue": False, "fold_day": "2000-01-01",
}
b.settle()
shark = u["bank"]["shark"]
check("跨天把利息滚进本金", shark["principal"] == 1120, shark["principal"])
check("跨天后 accrued 归零（已并入本金）", shark["accrued"] < 200, shark["accrued"])

# 逾期自动划扣：先存款后现金
b, u, g, w = make(100)
u["bank"]["dep"] = 500
u["bank"]["shark"] = {
    "principal": 1000, "accrued": 0, "start_ts": utils.ts() - 5 * 86400,
    "due_ts": utils.ts() - 3600, "ts": utils.ts(),
    "overdue": False, "fold_day": str(utils.now().date()),
}
notes = b.settle()
check("逾期先扣存款", int(u["bank"]["dep"]) == 0, u["bank"]["dep"])
check("存款不够再扣现金", w.coins == 0, w.coins)
check("自动划扣有播报", any("自动划扣" in n for n in notes), notes)

# 还清
b, u, g, w = make(2000)
b.borrow_shark(1000)
b.repay_shark(0)
check("高利贷还清", u["bank"]["shark"] is None, u["bank"]["shark"])
check("还清后有记录", len(u["bank"]["history"]) == 1, u["bank"]["history"])

# ------------------------------------------------------------------ 锁 ----
section("资金锁")

b, u, g, w = make(100)
check("无债不锁", b.locked() == "", b.locked())
b.borrow(100)
check("有贷款时锁红包", b.locked() == "贷款", b.locked())
b.repay(0)
check("还清后解锁", b.locked() == "", b.locked())
b.borrow_shark(100)
check("高利贷优先级更高", b.locked() == "高利贷", b.locked())

# ------------------------------------------------------------ 老存档兼容 --
section("老存档兼容")

b, u, g, w = make(100)
u["bank"]["loan"] = {}
u["bank"]["coupon"] = 1
b.settle()
check("空 dict 贷款被清理", u["bank"]["loan"] is None, u["bank"]["loan"])
check("coupon 数字 1 转布尔", u["bank"]["coupon"] is True, u["bank"]["coupon"])
check("无 credit 字段时按默认值", b.credit == DEFAULT_CREDIT, b.credit)

# ------------------------------------------------------------------ 汇总 --
print()
if FAIL:
    print(f"FAILED  {len(FAIL)} 条未通过 / {PASS + len(FAIL)} 条")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(1)

print(f"ALL PASS  {PASS} 条断言全过")
