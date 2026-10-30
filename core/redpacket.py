# -*- coding: utf-8 -*-
"""红包：群友之间的零和转移，外加一个「手气王」的虚名。

为什么红包必须绕开每日上限
--------------------------
每日上限管的是「系统发钱」。红包是甲的钱进乙的口袋，群内总量不变，
如果也算进上限，那大额红包会被削成上限值，等于把转移当成了增发。

这也是银行那道锁存在的原因
--------------------------
「借钱 → 发红包给同伙 → 同伙领走 → 贷款悬空」是一条真实存在的套现路径。
所以发包前必须先问 `Bank.locked()`：有未结清贷款就不许把资金转出去。

金额在发包时就分好
------------------
拼手气红包的传统做法是「领的时候才随机」，但那样有个副作用：**过期退款
必须把已经发出去的钱算清楚**，一旦状态被回滚就会重复退款。这里改成
发包时一次把 N 份金额切好写进存档，领取只是「取走一份」，剩余份数直接
求和就是应退金额——账永远对得上。
"""
from __future__ import annotations

import random
from typing import Any

from . import utils
from .bank import Bank
from .wallet import Wallet

MIN_TOTAL = 10
MAX_TOTAL = 100_000
MAX_COUNT = 200
TTL_SEC = 600                 # 10 分钟没抢完，剩余退回
MAX_ACTIVE_PER_GROUP = 3      # 同时挂着的红包上限，防刷屏

_R = random.Random()


