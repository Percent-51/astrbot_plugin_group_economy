# -*- coding: utf-8 -*-
"""银行：存款吃息、信用贷、地下钱庄（高利贷）。

为什么银行是这个插件的核心
--------------------------
游戏只是「花钱的口子」——弹药、彩票、炸弹，赢了加币输了扣币，钱在群里
来回转，总量不变。真正让经济体系活起来的是银行的三条腿：

1. **存款**：给存量资金一个去处，顺带给「不玩游戏的潜水党」一条收益线
2. **信用贷**：给「想玩但没钱」的人一个入口，额度随信用增长
3. **高利贷**：给走投无路的人一个更贵的入口，同时是**通胀阀门**

第三条最容易被忽略。群里如果没有高息负债，钱只会越攒越多，最后所有人
都富到「赢了也不兴奋」。高利贷把币收回去，并且用复利把拖延的代价放大，
这才形成闭环：**赚 → 存 → 借 → 赌 → 还不上 → 被收走**。

三个刻意的设计
--------------
1. **利息按持有时长折算，并且单次结算最多按 48 小时计**
   按天一刀切会有隔夜套利：23:59 存进去、00:01 取出来也吃一天利息。
   折算时长堵掉这个口子。而 48 小时上限是防灾开关——存档被旧数据覆盖时
   （容器重启回滚），不限长会把整个借款周期按旧利率重算一遍，实测出现过
   多收 1317 堡贝的事故。

2. **档位不看「好感」，看「信用分」**
   好感是小堡私有的亲密度数据，开源版本里没有也不该有。这里换成一套
   可解释的信用分：按时还清加分、逾期扣分、破产重扣。数字和五档的额度、
   利率沿用原设计，只换了判定依据。

3. **贷款未还清时不许再发红包**
   否则存在套现路径：借钱 → 发红包给同伙 → 领走 → 贷款悬空。这条锁
   不是财务计算，是防作弊，放在这里由调用方（红包模块）查询。
"""
from __future__ import annotations

import re
from typing import Any

from . import utils
from .wallet import Wallet

# ------------------------------------------------------------------ 参数 --
DEPOSIT_DAILY_RATE = 0.015      # 存款日息 1.5%
SETTLE_MAX_HOURS = 48.0         # 单次结算最多按 48h 计息（防存档回滚时重算全周期）

LOAN_TERM_DAYS = 7              # 信用贷期限
LOAN_MIN = 100                  # 起借金额
LOAN_HISTORY_KEEP = 5           # 保留最近几笔结清记录
COUPON_CAP = 300                # 首贷免息券上限
OPEN_ACCOUNT_BONUS = 5          # 首次存款开户礼

SHARK_CAP = 3000                # 单笔高利贷上限（不信用分）
SHARK_RATE = 12.0               # 日息 12%，复利
SHARK_TERM_DAYS = 3
SHARK_OVERDUE_MULT = 2.0        # 逾期利息倍数
SHARK_DAILY_POOL = 10000        # 单群每日放贷总池（防集中增发）

# ------------------------------------------------------------------ 信用 --
DEFAULT_CREDIT = 60             # 新用户起始信用分：从「熟客」起步
CREDIT_MAX = 1000
CREDIT_ON_TIME = 25             # 按时还清
CREDIT_LATE = 5                 # 逾期后还清（有污点但补上了）
CREDIT_OVERDUE_TICK = 20        # 每次逾期结算扣分
CREDIT_BANKRUPT = 60            # 破产扣分

# (信用分下限, 档位, 贷款额度, 日息%)
# 额度与利率沿用原设计，只把判定依据从「好感」换成「信用分」。
TIERS: tuple[tuple[int, str, int, float], ...] = (
    (800, "家人", 2000, 3.0),
    (300, "老朋友", 1200, 4.0),
    (100, "常客", 800, 5.0),
    (20, "熟客", 500, 6.0),
    (0, "路人", 300, 8.0),
)


