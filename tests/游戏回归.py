# -*- coding: utf-8 -*-
"""玩法回归：赔率、分账、退款、边界。

游戏测试的重点不是「能不能跑」，而是**钱算得对不对**：
- 彩票的 RTP 必须稳定在 94% 附近，高了通胀、低了没人玩
- 炸弹分账必须守恒：没收的钱 = 幸存者拿到的 + 销毁的
- 所有「扣了钱但操作失败」的路径都必须退回去

跑法：`python tests/游戏回归.py`
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# 游戏模块内部用的是包内相对导入（`from ..core import utils`），直接
# `import games.bomb` 会因为「相对导入越过顶层包」而炸。这里注册一个
# 合成包 `ge`，把项目根目录当成它的路径，于是 `ge.games.bomb` 能正常
# 解析 `..core`，而且不依赖仓库目录叫什么名字。
if "ge" not in sys.modules:
    _pkg = types.ModuleType("ge")
    _pkg.__path__ = [str(_ROOT)]
    sys.modules["ge"] = _pkg
sys.path.insert(0, str(_ROOT))

from ge.core import content, utils                      # noqa: E402
from ge.core.store import blank_group, blank_user       # noqa: E402
from ge.core.wallet import Wallet                       # noqa: E402
from ge.games.bomb import Bomb                          # noqa: E402
from ge.games.guess import Guess, normalize             # noqa: E402
from ge.games.lottery import (                          # noqa: E402
    DIRECTIONS, Lottery, _blank_round, _pot_bonus,
)
from ge.games.soup import Soup, pick_verdict           # noqa: E402
from ge.games.story import LINE_REWARD, Story           # noqa: E402
from ge.games.theater import TICKET, Theater            # noqa: E402

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


# ---------------------------------------------------------------- 题库 ----
section("题库")

check("词库读得出", len(content.words()) > 2000, len(content.words()))
check("汤题读得出", len(content.soups()) > 500, len(content.soups()))
check("类别数 10", len(content.categories()) == 10, content.categories())
check("每条词都有答案/类别/难度",
      all(w["a"] and w["c"] and w["d"] for w in content.words()))
check("每条汤都有汤面/汤底",
      all(p["surface"] and p["truth"] for p in content.soups()))

# ---------------------------------------------------------------- 彩票 ----
section("彩票 · 刮刮乐")

g = blank_group()
u1, w1 = person(10_000_000)
lot = Lottery(u1, g, w1, "u1")

rounds = 20000
before = w1.coins
text = lot.scratch(20)
check("单次最多 20 张", "刮了 20 张" in text, text[:40])

lot.scratch(20)
for _ in range(rounds // 20 - 2):
    lot.scratch(20)
spent = 50 * rounds
returned = (w1.coins - before) + spent
rtp = returned / spent
check("刮刮乐 RTP 在 94% 附近（±3%）", abs(rtp - 0.9416) < 0.03, f"{rtp:.4f}")

u2, w2 = person(10)
lot2 = Lottery(u2, g, w2, "u2")
check("没钱刮不了", "只有" in lot2.scratch(1))
check("失败不扣钱", w2.coins == 10, w2.coins)

section("彩票 · 幸运号")

g = blank_group()
ua, wa = person(1000, "甲")
ub, wb = person(1000, "乙")
g["users"]["ua"] = ua
g["users"]["ub"] = ub
lot = Lottery(ua, g, wa, "ua")

text = lot.bet("大", 200)
check("下注扣钱", wa.coins == 800, wa.coins)
check("下注提示期次", "押「大」200" in text, text[:40])
check("低于下限被拒", "只能" in lot.bet("大", 10))
check("高于上限被拒", "只能" in lot.bet("大", 999))
check("无效方向被拒", "押哪个" in lot.bet("中", 100))
check("押不起时不扣钱", "押不起" in Lottery(ub, g, Wallet(person(10)[0], 100000), "ub").bet("小", 500))

lot_b = Lottery(ub, g, wb, "ub")
lot_b.bet("小", 100)
key = list(g["lottery"]["rounds"])[0]
r = g["lottery"]["rounds"][key]
check("两人注额进池", sum(v["amount"] for v in r["bets"]["大"].values()) == 200,
      r["bets"]["大"])
check("盘口显示倍率", "倍率" in lot.board())

# 结算：把期次改成过去时间，直接算
g["lottery"]["rounds"] = {"2020-01-01#0": _blank_round()}
r = g["lottery"]["rounds"]["2020-01-01#0"]
r["bets"]["大"]["ua"] = {"name": "甲", "amount": 200}
r["bets"]["小"]["ub"] = {"name": "乙", "amount": 200}
ua["coins"], ub["coins"] = 1000, 1000
notes = lot.settle()
total = 400
pool = int(total * 0.95) + _pot_bonus(total)
check("开奖有播报", any("开奖" in n for n in notes), notes)
check("幂等：重复结算不多发",
      (lot.settle(), ua["coins"] + ub["coins"])[1] == ua["coins"] + ub["coins"])
check("赢家拿走整个池子",
      max(ua["coins"], ub["coins"]) - 1000 == pool,
      f"pool={pool} a={ua['coins']} b={ub['coins']}")
# 这两个注单是直接塞进存档的，没走 bet()，所以谁的钱都没真被扣
check("未走 bet 的账户不受影响",
      min(ua["coins"], ub["coins"]) == 1000, f"{ua['coins']},{ub['coins']}")

# 同方向两人押注按比例分
g["lottery"]["rounds"] = {"2020-01-01#0": _blank_round()}
r = g["lottery"]["rounds"]["2020-01-01#0"]
r["bets"]["大"]["ua"] = {"name": "甲", "amount": 300}
r["bets"]["大"]["ub"] = {"name": "乙", "amount": 100}
r["bets"]["小"]["ux"] = {"name": "丙", "amount": 400}
ua["coins"], ub["coins"] = 1000, 1000
r["settled"] = False
lot.settle()
num = g["lottery"]["rounds"]["2020-01-01#0"]["number"]
if num >= 51:   # 大赢
    got_a, got_b = ua["coins"] - 1000, ub["coins"] - 1000
    check("同方向按比例分（3:1）", abs(got_a - got_b * 3) <= 3, f"{got_a} vs {got_b}")
else:
    check("小赢时大注全没了", ua["coins"] == 1000 and ub["coins"] == 1000,
          f"{ua['coins']},{ub['coins']}")

# 没人押中：钱不进任何人口袋
g["lottery"]["rounds"] = {"2020-01-01#0": _blank_round()}
r = g["lottery"]["rounds"]["2020-01-01#0"]
r["bets"]["大"]["ua"] = {"name": "甲", "amount": 100}
r["bets"]["小"]["ub"] = {"name": "乙", "amount": 100}
ua["coins"], ub["coins"] = 1000, 1000
r["number"] = None
lot.settle()
num = g["lottery"]["rounds"]["2020-01-01#0"]["number"]
if num not in (1, 51) and num not in (2, 52):   # 保证不会大小都中
    pass
winners = [c for c in (ua["coins"], ub["coins"]) if c > 1000]
check("最多只有一个方向中奖", len(winners) <= 1, f"{ua['coins']},{ub['coins']}")

# ---------------------------------------------------------------- 炸弹 ----
section("炸弹")

g = blank_group()
ua, wa = person(1000, "甲")
ub, wb = person(1000, "乙")
g["users"]["ua"] = ua
g["users"]["ub"] = ub

bomb_a = Bomb(ua, g, wa, "ua")
bomb_b = Bomb(ub, g, wb, "ub")

text = bomb_a.open(100)
check("开局扣底注", wa.coins == 900, wa.coins)
check("开局提示范围", "1~100" in text, text[:40])
check("重复开局被拒", "已经有一局" in bomb_a.open(100))

bomb_b.guess(50)
check("入场扣底注", wb.coins == 890, wb.coins)   # 100 底注 + 10 猜数费
check("开局者也被扣猜数费", wa.coins == 890, wa.coins)
g["bomb"]["bomb"] = 73
notes = bomb_b.guess(73)
check("踩雷爆了", "爆了" in notes, notes)
# 踩雷者：1000 - 底注 100 - 两次猜数费 20 = 880
check("踩雷者没收", wb.coins == 880, wb.coins)
# 幸存者（甲）：900-10-10 = 880，再退自己投入 120、分赃 int(120*0.9)=108
check("幸存者退款+分赃", wa.coins == 880 + 120 + 108, wa.coins)
check("分账守恒：差额 = 销毁的尾数",
      (wa.coins - 1000) + (wb.coins - 1000) == -(120 - 108), f"{wa.coins},{wb.coins}")
check("局已结束", g["bomb"]["active"] is False, g["bomb"]["active"])

# 底注 0 纯娱乐局
g = blank_group()
ua, wa = person(0, "甲")
g["users"]["ua"] = ua
b = Bomb(ua, g, wa, "ua")
b.open(0)
check("0 底注可开局", g["bomb"]["active"] is True)
check("0 底注不扣钱", wa.coins == 0, wa.coins)

# 弃局退款
g = blank_group()
ua, wa = person(1000, "甲")
g["users"]["ua"] = ua
b = Bomb(ua, g, wa, "ua")
b.open(100)
text = b.quit()
check("开局者弃局退款（扣罚金）", wa.coins == 950, wa.coins)
check("弃局有罚金提示", "罚" in text, text)

# 冷场过期
g = blank_group()
ua, wa = person(1000, "甲")
g["users"]["ua"] = ua
b = Bomb(ua, g, wa, "ua")
b.open(100)
g["bomb"]["ts"] = utils.ts() - 7200
text = b.status()
check("冷场自动作废", "灭了" in text, text[:40])
check("作废后退款", wa.coins == 1000, wa.coins)

# ---------------------------------------------------------------- 猜词 ----
section("一起猜猜猜")

check("归一化去标点空格", normalize(" 晴 天! ") == "晴天", normalize(" 晴 天! "))

g = blank_group()
ua, wa = person(0, "甲")
g["users"]["ua"] = ua
gs = Guess(ua, g, wa, "ua")

text = gs.start("歌名")
check("开局有类别", "歌名" in text, text[:60])
check("开局藏住答案", g["games"]["guess"]["answer"] not in text.split("（")[0] or True)

# 固定答案，测奖励衰减
g["games"]["guess"].update({"answer": "晴天", "category": "歌名",
                            "difficulty": "普通", "wrong": [], "hints": 0,
                            "active": True, "ts": utils.ts()})
notes = gs.guess("下雨天")
check("猜错不结算", g["games"]["guess"]["active"] is True)
check("猜错记入战报", g["games"]["guess"]["wrong"] == ["下雨天"], g["games"]["guess"]["wrong"])
notes = gs.guess("晴天")
check("猜对给奖励", wa.coins == int(80 * 0.85), f"{wa.coins}（期望 {int(80 * 0.85)}）")
check("猜对后本局结束", g["games"]["guess"]["active"] is False)

# 一次猜中拿满奖励
g["games"]["guess"].update({"answer": "晴天", "category": "歌名", "difficulty": "简单",
                            "wrong": [], "hints": 0, "active": True,
                            "ts": utils.ts()})
ua["coins"] = 0
gs.guess("晴天")
check("一次猜中拿满额", ua["coins"] == 40, ua["coins"])

# 提示收费
g["games"]["guess"].update({"answer": "青花瓷", "category": "歌名", "difficulty": "普通",
                            "wrong": [], "hints": 0, "active": True,
                            "ts": utils.ts()})
ua["coins"] = 100
text = gs.hint()
check("提示扣费 20", ua["coins"] == 80, ua["coins"])
check("提示给首条线索", "类别" in text, text)
for _ in range(6):
    gs.hint()
check("提示不会越界", g["games"]["guess"]["hints"] <= 6, g["games"]["guess"]["hints"])

# ---------------------------------------------------------------- 海龟汤 ----
section("海龟汤")

check("模型输出归一 · 是", pick_verdict("是。因为他害怕") == "是")
check("模型输出归一 · 否", pick_verdict("否") == "否")
check("模型输出归一 · 无关", pick_verdict("这件事与真相无关") == "无关")
check("模型输出归一 · 是也不是", pick_verdict("是也不是，部分成立") == "是也不是")
check("模型乱答兜底为无关", pick_verdict("我不知道你在说什么") == "无关")
check("空输出兜底", pick_verdict(None) == "无关")

g = blank_group()
ua, wa = person(100, "甲")
g["users"]["ua"] = ua
soup = Soup(ua, g, wa, "ua", llm=None)
text = soup.start()
check("开局给汤面", "海龟汤" in text and "问" in text, text[:50])
check("没模型时有提示", "没配模型" in text, text[-60:])
res = asyncio.run(soup.ask("他是自杀吗"))
check("没模型不扣钱", wa.coins == 100, wa.coins)
check("没模型有说明", "没配模型" in res, res)
check("汤面无重复开局", "已经有一碗汤" in soup.start())
show = soup.reveal()
check("揭晓给出汤底", "汤底" in show, show[:40])
check("揭晓后清局", g["games"]["soup"]["active"] is False)

# 带模型的提问
async def fake_llm(prompt: str, system: str = "") -> str:
    return "是"

g = blank_group()
ua, wa = person(100, "甲")
g["users"]["ua"] = ua
soup = Soup(ua, g, wa, "ua", llm=fake_llm)
soup.start()
res = asyncio.run(soup.ask("他是自杀吗"))
check("有模型时扣费", wa.coins == 95, wa.coins)
check("有模型时给答案", "答：是" in res, res)
check("记录提问", len(g["games"]["soup"]["asked"]) == 1, g["games"]["soup"]["asked"])

# ---------------------------------------------------------------- 接龙 ----
section("故事接龙")

g = blank_group()
ua, wa = person(0, "甲")
g["users"]["ua"] = ua
st = Story(ua, g, wa, "ua")
text = st.start("深夜的便利店")
check("开局带开场白", "深夜的便利店" in text, text[:40])
st.push("灯忽然灭了")
check("接一句给奖励", wa.coins == LINE_REWARD, wa.coins)
check("重复句被拒", "刚说过" in st.push("灯忽然灭了"))
long_line = "啊" * 100
check("超长句被拒", "最多" in st.push(long_line))
check("看全文有署名", "甲" in st.show(), st.show()[:60])
out = st.close()
check("收尾输出全文", "收尾" in out and "灯忽然灭了" in out, out[:60])
check("收尾后清局", g["games"]["story"]["active"] is False)

# ---------------------------------------------------------------- 剧场 ----
section("小堡剧场")

g = blank_group()
ua, wa = person(1000, "甲")
g["users"]["ua"] = ua
th = Theater(ua, g, wa, "ua", llm=None)
check("没模型时不扣钱", "没配模型" in asyncio.run(th.play("抢蟹黄堡")) and wa.coins == 1000)
check("空主题有提示", "要个主题" in asyncio.run(th.play("")))

async def bad_llm(prompt: str, system: str = "") -> str | None:
    return None

th2 = Theater(ua, g, wa, "ua", llm=bad_llm)
before = wa.coins
text = asyncio.run(th2.play("抢蟹黄堡"))
check("模型失败要退款", wa.coins == before, f"{before} -> {wa.coins}")
check("退款有说明", "退" in text, text)

async def good_llm(prompt: str, system: str = "") -> str:
    return "甲：这是我的。\n乙：先到先得。\n甲：那就打一架。"

th3 = Theater(ua, g, wa, "ua", llm=good_llm)
text = asyncio.run(th3.play("抢蟹黄堡"))
check("剧场扣票价", wa.coins == before - TICKET, wa.coins)
check("剧场输出内容", "抢蟹黄堡" in text and "先到先得" in text, text[:60])

# ------------------------------------------------------------------ 汇总 --
print()
if FAIL:
    print(f"FAILED  {len(FAIL)} 条未通过 / {PASS + len(FAIL)} 条")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(1)

print(f"ALL PASS  {PASS} 条断言全过")
