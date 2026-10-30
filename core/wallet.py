# -*- coding: utf-8 -*-
"""堡贝账户。

核心概念：**获取** 与 **转移** 必须分开。
------------------------------------------------
- 获取（签到、游戏答对、BOSS 分红）= 系统凭空发钱 → 受每日上限约束
- 转移（红包、彩票赔付、存贷、转账）= 群友之间搬钱 → 零和，不占上限

这条分开是为了堵一个具体漏洞：如果赔付也算「获取」，那靠红包互相刷就能
顶破每日上限；反过来如果上限管住所有加币，彩票大奖会被卡成上限值。
"""
from __future__ import annotations

from typing import Any, Literal

from . import utils

Kind = Literal["earn", "transfer", "spend"]


class Wallet:
    """针对单个用户 dict 的薄操作层。

    不持有状态、不落盘——所有改动由调用方在 `store.lock()` 内完成。
    """

    def __init__(self, user: dict[str, Any], daily_cap: int = 300) -> None:
        self.user = user
        self.daily_cap = max(0, int(daily_cap))

    # -------------------------------------------------------------- 查询 --
    @property
    def coins(self) -> int:
        return int(self.user.get("coins", 0) or 0)

    @property
    def earned_today(self) -> int:
        e = self.user.get("earned") or {}
        if str(e.get("day") or "") != utils.today():
            return 0
        return int(e.get("amount", 0) or 0)

    @property
    def remaining_today(self) -> int:
        if self.daily_cap <= 0:
            return 10**9
        return max(0, self.daily_cap - self.earned_today)

    def can_afford(self, amount: int) -> bool:
        return self.coins >= int(amount)

    # -------------------------------------------------------------- 记账 --
    def _bump_earned(self, amount: int) -> None:
        day = utils.today()
        e = self.user.get("earned")
        if not isinstance(e, dict) or str(e.get("day") or "") != day:
            e = {"day": day, "amount": 0}
        e["amount"] = int(e.get("amount", 0) or 0) + int(amount)
        self.user["earned"] = e

    def _bump_stat(self, key: str, amount: int) -> None:
        stats = self.user.setdefault("stats", {})
        stats[key] = int(stats.get(key, 0) or 0) + int(amount)

    def earn(self, amount: int, *, ignore_cap: bool = False) -> tuple[int, bool]:
        """系统发钱。返回 `(实际到账, 是否被每日上限截断)`。

        `ignore_cap=True` 用于刮刮乐头奖这类「定点放水」，
        原设计明确它不占每日上限，否则大奖会被削平。
        """
        amount = int(amount)
        if amount <= 0:
            return 0, False
        if ignore_cap or self.daily_cap <= 0:
            granted = amount
            capped = False
        else:
            granted = min(amount, self.remaining_today)
            capped = granted < amount
        if granted <= 0:
            return 0, True
        self.user["coins"] = self.coins + granted
        if not ignore_cap:
            self._bump_earned(granted)
        self._bump_stat("earned", granted)
        return granted, capped

    def add(self, amount: int) -> int:
        """群友间转移进来的钱（红包 / 赔付）。零和，不占上限。"""
        amount = int(amount)
        if amount <= 0:
            return 0
        self.user["coins"] = self.coins + amount
        return amount

    def spend(self, amount: int) -> bool:
        """扣钱。余额不足返回 False 且不做任何改动。"""
        amount = int(amount)
        if amount <= 0:
            return True
        if self.coins < amount:
            return False
        self.user["coins"] = self.coins - amount
        self._bump_stat("spent", amount)
        return True

    def force_deduct(self, amount: int) -> int:
        """强制划扣（高利贷结算用）。扣到 0 为止，返回实际扣到的金额。"""
        amount = max(0, int(amount))
        if amount <= 0:
            return 0
        taken = min(self.coins, amount)
        self.user["coins"] = self.coins - taken
        self._bump_stat("spent", taken)
        return taken

    # -------------------------------------------------------------- 破产 --
    def mark_bankrupt(self) -> int:
        """记一次破产（余额归零且资不抵债时由银行调用）。返回累计次数。"""
        stats = self.user.setdefault("stats", {})
        stats["bankrupt"] = int(stats.get("bankrupt", 0) or 0) + 1
        return stats["bankrupt"]

    @property
    def bankrupt_times(self) -> int:
        return int((self.user.get("stats") or {}).get("bankrupt", 0) or 0)


def total_wealth(user: dict[str, Any]) -> int:
    """总资产 = 现金 + 存款 − 欠款。用于排行榜与破产判定。"""
    bank = user.get("bank") or {}
    dep = int(bank.get("dep", 0) or 0)
    loan = bank.get("loan") or {}
    debt = 0
    if isinstance(loan, dict) and loan:
        debt = int(loan.get("principal", 0) or 0) + int(loan.get("interest", 0) or 0)
    return int(user.get("coins", 0) or 0) + dep - debt