def tier_of(credit: int) -> dict[str, Any]:
    """信用分 → 档位。信用分是负数时也兜得住（走最后一档）。"""
    c = int(credit or 0)
    for lo, label, loan_cap, rate in TIERS:
        if c >= lo:
            return {"min": lo, "label": label, "loan_cap": loan_cap, "rate": rate}
    lo, label, loan_cap, rate = TIERS[-1]
    return {"min": 0, "label": label, "loan_cap": loan_cap, "rate": rate}


def next_tier(credit: int) -> dict[str, Any] | None:
    """下一档。已封顶返回 None。"""
    c = int(credit or 0)
    cands = [t for t in TIERS if t[0] > c]
    if not cands:
        return None
    lo, label, loan_cap, rate = min(cands)
    return {"min": lo, "label": label, "loan_cap": loan_cap, "rate": rate}


class Bank:
    """针对「一个用户在一个群里」的银行操作。

    不落盘、不加锁——调用方必须在 ``store.transaction()`` 内使用。
    ``wallet`` 由外部注入，因为利息要走每日上限，而钱包才知道上限是多少。
    """

    def __init__(self, user: dict[str, Any], group: dict[str, Any], wallet: Wallet) -> None:
        self.user = user
        self.group = group
        self.w = wallet

    # ------------------------------------------------------------ 状态 --
    def _bank(self) -> dict[str, Any]:
        bank = self.user.setdefault("bank", {})
        bank.setdefault("dep", 0)
        bank.setdefault("dep_ts", utils.ts())
        bank.setdefault("loan", None)
        bank.setdefault("shark", None)
        bank.setdefault("history", [])
        # 老存档里 `coupon` 可能存成 1/0，统一成布尔，否则「用掉了」会被判成还有
        bank["coupon"] = bool(bank.get("coupon"))
        bank.setdefault("first_dep", False)
        # 早期版本把「没有贷款」写成 `{}`，直接 `isinstance(x, dict)` 会把它
        # 当成有效贷款，结算时往上写 ts/overdue，账户里就会挂一笔 0 元贷款。
        for key in ("loan", "shark"):
            item = bank.get(key)
            if isinstance(item, dict) and not item:
                bank[key] = None
        return bank

    @property
    def credit(self) -> int:
        """信用分。老存档没有这个字段时按默认值起算并写回，避免每次重算。"""
        if "credit" not in self.user:
            self.user["credit"] = DEFAULT_CREDIT
        return int(self.user.get("credit") or 0)

    def _bump_credit(self, delta: int) -> None:
        cur = self.credit + int(delta)
        self.user["credit"] = max(0, min(CREDIT_MAX, cur))

    @property
    def tier(self) -> dict[str, Any]:
        return tier_of(self.credit)

    def has_loan(self) -> bool:
        bank = self._bank()
        loan = bank.get("loan")
        return isinstance(loan, dict) and int(loan.get("principal", 0) or 0) > 0

    def has_shark(self) -> bool:
        bank = self._bank()
        shark = bank.get("shark")
        return isinstance(shark, dict) and int(shark.get("principal", 0) or 0) > 0

    def locked(self) -> str:
        """返回锁原因（空串=没锁）。

        红包模块在发包前必须查这个：贷款存续期禁止资金外流，
        否则「借钱 → 发包给同伙 → 领走」就能把贷款变成白送。
        """
        if self.has_shark():
            return "高利贷"
        if self.has_loan():
            return "贷款"
        return ""

    # ------------------------------------------------------------ 结算 --
    def settle(self, *, from_checkin: bool = False) -> list[str]:
        """懒结算：只在签到或打开银行时跑一次，不挂定时器。

        群里没有稳定时钟（容器会重启、会话会断），定时结算容易漏跑或
        重复跑；改成「谁来找我算账，就先把账算清」，代价是没人来的群
        不产生利息——而这恰好也是我们想要的。
        """
        notes: list[str] = []
        bank = self._bank()
        now = utils.ts()

        notes.extend(self._settle_deposit(bank, now))
        notes.extend(self._settle_loan(bank, now, from_checkin=from_checkin))
        notes.extend(self._settle_shark(bank, now))
        return notes

    def _settle_deposit(self, bank: dict[str, Any], now: int) -> list[str]:
        dep = int(bank.get("dep", 0) or 0)
        dep_ts = float(bank.get("dep_ts", now) or now)
        bank["dep_ts"] = now
        if dep <= 0 or now <= dep_ts:
            return []
        hours = min((now - dep_ts) / 3600.0, SETTLE_MAX_HOURS)
        interest = int(dep * DEPOSIT_DAILY_RATE * hours / 24.0)
        if interest < 1:
            return []
        # 利息是系统发钱，必须走 earn()：计入每日获取上限。
        # 被上限截掉的部分**不补发**——这是刻意的，否则「每天存满再取」
        # 就成了绕过每日上限的印钞机。
        granted, capped = self.w.earn(interest)
        if granted <= 0:
            return []
        tail = f"（超出每日上限 {interest - granted} 未发放）" if capped else ""
        return [f"🏦 存款利息到账 +{utils.fmt_coins(granted)}{tail}"]

    def _settle_loan(self, bank: dict[str, Any], now: int, *, from_checkin: bool) -> list[str]:
        loan = bank.get("loan")
        if not isinstance(loan, dict):
            return []
        notes: list[str] = []
        principal = int(loan.get("principal", 0) or 0)
        rate = float(loan.get("rate", 0) or 0) / 100.0
        ts = float(loan.get("ts", now) or now)
        due = float(loan.get("due_ts", now) or now)

        if principal > 0 and now > ts:
            if now <= due:
                hours = min((now - ts) / 3600.0, SETTLE_MAX_HOURS)
                acc = int(principal * rate * hours / 24.0)
            else:
                h1 = max(0.0, (min(now, due) - ts) / 3600.0)   # 正常期
                h2 = max(0.0, (now - due) / 3600.0)            # 逾期期
                # 防回滚：两段时长按比例压缩到 48h 内，保持逾期占比不变
                total = h1 + h2
                if total > SETTLE_MAX_HOURS:
                    scale = SETTLE_MAX_HOURS / total
                    h1 *= scale
                    h2 *= scale
                acc = int(principal * rate * h1 / 24.0
                          + principal * rate * 2.0 * h2 / 24.0)
            if acc > 0:
                loan["accrued"] = int(loan.get("accrued", 0) or 0) + acc

        was_overdue = bool(loan.get("overdue"))
        loan["ts"] = now
        loan["overdue"] = now > due

        if loan["overdue"] and not was_overdue:
            # 逾期只扣一次分，之后每次结算不再重复扣
            self._bump_credit(-CREDIT_OVERDUE_TICK)
        if loan["overdue"] and from_checkin:
            owed = int(loan.get("accrued", 0) or 0)
            coins = self.w.coins
            if owed > 0 and coins > 0:
                pay = min(owed, coins)
                self.w.force_deduct(pay)
                loan["accrued"] = owed - pay
                notes.append(
                    f"🏦 贷款已逾期，签到自动扣息 -{utils.fmt_coins(pay)}"
                )
                if loan["accrued"] <= 0 and int(loan.get("principal", 0) or 0) <= 0:
                    bank["loan"] = None
        return notes

    def _settle_shark(self, bank: dict[str, Any], now: int) -> list[str]:
        shark = bank.get("shark")
        if not isinstance(shark, dict):
            return []
        p = int(shark.get("principal", 0) or 0)
        if p <= 0:
            return []
        notes: list[str] = []
        acc = int(shark.get("accrued", 0) or 0)

        # 复利按「天」滚，不按结算频率滚：一天结算十次和一次结果必须一样，
        # 否则谁写脚本刷指令谁吃亏/占便宜。
        day_key = utils.today()
        if str(shark.get("fold_day") or "") != day_key:
            p, acc = p + acc, 0
            shark["fold_day"] = day_key

        ts = float(shark.get("ts", now) or now)
        due = float(shark.get("due_ts", now) or now)
        overdue = now > due
        if now > ts:
            hours = min((now - ts) / 3600.0, SETTLE_MAX_HOURS)
            rate = SHARK_RATE / 100.0 * (SHARK_OVERDUE_MULT if overdue else 1.0)
            acc += int(p * rate * hours / 24.0)

        was_overdue = bool(shark.get("overdue"))
        shark["ts"] = now
        shark["overdue"] = overdue
        if overdue and not was_overdue:
            self._bump_credit(-CREDIT_OVERDUE_TICK)

        if overdue:
            # 逾期自动划扣：先扣存款，再扣现金。这条是高利贷唯一有牙齿的地方。
            owed = p + acc
            dep = int(bank.get("dep", 0) or 0)
            pay = min(owed, dep + self.w.coins)
            if pay <= 0 and dep + self.w.coins <= 0 and not shark.get("broke_marked"):
                # 一分钱都扣不出来：记一次破产。加标记是为了别每次结算都记一次
                shark["broke_marked"] = True
                times = self.w.mark_bankrupt()
                self._bump_credit(-CREDIT_BANKRUPT)
                notes.append(f"🪦 已记为破产第 {times} 次——账上一分钱都划不出来了")
            if pay > 0:
                pay_dep = min(dep, pay)
                bank["dep"] = dep - pay_dep
                bank["dep_ts"] = now
                pay_coins = pay - pay_dep
                if pay_coins > 0:
                    self.w.force_deduct(pay_coins)
                pay_acc = min(pay, acc)
                acc -= pay_acc
                p -= (pay - pay_acc)
                if p <= 0:
                    self._record_cleared(bank, pay - pay_acc, pay_acc, "shark")
                    bank["shark"] = None
                    notes.append(
                        f"🌑 高利贷逾期，自动划扣 {utils.fmt_coins(pay)}"
                        f"（存款 {pay_dep} + 余额 {pay_coins}），已结清"
                    )
                    self._bump_credit(CREDIT_LATE)
                    return notes
                notes.append(
                    f"🌑 高利贷逾期，自动划扣 {utils.fmt_coins(pay)}"
                    f"（存款 {pay_dep} + 余额 {pay_coins}），仍欠 {utils.fmt_coins(p + acc)}"
                )
        shark["principal"], shark["accrued"] = p, acc
        return notes

    @staticmethod
    def _record_cleared(bank: dict[str, Any], principal: int, interest: int, kind: str) -> None:
        hist = bank.setdefault("history", [])
        hist.append({
            "principal": int(principal),
            "interest": int(interest),
            "ts": utils.ts(),
            "kind": kind,
        })
        del hist[:-LOAN_HISTORY_KEEP]

    # ------------------------------------------------------------ 存款 --
    def deposit(self, amount: int) -> str:
        amount = int(amount)
        if amount <= 0:
            return "要存多少呀？格式：银行：存款 100"
        bank = self._bank()
        coins = self.w.coins
        if coins <= 0:
            return "余额是 0，想存也存不进来。先去赚点堡贝喵。"
        # 余额不够就全额存入，而不是拒绝——原设计里这条是踩出来的：
        # 群友说「存款 500」时往往并不知道自己只有 380，拒绝一次就流失了。
        actual = min(amount, coins)
        note = f"\n（余额不够，先把 {actual} 全存了）" if amount > coins else ""
        self.w.debit(actual)
        bank["dep"] = int(bank.get("dep", 0) or 0) + actual
        bank["dep_ts"] = utils.ts()

        extra = ""
        if not bank.get("first_dep"):
            bank["first_dep"] = True
            granted, _ = self.w.earn(OPEN_ACCOUNT_BONUS)
            if granted:
                extra = f"\n🎁 开户礼 +{granted} 堡贝（首次存款专享）"
        return (f"🏦 存入 {utils.fmt_coins(actual)}，当前存款 "
                f"{utils.fmt_coins(bank['dep'])}。{note}{extra}")

    def withdraw(self, amount: int) -> str:
        bank = self._bank()
        dep = int(bank.get("dep", 0) or 0)
        if dep <= 0:
            return "你还没存款。发「银行：存款 100」先把小金库建起来喵。"
        if int(amount) <= 0:
            return "要取多少呀？格式：银行：取款 100"
        actual = min(int(amount), dep)
        bank["dep"] = dep - actual
        bank["dep_ts"] = utils.ts()
        self.w.credit(actual)
        return (f"🏦 取出 {utils.fmt_coins(actual)}，现金 {utils.fmt_coins(self.w.coins)}。"
                f"剩余存款 {utils.fmt_coins(bank['dep'])}。")

    # ------------------------------------------------------------ 信用贷 --
    def borrow(self, amount: int) -> str:
        amount = int(amount)
        bank = self._bank()
        tier = self.tier
        cap = int(tier["loan_cap"])
        if amount <= 0:
            return f"要借多少呀？格式：银行：贷款 100（{tier['label']}档额度 {cap}）"
        if amount < LOAN_MIN:
            return f"贷款起步价 {LOAN_MIN} 堡贝。"

        # 循环贷：没还清也能继续借，但「本金 + 已计息 + 本次」不得超额度。
        # 期限不重置，否则可以靠反复追加把 7 天期无限延长。
        existing = bank.get("loan") if isinstance(bank.get("loan"), dict) else None
        if existing is not None:
            used = int(existing.get("principal", 0) or 0) + int(existing.get("accrued", 0) or 0)
            if used + amount > cap:
                left = max(cap - used, 0)
                tip = "升信用分会涨额度，或先还点旧账" if left >= LOAN_MIN else "先还点旧账腾地方"
                return (f"你的档位（{tier['label']}）总额度 {cap}，已占用 {used}，"
                        f"本次最多还能借 {left}——{tip}喵。")
            old_rate = float(existing.get("rate", 0) or 0)
            new_rate = max(old_rate, float(tier["rate"]))  # 防止免息券被追加扩大套利
            rate_note = ""
            if new_rate > old_rate:
                existing["rate"] = new_rate
                rate_note = f"，追加后整笔按日息 {new_rate:g}% 计（含原免息部分）"
            existing["principal"] = used + amount
            existing["ts"] = utils.ts()
            self.w.credit(amount)
            due_day = utils.day_of(float(existing.get("due_ts") or utils.ts()))
            return (f"🏦 追加放款 {utils.fmt_coins(amount)}（{due_day} 前随首笔一并还清"
                    f"{rate_note}）。现金 {utils.fmt_coins(self.w.coins)}｜"
                    f"额度 {cap}，已用 {used + amount}。")

        if amount > cap:
            return (f"你的档位（{tier['label']}）最高只能借 {cap}。"
                    f"信用分涨到下一档额度会跟着涨。")

        now = utils.ts()
        rate = float(tier["rate"])
        coupon_note = ""
        if bank.get("coupon") and amount <= COUPON_CAP:
            rate = 0.0
            bank["coupon"] = False
            coupon_note = "\n🎟️ 首贷免息券已使用：这笔免息。"
        due_ts = now + LOAN_TERM_DAYS * 86400
        bank["loan"] = {
            "principal": amount, "rate": rate,
            "start_ts": now, "due_ts": due_ts,
            "accrued": 0, "ts": now, "overdue": False,
        }
        self.w.credit(amount)
        rate_note = "免息" if rate == 0 else f"日息 {rate:g}%"
        return (f"🏦 放款 {utils.fmt_coins(amount)}（{rate_note}，{LOAN_TERM_DAYS} 天期，"
                f"{utils.day_of(due_ts)} 前还清）。现金 {utils.fmt_coins(self.w.coins)}。"
                f"还清前不能发红包（防套现），逾期利息翻倍并自动扣款。{coupon_note}")

    def repay(self, amount: int = 0) -> str:
        bank = self._bank()
        loan = bank.get("loan")
        if not isinstance(loan, dict):
            return "你名下没有贷款，无债一身轻喵。"
        self.settle()  # 先结息，否则还的是旧账
        loan = bank.get("loan")
        if not isinstance(loan, dict):
            return "这笔账刚刚结清了喵。"
        owed = int(loan.get("accrued", 0) or 0) + int(loan.get("principal", 0) or 0)
        if owed <= 0:
            bank["loan"] = None
            return "这笔账已经清了喵。"
        pay = owed if int(amount) <= 0 else min(int(amount), owed)
        if not self.w.spend(pay):
            return (f"要还 {utils.fmt_coins(pay)}，但你只有 {utils.fmt_coins(self.w.coins)}。"
                    f"先赚点钱，或者带个小一点的数字部分还款喵。")
        acc = int(loan.get("accrued", 0) or 0)
        pay_acc = min(pay, acc)
        loan["accrued"] = acc - pay_acc
        loan["principal"] = int(loan.get("principal", 0) or 0) - (pay - pay_acc)

        if loan["principal"] <= 0 and loan["accrued"] <= 0:
            principal_paid = int(loan.get("principal", 0) or 0) + (pay - pay_acc)
            self._record_cleared(bank, max(0, principal_paid), int(loan.get("accrued", 0) or 0) + pay_acc, "loan")
            late = bool(loan.get("overdue"))
            bank["loan"] = None
            self._bump_credit(CREDIT_LATE if late else CREDIT_ON_TIME)
            gain = CREDIT_LATE if late else CREDIT_ON_TIME
            return (f"🏦 贷款结清！还款 {utils.fmt_coins(pay)}（利息 {pay_acc}）。"
                    f"信用分 +{gain}，现在 {self.credit}（{self.tier['label']}档）。"
                    f"发红包和玩游戏都解锁了喵。")
        return (f"🏦 部分还款 {utils.fmt_coins(pay)}（利息 {pay_acc}）。"
                f"剩余本金 {utils.fmt_coins(loan['principal'])}、"
                f"计息 {utils.fmt_coins(loan['accrued'])}。")

    # ---------------------------------------------------------- 地下钱庄 --
    def borrow_shark(self, amount: int) -> str:
        amount = int(amount)
        bank = self._bank()
        if self.has_shark():
            shark = bank.get("shark") or {}
            owed = int(shark.get("principal", 0) or 0) + int(shark.get("accrued", 0) or 0)
            return (f"你身上还压着一笔高利贷（欠 {utils.fmt_coins(owed)}）。"
                    "地下钱庄不做二贷——先还清再谈喵。")
        if amount <= 0:
            return ("🌑 地下钱庄：不看信用分、上限 "
                    f"{SHARK_CAP}，日息 {SHARK_RATE:g}% 复利、{SHARK_TERM_DAYS} 天期。\n"
                    "逾期利息×2、锁发红包、自动扣存款并当众催债。\n"
                    "格式：银行：借高利贷 1000（⚠️ 很黑，走投无路再借）")
        if amount > SHARK_CAP:
            return f"地下钱庄最多放 {SHARK_CAP} 给你，再多怕你跑路喵。"

        gstate = self.group.setdefault("global", {})
        pool = gstate.setdefault("shark_pool", {})
        day_key = utils.today()
        if str(pool.get("day") or "") != day_key:
            pool.update({"day": day_key, "issued": 0})
        issued = int(pool.get("issued", 0) or 0)
        if issued + amount > SHARK_DAILY_POOL:
            left = max(0, SHARK_DAILY_POOL - issued)
            return (f"今天本群的地下钱庄额度用完了（日池 {SHARK_DAILY_POOL}，还剩 {left}）。"
                    "明天趁早喵。")

        now = utils.ts()
        due_ts = now + SHARK_TERM_DAYS * 86400
        bank["shark"] = {
            "principal": amount, "accrued": 0,
            "start_ts": now, "due_ts": due_ts,
            "ts": now, "overdue": False, "fold_day": day_key,
        }
        pool["issued"] = issued + amount
        self.w.credit(amount)
        return (f"🌑 放款 {utils.fmt_coins(amount)}（日息 {SHARK_RATE:g}% 复利，"
                f"{utils.day_of(due_ts)} 前还清）。现金 {utils.fmt_coins(self.w.coins)}。\n"
                "警告：逾期利息×2、锁发红包、自动从存款里划扣，小堡还会当众催债喵。")

    def repay_shark(self, amount: int = 0) -> str:
        bank = self._bank()
        if not self.has_shark():
            return "你名下没有高利贷，干干净净喵。（普通贷款用「还款」）"
        self.settle()
        shark = bank.get("shark")
        if not isinstance(shark, dict):
            return "高利贷刚刚结清了喵。"
        p = int(shark.get("principal", 0) or 0)
        acc = int(shark.get("accrued", 0) or 0)
        owed = p + acc
        pay = owed if int(amount) <= 0 else min(int(amount), owed)
        if not self.w.spend(pay):
            return (f"要还 {utils.fmt_coins(pay)}，但你只有 {utils.fmt_coins(self.w.coins)}。"
                    "拖到逾期的话，存款也会被直接划扣喵。")
        pay_acc = min(pay, acc)
        acc -= pay_acc
        p -= (pay - pay_acc)
        if p <= 0:
            self._record_cleared(bank, pay - pay_acc, pay_acc, "shark")
            bank["shark"] = None
            self._bump_credit(CREDIT_LATE)
            return (f"🌑 高利贷结清！还了 {utils.fmt_coins(pay)}（利息 {pay_acc}）。"
                    "小堡把账本合上了……下次别借这么黑的喵。")
        shark["principal"], shark["accrued"] = p, acc
        return (f"🌑 部分还款 {utils.fmt_coins(pay)}（利息 {pay_acc}）。"
                f"还欠 {utils.fmt_coins(p + acc)}，钟还在走喵。")

    # ------------------------------------------------------------ 展示 --
    def account_text(self) -> str:
        bank = self._bank()
        tier = self.tier
        nxt = next_tier(self.credit)
        lines = ["🏦 小堡银行 · 账户", "━━━━━━━━━━━━━━"]
        lines.append(f"档位：{tier['label']}（信用 {self.credit}）"
                     f"｜现金 {utils.fmt_coins(self.w.coins)} 堡贝")
        lines.append(f"存款：{utils.fmt_coins(bank.get('dep', 0))}"
                     f"（不设上限，日息 {DEPOSIT_DAILY_RATE * 100:g}%，利息算进每日获取上限）")
        if nxt:
            lines.append(f"升档：信用再攒 {nxt['min'] - self.credit} 到「{nxt['label']}」，"
                         f"额度 {tier['loan_cap']}→{nxt['loan_cap']}")
        loan = bank.get("loan")
        if isinstance(loan, dict):
            left = utils.duration_cn(float(loan.get("due_ts", 0) or 0) - utils.ts())
            status = ("⚠️ 已逾期（利息翻倍、已锁发红包）" if loan.get("overdue")
                      else f"还剩 {left}到期")
            lines.append(f"贷款：本金 {utils.fmt_coins(loan.get('principal', 0))}"
                         f"｜已计息 {utils.fmt_coins(loan.get('accrued', 0))}"
                         f"｜日息 {loan.get('rate', 0):g}%｜{status}")
            lines.append("还款：发「银行：还款」一键全清，或带数字部分还")
        else:
            lines.append(f"贷款：无（额度 {tier['loan_cap']}，日息 {tier['rate']:g}%，"
                         f"{LOAN_TERM_DAYS} 天期）")
        shark = bank.get("shark")
        if isinstance(shark, dict) and int(shark.get("principal", 0) or 0) > 0:
            owed = int(shark.get("principal", 0) or 0) + int(shark.get("accrued", 0) or 0)
            left = utils.duration_cn(float(shark.get("due_ts", 0) or 0) - utils.ts())
            status = ("⚠️ 已逾期（利息×2 + 自动划扣）" if shark.get("overdue")
                      else f"还剩 {left}到期")
            lines.append(f"🌑 高利贷：欠 {utils.fmt_coins(owed)}"
                         f"（本金 {utils.fmt_coins(shark.get('principal', 0))}"
                         f"+利息 {utils.fmt_coins(shark.get('accrued', 0))}，"
                         f"日息 {SHARK_RATE:g}% 复利）｜{status}")
        if bank.get("coupon"):
            lines.append(f"🎟️ 你有一张「首贷免息券」：借 ≤{COUPON_CAP} 时自动使用")
        lines.append("📋 完整规则：发「银行：规则」")
        return "\n".join(lines)

    @staticmethod
    def rules_text() -> str:
        tier_lines = "｜".join(
            f"{label} {loan}（{rate:g}%）"
            for _lo, label, loan, rate in TIERS)
        return (
            "🏦 小堡银行 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"【存款】日息 {DEPOSIT_DAILY_RATE * 100:g}%，按持有时长折算，随存随取、不设上限。\n"
            "　　利息计入每日获取上限，超出部分不补发（防「存满再取」刷币）。\n"
            "【信用贷】7 天期，额度/日息按信用分五档：" + tier_lines + "。\n"
            "　　额度内可随借随还：没还清也能追加，累计不超额度，期限不重置。\n"
            f"　　每人一张首贷免息券：借 ≤{COUPON_CAP} 自动免息。\n"
            "　　存续期不能发红包（防套现）；逾期日息×2、签到自动扣息。\n"
            "【信用分】新用户 60。按时还清 +25｜逾期后还清 +5｜逾期一次 −20｜破产 −60。\n"
            f"【地下钱庄】不看信用分、上限 {SHARK_CAP}，日息 {SHARK_RATE:g}% 复利、"
            f"{SHARK_TERM_DAYS} 天期。\n"
            "　　逾期：利息×2、锁发红包、自动划扣（先存款后现金）、当众催债。\n"
            f"　　每群每日放贷池 {SHARK_DAILY_POOL}，借完等明天。\n"
            "【指令】存款 X｜取款 X｜贷款 X｜还款（不带数字=全清）｜\n"
            "　　借高利贷 X｜还高利贷｜账户｜规则\n"
            "　　存钱/取钱/取出/借款/借钱/还钱/还贷/高贷/还债 都认得，冒号可省。"
        )


