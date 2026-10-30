# -*- coding: utf-8 -*-
"""彩票：刮刮乐 + 每日幸运号。

为什么把定时开奖改成「懒结算」
------------------------------
原版挂了一个常驻调度器，到点自己开奖、自己播报。这在群里很好用，但
它有三个成本：容器重启会丢定时器、多实例会重复开奖、离线时开奖消息
发不出去还得补发重试。

这里换成懒结算：**开奖时刻过了，谁碰彩票谁触发结算**，公告跟着他的
回执一起发出去。代价是没人玩的期次不出结果（但没人下注的期次本来
也没什么可开），收益是整个模块不需要任何后台任务。

两种玩法的资金性质不同，必须分开记账
------------------------------------
- 刮刮乐：**买定离手**，钱直接烧掉（消费），中了是系统放水
- 幸运号：**彩池共担**，输家的钱进池子，赢家按注额比例瓜分，系统抽 5%

前者是通缩（每刮一张净回收 3%），后者是转移。混在一起会让 RTP 算不清，
所以下面每一个加币都明确标了走 add 还是 earn。
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from ..core import utils
from ..core.wallet import Wallet

# --------------------------------------------------------------- 刮刮乐 --
SCRATCH_PRICE = 50
SCRATCH_BULK_MAX = 20           # 单次连刮上限，防手滑
# (档位, 概率, 奖金)；合计概率 1.0，平均返还 47/张 → RTP 94%
SCRATCH_TABLE: tuple[tuple[str, float, int], ...] = (
    ("谢谢惠顾", 0.36, 0),
    ("安慰奖", 0.25, 12),
    ("小奖", 0.20, 50),
    ("中奖", 0.12, 100),
    ("大奖", 0.06, 220),
    ("头奖", 0.01, 888),
)

# ------------------------------------------------------------- 幸运号 --
DRAW_SLOTS = (11, 15, 19, 22)   # 每天 4 期开奖时刻（北京时间）
SEALED_SEC = 5 * 60             # 开奖前 5 分钟封盘
BET_MIN = 50
BET_MAX = 500
HOUSE_RATE = 0.05               # 系统抽水
POT_RATIO = 0.20                # 底池 = 全场注额 ×20%
POT_CAP = 2000                  # 底池封顶
ROUNDS_KEEP = 12                # 存档里最多留几期历史

DIRECTIONS = ("大", "小", "单", "双")

_R = random.Random()


def _round_key(now: datetime, idx: int) -> str:
    return f"{now.date().isoformat()}#{idx}"


def _slot_ts(day: str, idx: int) -> float:
    hour = DRAW_SLOTS[min(idx, len(DRAW_SLOTS) - 1)]
    d = datetime.strptime(day, "%Y-%m-%d").replace(
        hour=hour, minute=0, second=0, tzinfo=utils.TZ)
    return d.timestamp()


def _split(key: str) -> tuple[str, int]:
    day, _, idx = key.partition("#")
    return day, int(idx or 0)


def current_round_key() -> str:
    """当前可下注的期次：今天第一个还没封盘的；都过了就押明天第一期。"""
    now = utils.now()
    for i in range(len(DRAW_SLOTS)):
        if now.timestamp() < _slot_ts(now.date().isoformat(), i) - SEALED_SEC:
            return _round_key(now, i)
    return _round_key(now + timedelta(days=1), 0)


def _slot_label(key: str) -> str:
    day, idx = _split(key)
    hh = DRAW_SLOTS[min(idx, len(DRAW_SLOTS) - 1)]
    return f"{day[5:]} 第 {idx + 1} 期（{hh}:00）"


def _result_dirs(n: int) -> tuple[str, str]:
    return ("大" if n >= 51 else "小"), ("单" if n % 2 else "双")


def _pot_bonus(total: int) -> int:
    return min(int(max(total, 0) * POT_RATIO), POT_CAP)


def parse_count(text: str) -> int:
    """从一串人话里抠出数字。抠不到返回 -1，空串返回 0。"""
    text = str(text or "").strip()
    if not text:
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else -1


def _blank_round() -> dict[str, Any]:
    return {
        "bets": {d: {} for d in DIRECTIONS},   # 方向 -> {uid: {name, amount}}
        "settled": False,
        "number": None,
        "payouts": {},                          # uid -> 金额
    }


class Lottery:
    """一个群的一台彩票机。状态落在 group["lottery"]。"""

    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "") -> None:
        self.user = user
        self.group = group
        self.w = wallet
        # uid 必须由调用方传进来：用户 dict 里只存昵称，昵称会改、会重名，
        # 拿它当主键会在改昵称后把注单认错人。
        self.uid = str(uid or user.get("name") or "?")

    # ------------------------------------------------------------ 状态 --
    def _state(self) -> dict[str, Any]:
        st = self.group.setdefault("lottery", {})
        st.setdefault("rounds", {})
        return st

    def _round(self, key: str) -> dict[str, Any]:
        """取（或建）某个期次。**已开奖的期次原样返回**，绝不重建——
        重建会把中奖记录抹掉，群里会出现「我明明中了但查不到」。"""
        rounds = self._state()["rounds"]
        r = rounds.get(key)
        if not isinstance(r, dict):
            r = _blank_round()
            rounds[key] = r
        if not r.get("settled"):
            for d in DIRECTIONS:
                r["bets"].setdefault(d, {})
        return r

    def _trim(self) -> None:
        rounds = self._state()["rounds"]
        if len(rounds) <= ROUNDS_KEEP:
            return
        for key in sorted(rounds)[: len(rounds) - ROUNDS_KEEP]:
            rounds.pop(key, None)

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    # ------------------------------------------------------------ 结算 --
    def settle(self) -> list[str]:
        """把所有已过开奖时刻、尚未结算的期次结掉。返回播报文本。"""
        st = self._state()
        now = utils.ts()
        lines: list[str] = []
        for key in sorted(st["rounds"]):
            r = st["rounds"][key]
            if not isinstance(r, dict) or r.get("settled"):
                continue
            day, idx = _split(key)
            if now < _slot_ts(day, idx):
                continue
            lines.extend(self._draw(key, r, now))
        self._trim()
        return lines

    def _draw(self, key: str, r: dict[str, Any], now: int) -> list[str]:
        number = _R.randint(1, 100)
        big_small, odd_even = _result_dirs(number)
        winners = {big_small, odd_even}

        bets: dict[str, dict[str, dict[str, Any]]] = r.get("bets") or {}
        total = sum(int(v.get("amount", 0) or 0)
                    for d in DIRECTIONS for v in (bets.get(d) or {}).values())
        payouts: dict[str, int] = {}

        if total > 0:
            pool = int(total * (1 - HOUSE_RATE)) + _pot_bonus(total)
            win_total = sum(int(v.get("amount", 0) or 0)
                            for d in winners for v in (bets.get(d) or {}).values())
            if win_total > 0:
                for d in winners:
                    for uid, bet in (bets.get(d) or {}).items():
                        stake = int(bet.get("amount", 0) or 0)
                        if stake <= 0:
                            continue
                        # 同一人押两个方向都中时，两笔一起按占比加权
                        payouts[uid] = payouts.get(uid, 0) + int(pool * stake / win_total)
                for uid, amount in payouts.items():
                    if amount <= 0:
                        continue
                    target = self._find_user(uid)
                    if target is not None:
                        # 彩池赔付是玩家之间的转移，不占每日上限
                        Wallet(target, self.w.daily_cap).add(amount)

        r["settled"] = True
        r["number"] = number
        r["payouts"] = payouts

        lines = [f"🎰 {_slot_label(key)} 开奖：{number}（{big_small}·{odd_even}）"]
        if total <= 0:
            lines.append("　这期没人下注，空开一期。")
        elif not payouts:
            lines.append(f"　全场注额 {total}，没人押中，池子归小堡了喵。")
        else:
            ranked = sorted(payouts.items(), key=lambda kv: kv[1], reverse=True)
            lines.append(f"　全场注额 {total}，中奖 {len(ranked)} 人：")
            for uid, amount in ranked[:5]:
                u = self._find_user(uid) or {}
                lines.append(f"　· {u.get('name') or uid}　+{utils.fmt_coins(amount)}")
        return lines

    def _find_user(self, uid: str) -> dict[str, Any] | None:
        users = self.group.get("users") or {}
        u = users.get(str(uid))
        return u if isinstance(u, dict) else None

    # ---------------------------------------------------------- 刮刮乐 --
    def scratch(self, count: int = 1) -> str:
        count = max(1, min(int(count or 1), SCRATCH_BULK_MAX))
        cost = SCRATCH_PRICE * count
        if not self.w.spend(cost):
            return (f"刮一张 {SCRATCH_PRICE} 堡贝，你只有 {utils.fmt_coins(self.w.coins)}。"
                    f"先去签到或者玩两局赚点喵。")

        tiers: list[str] = []
        total_win = 0
        for _ in range(count):
            roll = _R.random()
            acc = 0.0
            for label, prob, prize in SCRATCH_TABLE:
                acc += prob
                if roll < acc:
                    tiers.append(label)
                    total_win += prize
                    break
            else:
                tiers.append(SCRATCH_TABLE[-1][0])
                total_win += SCRATCH_TABLE[-1][2]

        if total_win > 0:
            # 刮刮乐是「定点放水」：不占每日上限，否则头奖会被削成上限值
            self.w.earn(total_win, ignore_cap=True)

        if count == 1:
            head = f"🎟️ 刮开：{tiers[0]}"
        else:
            cnt = Counter(tiers)
            head = f"🎟️ 刮了 {count} 张：" + "｜".join(f"{k}×{v}" for k, v in cnt.items())
        net = total_win - cost
        return (f"{head}\n💰 花 {cost}，回 {total_win}，净 {'+' if net >= 0 else ''}{net}"
                f"　现金 {utils.fmt_coins(self.w.coins)}")

    # ---------------------------------------------------------- 幸运号 --
    def bet(self, direction: str, amount: int) -> str:
        direction = str(direction or "").strip()
        if direction not in DIRECTIONS:
            return "押哪个？大 / 小 / 单 / 双。格式：彩票：买大 200"
        amount = int(amount)
        if amount <= 0:
            return f"押多少呀？单注 {BET_MIN}~{BET_MAX}，格式：彩票：买大 200"
        if amount < BET_MIN or amount > BET_MAX:
            return f"单注只能在 {BET_MIN}~{BET_MAX} 之间，多了小堡不敢接喵。"
        if not self.w.spend(amount):
            return f"你只有 {utils.fmt_coins(self.w.coins)}，押不起 {amount} 喵。"

        key = current_round_key()
        r = self._round(key)
        if r.get("settled"):
            # 极端边界：刚好跨过开奖时刻。钱已经扣了，必须退回去
            self.w.add(amount)
            return "这期刚封盘，钱还你了，再发一次就押下一期喵。"
        slot = r["bets"][direction].setdefault(self.uid, {"name": self._name(), "amount": 0})
        slot["amount"] = int(slot.get("amount", 0) or 0) + amount
        slot["name"] = self._name()

        total = sum(int(v.get("amount", 0) or 0)
                    for d in DIRECTIONS for v in r["bets"][d].values())
        stake = sum(int(v.get("amount", 0) or 0)
                    for d in DIRECTIONS for v in r["bets"][d].values()
                    if v.get("name") == self._name())
        return (f"🎯 押「{direction}」{amount} 堡贝，期次 {_slot_label(key)}。\n"
                f"本期全场注额 {utils.fmt_coins(total)}，你共押 {utils.fmt_coins(stake)}。"
                f"开奖前 5 分钟封盘喵。")

    def board(self) -> str:
        key = current_round_key()
        r = self._round(key)
        total = sum(int(v.get("amount", 0) or 0)
                    for d in DIRECTIONS for v in r["bets"][d].values())
        lines = [f"🎰 盘口 · {_slot_label(key)}", "━━━━━━━━━━━━━━"]
        if total <= 0:
            lines.append("还没人下注。你敢先押，赔率就由你定喵。")
            lines.append("格式：彩票：买大 200（大/小/单/双）")
            return "\n".join(lines)
        pool = int(total * (1 - HOUSE_RATE)) + _pot_bonus(total)
        for d in DIRECTIONS:
            stake = sum(int(v.get("amount", 0) or 0) for v in r["bets"][d].values())
            share = stake / total if total else 0
            # 倍率 = 押中能拿回多少 / 押多少。冷门少人押 → 倍率高
            odds = (pool * share / stake) if stake else 0.0
            lines.append(f"　{d}　{utils.fmt_coins(stake):>8}　"
                         f"{share * 100:4.1f}%　中奖倍率 {odds:.2f}")
        lines.append(f"合计注额 {utils.fmt_coins(total)}，可分配池 {utils.fmt_coins(pool)}")
        lines.append("倍率是活的：押爆热门会倒挂，开中也可能回不满本喵。")
        return "\n".join(lines)

    def my_bets(self) -> str:
        key = current_round_key()
        r = self._round(key)
        mine = []
        total = 0
        for d in DIRECTIONS:
            bet = r["bets"][d].get(self.uid)
            if isinstance(bet, dict) and int(bet.get("amount", 0) or 0) > 0:
                amt = int(bet["amount"])
                total += amt
                mine.append(f"　{d} {utils.fmt_coins(amt)}")
        if not mine:
            return f"{_slot_label(key)} 你还没押注。格式：彩票：买大 200"
        return (f"🎯 我的注单 · {_slot_label(key)}\n" + "\n".join(mine)
                + f"\n　合计 {utils.fmt_coins(total)}")

    # ------------------------------------------------------------ 分发 --
    async def handle(self, arg: str) -> str:
        """统一入口。main.py 只负责把「彩票: xxx」的 xxx 交给这里。"""
        text = str(arg or "").strip()
        notes = self.settle()
        head = ("\n".join(notes) + "\n") if notes else ""
        if not text:
            return head + self.board()
        if text in ("规则", "玩法"):
            return head + self.rules_text()
        if text in ("盘口", "看", "赔率"):
            return head + self.board()
        if text in ("注单", "我的", "我的注"):
            return head + self.my_bets()

        head_token, _, tail = text.partition(" ")
        head_token = head_token.strip()
        tail = tail.strip()

        if head_token in ("刮", "刮刮乐", "买"):
            n = parse_count(tail or "")
            if n < 0:
                return "刮几张呀？格式：彩票：刮 5（最多 20 张）"
            return head + self.scratch(n or 1)

        direction = head_token
        if direction.startswith("买") and len(direction) == 2:
            direction = direction[1]
        if direction in DIRECTIONS:
            amount = parse_count(tail or "")
            return head + self.bet(direction, amount)
        return head + "没看懂。发「彩票：规则」看看能怎么玩喵。"

    @staticmethod
    def rules_text() -> str:
        table = "｜".join(
            f"{label} {int(prob * 100)}%" + (f"→{prize}" if prize else "→0")
            for label, prob, prize in SCRATCH_TABLE)
        return (
            "🎰 彩票 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"🎟️ 刮刮乐：{SCRATCH_PRICE} 堡贝一张，当场开奖，单次最多 {SCRATCH_BULK_MAX} 张。\n"
            f"　档位：{table}\n"
            f"　平均每张返 47，差额是抽水（约 6%），用来抑制通胀。\n"
            f"🎯 幸运号：每天 {len(DRAW_SLOTS)} 期（"
            + " / ".join(f"{h}:00" for h in DRAW_SLOTS) + "），开奖前 5 分钟封盘。\n"
            f"　买大/小/单/双，单注 {BET_MIN}~{BET_MAX}，可累计。51-100 大｜1-50 小｜奇数单｜偶数双。\n"
            f"　彩池共担：系统抽 {HOUSE_RATE * 100:g}%，剩下的 + 底池按注额比例分给押中的人。\n"
            f"　底池 = 全场注额 ×{POT_RATIO * 100:g}%（封顶 {POT_CAP}），押中必赚。\n"
            "　倍率实时变：押爆热门会倒挂。\n"
            "📋 指令：彩票｜彩票：刮 5｜彩票：买大 200｜彩票：盘口｜彩票：注单｜彩票：规则"
        )
