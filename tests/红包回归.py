# -*- coding: utf-8 -*-
"""红包回归：金额守恒、过期退款、套现锁。

红包是本插件里唯一「钱在群友之间流动」的功能，也是唯一可能被用来
套现的入口。这两件事都必须钉死：
1. 发出去的总额 = 每个人抢到的 + 过期退回的（一分不能多、一分不能少）
2. 有欠款时不许发包，否则「借钱 → 发红包 → 领走」就能把贷款变白送

跑法：`python tests/红包回归.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from core import utils                                      # noqa: E402
from core.bank import Bank                                  # noqa: E402
from core.redpacket import (                                # noqa: E402
    MAX_ACTIVE_PER_GROUP, TTL_SEC, RedPacket, split_lucky,
)
from core.store import blank_group, blank_user               # noqa: E402
from core.wallet import Wallet                              # noqa: E402

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


def person(coins: int = 1000, name: str = "甲"):
    u = blank_user(name)
    u["coins"] = coins
    return u, Wallet(u, 100000)


def world(*names):
    """造一个群，返回 (group, {名字: (user, wallet)})。"""
    g = blank_group()
    pool = {}
    for nm in names:
        u, w = person(1000, nm)
        g["users"][nm] = u
        pool[nm] = (u, w)
    return g, pool


# ---------------------------------------------------------------- 拆分 ----
section("拼手气拆分")

for total, count in [(100, 5), (10, 10), (7, 3), (100, 1), (1000, 20)]:
    parts = split_lucky(total, count)
    check(f"{total}/{count} 份数正确", len(parts) == count, len(parts))
    check(f"{total}/{count} 合计精确", sum(parts) == total, sum(parts))
    check(f"{total}/{count} 每份至少 1", all(p >= 1 for p in parts), parts)

bad = 0
for _ in range(500):
    parts = split_lucky(500, 7)
    if sum(parts) != 500 or min(parts) < 1:
        bad += 1
check("500 次随机拆分全部守恒", bad == 0, f"{bad} 次异常")
check("0 份返回空", split_lucky(100, 0) == [])
check("0 元返回空", split_lucky(0, 5) == [])

# ---------------------------------------------------------------- 发包 ----
section("发包")

g, people = world("甲", "乙", "丙")
ua, wa = people["甲"]
rp = RedPacket(ua, g, wa, "甲")

check("金额太小被拒", "至少要" in rp.send(5, 1))
check("金额太大被拒", "最多" in rp.send(200000, 1))
check("份数超过上限被拒", "最多拆" in rp.send(100, 999))
check("总额不够分被拒", "分不了" in rp.send(10, 20))
check("余额不足被拒", "只有" in rp.send(99999, 1))

text = rp.send(100, 5, "恭喜发财")
check("发包扣钱", wa.coins == 900, wa.coins)
check("发包有播报", "100 堡贝 / 5 份" in text, text[:50])
check("祝福语带上", "恭喜发财" in text, text)
check("落盘一个红包", len(g["redpackets"]) == 1, list(g["redpackets"]))

# ---------------------------------------------------------------- 抢包 ----
section("抢包")

ub, wb = people["乙"]
uc, wc = people["丙"]
rp_b = RedPacket(ub, g, wb, "乙")
rp_c = RedPacket(uc, g, wc, "丙")

check("自己不能抢自己的", "不能自己抢" in rp.grab())
text = rp_b.grab()
check("抢到有钱", wb.coins > 1000, wb.coins)
check("抢包有提示", "抢到" in text, text[:40])
check("不能重复抢", "已经抢过" in rp_b.grab())

rid = list(g["redpackets"])[0]
check("记录领取人", "乙" in g["redpackets"][rid]["taken"], g["redpackets"][rid]["taken"])

rp_c.grab()
check("两人抢完还剩 3 份", len(g["redpackets"][rid]["shares"]) == 3,
      g["redpackets"][rid]["shares"])

# 金额守恒
taken_sum = sum(g["redpackets"][rid]["taken"].values())
left_sum = sum(g["redpackets"][rid]["shares"])
check("已抢 + 剩余 = 总额", taken_sum + left_sum == 100, f"{taken_sum}+{left_sum}")

# ---------------------------------------------------------------- 抢完 ----
section("抢完与手气王")

g, people = world("甲", "乙", "丙")
ua, wa = people["甲"]
rp = RedPacket(ua, g, wa, "甲")
rp.send(30, 2)
rp_b = RedPacket(people["乙"][0], g, people["乙"][1], "乙")
rp_c = RedPacket(people["丙"][0], g, people["丙"][1], "丙")
rp_b.grab()
text = rp_c.grab()
check("最后一个有结算语", "手气王" in text, text)
check("抢完自动移除", len(g["redpackets"]) == 0, list(g["redpackets"]))
total_back = (people["乙"][1].coins - 1000) + (people["丙"][1].coins - 1000)
check("两人合计拿回 30", total_back == 30, total_back)
check("手气王已记录", bool((g["global"].get("rp_king") or {}).get("name")),
      g["global"].get("rp_king"))

# ---------------------------------------------------------------- 过期 ----
section("过期退款")

g, people = world("甲", "乙")
ua, wa = people["甲"]
rp = RedPacket(ua, g, wa, "甲")
rp.send(100, 5)
RedPacket(people["乙"][0], g, people["乙"][1], "乙").grab()
left_after = wa.coins
rid = list(g["redpackets"])[0]
remain = sum(g["redpackets"][rid]["shares"])

g["redpackets"][rid]["ts"] = utils.ts() - TTL_SEC - 60
notes = rp.sweep()
check("过期有播报", any("过期" in n for n in notes), notes)
check("剩余退回发包人", wa.coins == left_after + remain, f"{left_after}+{remain} -> {wa.coins}")
check("过期后清包", len(g["redpackets"]) == 0, list(g["redpackets"]))

# ---------------------------------------------------------------- 套现 ----
section("防套现")

g, people = world("甲", "乙")
ua, wa = people["甲"]
b = Bank(ua, g, wa)
b.borrow(300)
rp = RedPacket(ua, g, wa, "甲")
text = rp.send(100, 2)
check("有贷款不能发包", "先还上" in text, text)
check("被拒时不扣钱", wa.coins == 1300, wa.coins)   # 1000 起始 + 300 放款

b.repay(0)
ua["coins"] = 1000
text = rp.send(100, 2)
check("还清后可以发包", "红包" in text and "先还上" not in text, text[:40])

# 高利贷也拦
g, people = world("甲")
ua, wa = people["甲"]
b = Bank(ua, g, wa)
b.borrow_shark(300)
check("高利贷也拦发包", "先还上" in RedPacket(ua, g, wa, "甲").send(100, 1))

# ------------------------------------------------------------ 并发上限 ----
section("并发上限")

g, people = world("甲", "乙", "丙", "丁")
for nm in ("甲", "乙", "丙", "丁"):
    people[nm][0]["coins"] = 1000
texts = [RedPacket(people[nm][0], g, people[nm][1], nm).send(50, 2)
         for nm in ("甲", "乙", "丙", "丁")]
check(f"最多挂 {MAX_ACTIVE_PER_GROUP} 个", len(g["redpackets"]) == MAX_ACTIVE_PER_GROUP,
      len(g["redpackets"]))
check("超出的被拒", any("先抢完再发" in t for t in texts), texts[-1][:40])

# ---------------------------------------------------------------- 汇总 ----
print()
if FAIL:
    print(f"FAILED  {len(FAIL)} 条未通过 / {PASS + len(FAIL)} 条")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(1)

print(f"ALL PASS  {PASS} 条断言全过")