# ---------------------------------------------------------------- 指令解析 --
# 群友不会背语法。「银行：存钱 100」「银行 存款100」「银行：取出 50 堡贝」
# 都得认，所以这里做的不是解析，是**猜**：先认动词（长词优先），
# 再归一半角/全角数字，最后去掉「堡贝/枚/元」这类量词。
VERB_MAP: dict[str, str] = {
    "存款": "deposit", "存钱": "deposit",
    "取款": "withdraw", "取钱": "withdraw", "取出": "withdraw",
    "贷款": "borrow", "借款": "borrow", "借钱": "borrow",
    "还款": "repay", "还钱": "repay", "还贷": "repay",
    "借高利贷": "borrow_shark", "高利贷": "borrow_shark", "高贷": "borrow_shark",
    "还高利贷": "repay_shark", "还高贷": "repay_shark", "还债": "repay_shark",
    "账户": "account", "余额": "account", "查余额": "account",
    "查账户": "account", "看看账户": "account",
    "规则": "rules", "玩法": "rules",
}

# 长词必须排在短词前面，否则「借高利贷」会被「高利贷」截胡、
# 「看看账户」会被「账户」截胡。
_VERB_RE = re.compile(
    r"^(借高利贷|还高利贷|看看账户|查余额|查账户|高利贷|高贷|还高贷|存款|存钱|取款"
    r"|取钱|取出|贷款|借款|借钱|还款|还钱|还贷|还债|余额|账户|规则|玩法)"
    r"\s*[:：]?\s*(.*)$")

_AMOUNT_RE = re.compile(r"^(\d+)\s*(?:堡?贝|枚|元)?$")


def parse_amount(text: str) -> int:
    """金额归一。返回 0=没写数字，-1=看不懂。

    「没写」和「看不懂」必须分开：`还款` 不带数字等于全额还清，
    而 `还款 一百` 应该报错，不能同样当成全额。
    """
    s = str(text or "").strip()
    s = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in s)
    s = s.replace(",", "").replace("，", "")
    if not s:
        return 0
    m = _AMOUNT_RE.match(s)
    return int(m.group(1)) if m else -1


def parse_command(text: str) -> tuple[str, int] | None:
    """把「存钱 100」解析成 `("deposit", 100)`；认不出来返回 None。"""
    m = _VERB_RE.match(str(text or "").strip())
    if not m:
        return None
    return VERB_MAP[m.group(1)], parse_amount(m.group(2))


USAGE_TEXT = (
    "小堡银行支持：\n"
    "账户｜存款 X｜取款 X｜贷款 X｜还款 X｜借高利贷 X｜还高利贷 X｜规则\n"
    "（存钱/取钱/借款/借钱/还钱/高贷 这些说法都认得，冒号可以省，"
    "「还款」不带数字=全额还清喵）"
)