def split_lucky(total: int, count: int) -> list[int]:
    """拼手气：二倍均值法。每人至少 1，合计精确等于总额。

    二倍均值的好处是「不会有人只抢到 1 分钱」——每次的期望都是剩余均值，
    方差比纯随机小，群里不会出现一个人拿走九成的极端情况。
    """
    total, count = int(total), int(count)
    if count <= 0 or total <= 0:
        return []
    if count == 1:
        return [total]
    # 每个人至少要 1，先把底水分掉，剩下的再随机
    left = total - count
    out: list[int] = []
    for i in range(count - 1):
        remain_people = count - i
        cap = max(1, left // remain_people * 2)
        take = _R.randint(0, min(cap, left)) if left > 0 else 0
        out.append(1 + take)
        left -= take
    out.append(1 + left)
    return out


def _blank() -> dict[str, Any]:
    return {
        "id": "",
        "sender": "",
        "sender_name": "",
        "total": 0,
        "shares": [],        # 尚未被抢走的金额
        "taken": {},         # uid -> 金额
        "names": {},         # uid -> 昵称
        "ts": 0,
        "blessing": "",
        "done": False,
    }


class RedPacket:
    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "") -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")

    def _store(self) -> dict[str, Any]:
        packs = self.group.setdefault("redpackets", {})
        return packs

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    # ------------------------------------------------------------ 清理 --
    def sweep(self) -> list[str]:
        """把过期红包的剩余金额退回发包人。每次操作前跑一遍。"""
        packs = self._store()
        now = utils.ts()
        notes: list[str] = []
        for rid in list(packs):
            p = packs.get(rid)
            if not isinstance(p, dict) or p.get("done"):
                continue
            if now - int(p.get("ts", 0) or 0) < TTL_SEC:
                continue
            left = sum(int(x) for x in (p.get("shares") or []))
            p["done"] = True
            if left > 0:
                sender = (self.group.get("users") or {}).get(str(p.get("sender")))
                if isinstance(sender, dict):
                    Wallet(sender, self.w.daily_cap).add(left)
                notes.append(f"🧧 {p.get('sender_name')} 的红包过期，退回 {utils.fmt_coins(left)}")
            packs.pop(rid, None)
        return notes

    # ------------------------------------------------------------ 发包 --
    def send(self, total: int, count: int, blessing: str = "") -> str:
        head = "\n".join(self.sweep())
        head = (head + "\n") if head else ""
        total = int(total)
        count = int(count)
        if total < MIN_TOTAL:
            return head + f"红包至少要 {MIN_TOTAL} 堡贝喵。"
        if total > MAX_TOTAL:
            return head + f"一个红包最多 {MAX_TOTAL}，分几个发吧喵。"
        if count <= 0:
            count = 1
        if count > MAX_COUNT:
            return head + f"一个红包最多拆 {MAX_COUNT} 份喵。"
        if total < count:
            return head + f"{total} 堡贝分不了 {count} 份，每份至少 1 喵。"

        active = [p for p in self._store().values()
                  if isinstance(p, dict) and not p.get("done")]
        if len(active) >= MAX_ACTIVE_PER_GROUP:
            return head + f"本群已经有 {MAX_ACTIVE_PER_GROUP} 个红包没抢完，先抢完再发喵。"
        if Bank(self.user, self.group, self.w).locked():
            return head + "你还有账没结清，先还上再发红包喵（防借钱套现）。"
        if not self.w.spend(total):
            return head + f"要发 {utils.fmt_coins(total)}，你只有 {utils.fmt_coins(self.w.coins)} 喵。"

        rid = f"{self.uid}-{utils.ts()}-{_R.randint(100, 999)}"
        pack = _blank()
        pack.update({
            "id": rid,
            "sender": self.uid,
            "sender_name": self._name(),
            "total": total,
            "shares": split_lucky(total, count),
            "ts": utils.ts(),
            "blessing": str(blessing or "").strip()[:30],
        })
        self._store()[rid] = pack
        bless = f"\n　「{pack['blessing']}」" if pack["blessing"] else ""
        return (head + f"🧧 {self._name()} 发了一个红包：{utils.fmt_coins(total)} 堡贝 / {count} 份"
                f"{bless}\n发「红包 抢」开抢，{TTL_SEC // 60} 分钟没抢完退回喵。")

    # ------------------------------------------------------------ 抢包 --
    def grab(self) -> str:
        head = "\n".join(self.sweep())
        head = (head + "\n") if head else ""
        packs = [p for p in self._store().values()
                 if isinstance(p, dict) and not p.get("done") and p.get("shares")]
        if not packs:
            return head + "现在没有红包可抢喵。发「红包 发 100 5」自己发一个？"
        pack = sorted(packs, key=lambda p: int(p.get("ts", 0) or 0))[0]
        if self.uid == str(pack.get("sender")):
            return head + "自己发的红包不能自己抢喵。"
        if self.uid in (pack.get("taken") or {}):
            return head + "这个红包你已经抢过了喵，等下一个。"

        shares = pack.get("shares") or []
        amount = int(shares.pop())
        pack["shares"] = shares
        pack.setdefault("taken", {})[self.uid] = amount
        pack.setdefault("names", {})[self.uid] = self._name()
        self.w.add(amount)

        total = int(pack.get("total", 0) or 0)
        taken_count = len(pack["taken"])
        left_count = len(shares)
        lines = [f"🧧 抢到 {utils.fmt_coins(amount)} 堡贝！（{pack.get('sender_name')} 的红包）"]

        if not shares:
            pack["done"] = True
            best_uid = max(pack["taken"], key=lambda k: int(pack["taken"][k]))
            best = int(pack["taken"][best_uid])
            best_name = str((pack.get("names") or {}).get(best_uid) or best_uid)
            lines.append(f"　最后一个！{taken_count} 人分完 {utils.fmt_coins(total)}，"
                         f"手气王是 {best_name}（{utils.fmt_coins(best)}）")
            self._crown(best_uid, best_name, best)
            self._store().pop(str(pack.get("id")), None)
        else:
            lines.append(f"　还剩 {left_count} 份，别人还能抢喵。")
        crown = self._crown_text()
        if crown:
            lines.append(crown)
        return head + "\n".join(lines)

    def _crown(self, uid: str, name: str, amount: int) -> None:
        g = self.group.setdefault("global", {})
        day = utils.today()
        cur = g.get("rp_king") or {}
        if str(cur.get("day") or "") != day or int(cur.get("amount", 0) or 0) < amount:
            g["rp_king"] = {"day": day, "uid": uid, "name": name, "amount": amount}

    def _crown_text(self) -> str:
        g = self.group.setdefault("global", {})
        cur = g.get("rp_king") or {}
        if str(cur.get("day") or "") != utils.today():
            return ""
        return f"👑 今日手气王：{cur.get('name')}（{utils.fmt_coins(cur.get('amount', 0))}）"

    # ------------------------------------------------------------ 查询 --
    def listing(self) -> str:
        head = "\n".join(self.sweep())
        head = (head + "\n") if head else ""
        packs = [p for p in self._store().values()
                 if isinstance(p, dict) and not p.get("done")]
        if not packs:
            return head + "现在没有挂着的红包喵。格式：红包 发 100 5 恭喜发财"
        lines = [f"🧧 群里的红包（{len(packs)} 个）"]
        now = utils.ts()
        for p in sorted(packs, key=lambda x: int(x.get("ts", 0) or 0)):
            left_amt = sum(int(x) for x in (p.get("shares") or []))
            left_n = len(p.get("shares") or [])
            left_sec = TTL_SEC - (now - int(p.get("ts", 0) or 0))
            lines.append(f"　{p.get('sender_name')}：{utils.fmt_coins(p.get('total'))}"
                         f"／剩 {left_n} 份 {utils.fmt_coins(left_amt)}"
                         f"／{utils.duration_cn(left_sec)}后过期")
        lines.append(self._crown_text() or "　（今天还没人抢到大包）")
        return head + "\n".join(lines)

    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        head, _, tail = text.partition(" ")
        head, tail = head.strip(), tail.strip()
        if head in ("规则", "玩法"):
            return self.rules_text()
        if head in ("抢", "领取"):
            return self.grab()
        if head in ("查", "列表", "看") or not text:
            return self.listing()
        if head in ("发", "塞"):
            parts = tail.split()
            nums = [p for p in parts if p.isdigit()]
            words = [p for p in parts if not p.isdigit()]
            if not nums:
                return "发多少？格式：红包 发 100 5 恭喜发财（总额 + 份数）"
            total = int(nums[0])
            count = int(nums[1]) if len(nums) > 1 else 1
            return self.send(total, count, " ".join(words))
        return "没看懂。发「红包 规则」喵。"

    @staticmethod
    def rules_text() -> str:
        return (
            "🧧 红包 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"发包：红包 发 <总额> <份数> <祝福语>，例：红包 发 100 5 恭喜发财\n"
            f"　总额 {MIN_TOTAL}~{MAX_TOTAL}，最多 {MAX_COUNT} 份，每份至少 1 堡贝。\n"
            "抢包：红包 抢。拼手气，金额发包时就切好了，不用赌运气。\n"
            f"　{TTL_SEC // 60} 分钟没抢完，剩下的自动退回发包人；自己不能抢自己的。\n"
            f"　同群同时最多挂 {MAX_ACTIVE_PER_GROUP} 个红包。\n"
            "　每天抢到最大包的人上「手气王」（纯荣誉，不发钱）。\n"
            "⚠️ 有贷款或高利贷没结清时不能发包（防借钱套现）。\n"
            "📋 指令：红包 发 100 5｜红包 抢｜红包 查｜红包 规则"
        )
