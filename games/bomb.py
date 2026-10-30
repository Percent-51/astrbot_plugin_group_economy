# -*- coding: utf-8 -*-
"""炸弹：全群猜数字，踩雷的人赔钱。

玩法
----
一个人开局并埋一个 1~100 的炸弹，其他人轮流猜。每猜一次，**场上所有人**
再掏 10 堡贝进池子（这是逼大家别磨蹭的关键：拖得越久越贵）。
猜中即引爆，踩雷者已投入的底注和猜数费全部没收，其中 90% 平分给幸存者，
10% 直接销毁。

为什么底注可以开到 0
--------------------
底注 0 是「纯娱乐局」——不花钱，但会当众社死，而且照样出结果。
群里总有人只想玩不想赌，留这个口子是刻意的：**参与门槛越低，局越多**，
而局多了才有流水。

没有定时器怎么防止烂尾
----------------------
原版有回合限时，到点不猜就引爆。开源版去掉了后台任务，改成
**按空转时长自然失效**：任何一次操作前先检查这局多久没人动了，
超过 30 分钟就直接作废并退款。比定时器省事，且不怕容器重启。
"""
from __future__ import annotations

import random
from typing import Any

from ..core import utils
from ..core.wallet import Wallet

ENTRANCE_DEFAULT = 100      # 默认底注
GUESS_FEE = 10              # 每猜一次，全体参与者各掏这么多
RESTITUTE = 0.90            # 没收金额中返还幸存者的比例，其余销毁
FINE_QUIT = 50              # 付费局中途退场的罚金
IDLE_SEC = 30 * 60          # 超过这么久没人动，本局作废并退款
RANGE_MIN, RANGE_MAX = 1, 100

_R = random.Random()


def _blank() -> dict[str, Any]:
    return {
        "active": False,
        "entrance": 0,
        "bomb": 0,
        "opener": "",
        "opener_name": "",
        "players": {},      # uid -> {name, paid, guesses}
        "low": RANGE_MIN,   # 已知下界（炸弹比它大）
        "high": RANGE_MAX,  # 已知上界（炸弹比它小）
        "ts": 0,
        "rounds": 0,
    }


class Bomb:
    """一个群的炸弹局。状态落在 group["bomb"]。"""

    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "") -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")

    # ------------------------------------------------------------ 状态 --
    def _game(self) -> dict[str, Any]:
        g = self.group.setdefault("bomb", {})
        for k, v in _blank().items():
            g.setdefault(k, v)
        return g

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    def _expire(self, g: dict[str, Any]) -> list[str]:
        """空转太久自动作废并退款。返回需要播报的文本。"""
        if not g.get("active"):
            return []
        if utils.ts() - int(g.get("ts", 0) or 0) < IDLE_SEC:
            return []
        refunded = 0
        for uid, p in (g.get("players") or {}).items():
            paid = int((p or {}).get("paid", 0) or 0)
            if paid > 0:
                target = self._find_user(uid)
                if target is not None:
                    Wallet(target, self.w.daily_cap).add(paid)
                    refunded += paid
        victim_name = g.get("opener_name") or "开局的人"
        g["active"] = False
        g["players"] = {}
        return [f"💣 {victim_name} 埋的这颗雷放了太久，自己灭了。"
                f"（退款 {utils.fmt_coins(refunded)}）"]

    def _find_user(self, uid: str) -> dict[str, Any] | None:
        u = (self.group.get("users") or {}).get(str(uid))
        return u if isinstance(u, dict) else None

    # ------------------------------------------------------------ 操作 --
    def open(self, entrance: int = -1) -> str:
        g = self._game()
        notes = self._expire(g)
        head = ("\n".join(notes) + "\n") if notes else ""
        if g.get("active"):
            return (head + f"已经有一局在进行了（{g.get('opener_name')} 埋的雷，"
                    f"已猜 {g.get('rounds')} 次）。先把它猜爆喵。")
        if entrance < 0:
            entrance = ENTRANCE_DEFAULT
        entrance = max(0, int(entrance))
        if entrance > 0 and not self.w.spend(entrance):
            return f"开底注局要 {entrance} 堡贝，你只有 {utils.fmt_coins(self.w.coins)}。"

        g.update(_blank())
        g["active"] = True
        g["entrance"] = entrance
        g["bomb"] = _R.randint(RANGE_MIN, RANGE_MAX)
        g["opener"] = self.uid
        g["opener_name"] = self._name()
        g["ts"] = utils.ts()
        if entrance > 0:
            g["players"][self.uid] = {"name": self._name(), "paid": entrance, "guesses": 0}
        mode = f"底注 {entrance}" if entrance else "纯娱乐"
        return (head + f"💣 {self._name()} 埋好了一颗雷（{mode}），范围 "
                f"{RANGE_MIN}~{RANGE_MAX}。\n"
                f"发「炸弹：猜 50」开始。每猜一次，场上每人掏 {GUESS_FEE} 堡贝进池子喵。")

    def guess(self, number: int) -> str:
        g = self._game()
        notes = self._expire(g)
        head = ("\n".join(notes) + "\n") if notes else ""
        if not g.get("active"):
            return head + "现在没有局。发「炸弹 开」埋一颗喵。"

        n = int(number)
        if n < RANGE_MIN or n > RANGE_MAX:
            return f"只能猜 {RANGE_MIN}~{RANGE_MAX} 之间的数喵。"

        entrance = int(g.get("entrance", 0) or 0)
        players: dict[str, dict[str, Any]] = g["players"]
        if self.uid not in players:
            if entrance > 0 and not self.w.spend(entrance):
                return f"入场要先押底注 {entrance}，你只有 {utils.fmt_coins(self.w.coins)} 喵。"
            players[self.uid] = {"name": self._name(), "paid": 0, "guesses": 0}
            if entrance > 0:
                players[self.uid]["paid"] = entrance

        # 猜数费：场上每个人都掏，不只猜的人。这是「别磨蹭」的经济压力
        survivors = [u for u in players if u != self.uid]
        fee_paid: list[str] = []
        for uid in list(players):
            target = self._find_user(uid)
            if target is None:
                continue
            tw = Wallet(target, self.w.daily_cap)
            if tw.spend(GUESS_FEE):
                players[uid]["paid"] = int(players[uid].get("paid", 0) or 0) + GUESS_FEE
                if uid != self.uid:
                    fee_paid.append(str(players[uid].get("name") or uid))
        players[self.uid]["guesses"] = int(players[self.uid].get("guesses", 0) or 0) + 1
        g["rounds"] = int(g.get("rounds", 0) or 0) + 1
        g["ts"] = utils.ts()

        if n == int(g.get("bomb", 0)):
            return head + self._detonate(g, self.uid)

        if n > int(g.get("bomb", 0)):
            g["high"] = min(int(g.get("high", RANGE_MAX)), n)
            hint = "大了"
        else:
            g["low"] = max(int(g.get("low", RANGE_MIN)), n)
            hint = "小了"
        pool = sum(int(p.get("paid", 0) or 0) for p in players.values())
        fee_note = f"（{'、'.join(fee_paid)} 也被扣了 {GUESS_FEE}）" if fee_paid else ""
        return (head + f"💣 {self._name()} 猜 {n}：{hint}，范围缩到 "
                f"{g['low']}~{g['high']}。{fee_note}\n"
                f"场上 {len(players)} 人，池子已累计 {utils.fmt_coins(pool)}。")

    def _detonate(self, g: dict[str, Any], victim_uid: str) -> str:
        players: dict[str, dict[str, Any]] = g.get("players") or {}
        victim = players.get(victim_uid) or {}
        forfeit = int(victim.get("paid", 0) or 0)
        survivors = [(uid, p) for uid, p in players.items() if uid != victim_uid]

        # 先退幸存者的本金：他们没踩雷，只该损失猜数费
        for uid, p in survivors:
            refund = int(p.get("paid", 0) or 0)
            if refund > 0:
                target = self._find_user(uid)
                if target is not None:
                    Wallet(target, self.w.daily_cap).add(refund)

        share = int(forfeit * RESTITUTE)
        each = share // len(survivors) if survivors else 0
        for uid, _p in survivors:
            if each > 0:
                target = self._find_user(uid)
                if target is not None:
                    Wallet(target, self.w.daily_cap).add(each)

        name = str(victim.get("name") or self._name())
        g["active"] = False
        lines = [f"💥 爆了！{name} 踩中 {g.get('bomb')}，"
                 f"没收 {utils.fmt_coins(forfeit)} 堡贝。"]
        if not survivors:
            lines.append("　就他一个人玩，钱直接蒸发了喵。")
        elif each <= 0:
            lines.append(f"　他还没投入多少，幸存者 {len(survivors)} 人没分到钱。")
        else:
            names = "、".join(str((p or {}).get("name") or u) for u, p in survivors[:6])
            lines.append(f"　{share} 堡贝平分给 {len(survivors)} 位幸存者"
                         f"（各 +{each}）：{names}")
            lines.append(f"　另外 {forfeit - share} 堡贝被销毁。")
        g["players"] = {}
        return "\n".join(lines)

    def quit(self) -> str:
        g = self._game()
        notes = self._expire(g)
        head = ("\n".join(notes) + "\n") if notes else ""
        if not g.get("active"):
            return head + "现在没有局，不用退喵。"
        paid = int((g["players"].get(self.uid) or {}).get("paid", 0) or 0)
        is_opener = self.uid == g.get("opener")
        if is_opener:
            # 开局的人跑了这局就没意义了：作废 + 全员退款
            refunded = 0
            for uid, p in g["players"].items():
                amt = int((p or {}).get("paid", 0) or 0)
                target = self._find_user(uid)
                if target is not None and amt > 0:
                    Wallet(target, self.w.daily_cap).add(amt)
                    refunded += amt
            fine = 0
            if int(g.get("entrance", 0) or 0) > 0 and self.w.spend(FINE_QUIT):
                fine = FINE_QUIT
            g["active"] = False
            g["players"] = {}
            return (head + f"💣 {self._name()} 把雷挖走了，这局作废。"
                    f"（退款 {utils.fmt_coins(refunded)}"
                    + (f"，另罚 {fine} 堡贝" if fine else "") + "）")
        if self.uid in g["players"]:
            g["players"].pop(self.uid, None)
            if paid > 0:
                self.w.add(paid)   # 自己那份退回来，不罚
            return head + f"💣 {self._name()} 退场，退回 {utils.fmt_coins(paid)}。"
        return head + "你不在这局里喵。"

    def close(self) -> str:
        """管理员强制收尾：不判负，全员退款。"""
        g = self._game()
        if not g.get("active"):
            return "现在没有局。"
        refunded = 0
        for uid, p in (g.get("players") or {}).items():
            amt = int((p or {}).get("paid", 0) or 0)
            target = self._find_user(uid)
            if target is not None and amt > 0:
                Wallet(target, self.w.daily_cap).add(amt)
                refunded += amt
        g["active"] = False
        g["players"] = {}
        return f"💣 已拆弹，全员退款 {utils.fmt_coins(refunded)}。"

    def status(self) -> str:
        g = self._game()
        notes = self._expire(g)
        head = ("\n".join(notes) + "\n") if notes else ""
        if not g.get("active"):
            return head + "现在没有局。发「炸弹 开」埋一颗喵。"
        players = g.get("players") or {}
        pool = sum(int(p.get("paid", 0) or 0) for p in players.values())
        names = "、".join(str((p or {}).get("name") or u) for u, p in players.items()) or "暂无"
        return (head + f"💣 局中｜{g.get('opener_name')} 埋雷｜已猜 {g.get('rounds')} 次\n"
                f"　剩余范围 {g.get('low')}~{g.get('high')}｜底注 "
                f"{utils.fmt_coins(g.get('entrance', 0))}｜池子 {utils.fmt_coins(pool)}\n"
                f"　场上：{names}")

    # ------------------------------------------------------------ 分发 --
    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        head, _, tail = text.partition(" ")
        head, tail = head.strip(), tail.strip()
        if not text or head in ("状态", "看"):
            return self.status()
        if head in ("规则", "玩法"):
            return self.rules_text()
        if head in ("开", "开局"):
            n = "".join(ch for ch in tail if ch.isdigit())
            return self.open(int(n) if n else -1)
        if head in ("猜", "猜数"):
            n = "".join(ch for ch in tail if ch.isdigit())
            if not n:
                return "猜几？格式：炸弹 猜 50"
            return self.guess(int(n))
        if head in ("弃", "退", "退出"):
            return self.quit()
        if head in ("拆", "拆弹", "结束"):
            return self.close()
        # 「炸弹 50」这种直接猜的写法也认
        n = "".join(ch for ch in text if ch.isdigit())
        if n and text == n:
            return self.guess(int(n))
        return "没看懂。发「炸弹 规则」喵。"

    @staticmethod
    def rules_text() -> str:
        return (
            "💣 炸弹 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            "开局的人埋一个 1~100 的数，全群轮流猜。\n"
            f"每猜一次，场上**每人**再掏 {GUESS_FEE} 堡贝进池子（拖越久越贵）。\n"
            f"猜中即引爆：踩雷者已投入的全部没收，其中 {int(RESTITUTE * 100)}% 平分给"
            "幸存者、剩下的销毁；幸存者本金原样退回。\n"
            f"底注可以开 0（纯娱乐局，不花钱只会社死）。\n"
            f"开局者中途跑了：这局作废、全员退款，付费局另罚 {FINE_QUIT}。\n"
            f"超过 {IDLE_SEC // 60} 分钟没人动，自动作废退款。\n"
            "📋 指令：炸弹 开｜炸弹 开 100｜炸弹 猜 50｜炸弹 状态｜炸弹 弃｜炸弹 拆｜炸弹 规则"
        )
