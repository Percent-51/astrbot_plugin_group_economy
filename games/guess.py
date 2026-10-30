# -*- coding: utf-8 -*-
"""一起猜猜猜：藏一个词，全群问出来。

为什么这个游戏值得占 2240 条题库
-------------------------------
它**不需要 LLM、不需要联网、不需要两个人配合**——一个人也能玩，而且
答案是确定的，不存在「模型判错」的争议。在一个经济体系里，这是唯一
可以无限重复、又不会让玩家觉得被坑的赚钱方式。

提示要花钱
----------
提示收费 20 堡贝，不是为了赚钱，是为了让「开提示」变成一个真正的选择：
白嫖党会硬猜，愿意付钱的人换确定性。有任何一方不需要付代价，提示就
会变成默认动作，游戏就没难度了。
"""
from __future__ import annotations

import random
import re
from typing import Any

from ..core import content, utils
from ..core.wallet import Wallet

IDLE_SEC = 30 * 60          # 冷场作废
HINT_COST = 20              # 每个提示的费用
MAX_GUESSES_SHOWN = 5       # 战报里最多列几个错误答案

REWARDS = {"简单": 40, "普通": 80, "困难": 150}
WRONG_PENALTY = 0.15        # 每猜错一次，奖励按比例衰减
REWARD_FLOOR = 0.20         # 衰减下限

_PUNCT = re.compile(r"[\s.,，。、！？!?~·\-—_'\"“”‘’()（）\[\]【】<>《》]")

_R = random.Random()


def normalize(text: str) -> str:
    """比对前归一：去空格标点、转小写。群友很少把词打得分毫不差。"""
    return _PUNCT.sub("", str(text or "")).lower()


def _blank() -> dict[str, Any]:
    return {
        "active": False,
        "answer": "",
        "category": "",
        "difficulty": "普通",
        "wrong": [],
        "hints": 0,
        "ts": 0,
        "opener": "",
        "opener_name": "",
    }


class Guess:
    """一个群的猜词局。状态落在 group["games"]["guess"]。"""

    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "") -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")

    def _g(self) -> dict[str, Any]:
        games = self.group.setdefault("games", {})
        g = games.get("guess")
        if not isinstance(g, dict):
            g = _blank()
            games["guess"] = g
        for k, v in _blank().items():
            g.setdefault(k, v)
        return g

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    def _expire(self, g: dict[str, Any]) -> str:
        if not g.get("active"):
            return ""
        if utils.ts() - int(g.get("ts", 0) or 0) < IDLE_SEC:
            return ""
        answer = g.get("answer")
        g.update(_blank())
        return f"⌛ 上一局（答案是「{answer}」）太久没人猜，作废了。\n"

    # ------------------------------------------------------------ 开局 --
    def start(self, arg: str = "") -> str:
        g = self._g()
        head = self._expire(g)
        if g.get("active"):
            return (head + f"已经在猜了，别贪心。（提示：{self._hint_line(g)}）")
        pool = list(content.words())
        if not pool:
            return "题库读不出来，先检查 data/words.json 喵。"

        arg = str(arg or "").strip()
        if arg in content.categories():
            pool = [w for w in pool if w["c"] == arg]
            picked = _R.choice(pool)
        elif arg in REWARDS:
            sub = [w for w in pool if w["d"] == arg]
            picked = _R.choice(sub or pool)
        elif not arg:
            picked = _R.choice(pool)
        else:
            return (f"没有「{arg}」这个类别喵。可选："
                    + "、".join(content.categories()) + "，或者 简单/普通/困难")

        g.update(_blank())
        g["active"] = True
        g["answer"] = picked["a"]
        g["category"] = picked["c"]
        g["difficulty"] = picked["d"]
        g["ts"] = utils.ts()
        g["opener"] = self.uid
        g["opener_name"] = self._name()
        return (head + f"🎯 猜一个词（{picked['c']}·{picked['d']}，"
                f"{len(picked['a'])} 个字）\n"
                f"发「猜猜猜 你的答案」。想线索就发「猜猜猜 提示」（{HINT_COST} 堡贝一条）喵。")

    # ------------------------------------------------------------ 提示 --
    def _hints(self, g: dict[str, Any]) -> list[str]:
        answer = str(g.get("answer") or "")
        out = [f"类别是「{g.get('category')}」",
               f"一共 {len(answer)} 个字"]
        if len(answer) >= 2:
            out.append(f"第一个字是「{answer[0]}」")
        if len(answer) >= 3:
            out.append(f"最后一个字是「{answer[-1]}」")
        out.append(f"答案是「{'·'.join(answer)}」的形状")
        return out

    def _hint_line(self, g: dict[str, Any]) -> str:
        hints = self._hints(g)
        n = min(int(g.get("hints", 0) or 0), len(hints))
        return "｜".join(hints[:n]) if n else "还没开提示"

    def hint(self) -> str:
        g = self._g()
        head = self._expire(g)
        if not g.get("active"):
            return head + "现在没在猜，发「猜猜猜 开」开始喵。"
        hints = self._hints(g)
        n = int(g.get("hints", 0) or 0)
        if n >= len(hints):
            return "线索全给你了，剩下的自己想喵。"
        if not self.w.spend(HINT_COST):
            return f"开提示要 {HINT_COST} 堡贝，你只有 {utils.fmt_coins(self.w.coins)}。"
        g["hints"] = n + 1
        g["ts"] = utils.ts()
        return f"💡 线索 {n + 1}/{len(hints)}：{hints[n]}\n（-{HINT_COST} 堡贝）"

    # ------------------------------------------------------------ 猜 --
    def guess(self, text: str) -> str:
        g = self._g()
        head = self._expire(g)
        if not g.get("active"):
            return head + "现在没在猜，发「猜猜猜 开」开始喵。"
        raw = str(text or "").strip()
        if not raw:
            return "猜什么呀？格式：猜猜猜 晴天"
        guess = normalize(raw)
        answer = normalize(g.get("answer"))
        g["ts"] = utils.ts()

        if guess == answer:
            return head + self._win(g)
        if answer and answer in guess and len(guess) <= len(answer) + 2:
            # 「是不是晴天」这种带语气的，算他猜对
            return head + self._win(g)

        g["wrong"] = (list(g.get("wrong") or []) + [raw])[-20:]
        return (head + f"❌ 不是「{raw}」。已经猜错 {len(g['wrong'])} 次，"
                f"奖励在缩水喵。\n{self._hint_line(g)}")

    def _win(self, g: dict[str, Any]) -> str:
        answer = str(g.get("answer") or "")
        difficulty = str(g.get("difficulty") or "普通")
        base = REWARDS.get(difficulty, 80)
        factor = max(REWARD_FLOOR, 1 - WRONG_PENALTY * len(g.get("wrong") or []))
        reward = int(base * factor)
        granted, capped = self.w.earn(reward)
        wrong = len(g.get("wrong") or [])
        g.update(_blank())
        tail = f"（奖励被每日上限截到 {granted}）" if capped else ""
        detail = f"，猜错 {wrong} 次" if wrong else "，一次就中"
        return (f"🎉 对了！答案是「{answer}」（{difficulty}）{detail}。\n"
                f"+{granted} 堡贝{tail}　现金 {utils.fmt_coins(self.w.coins)}")

    def give_up(self) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在没在猜喵。"
        answer = g.get("answer")
        wrong = len(g.get("wrong") or [])
        g.update(_blank())
        return f"🏳️ 放弃了。答案是「{answer}」，猜错 {wrong} 次。下一局再来喵。"

    def status(self) -> str:
        g = self._g()
        head = self._expire(g)
        if not g.get("active"):
            n = len(content.words())
            return head + f"现在没在猜。题库里有 {n} 个词，发「猜猜猜 开」开始喵。"
        wrong = list(g.get("wrong") or [])
        lines = [f"🎯 正在猜（{g.get('category')}·{g.get('difficulty')}）",
                 f"　线索：{self._hint_line(g)}"]
        if wrong:
            lines.append("　错过的：" + "、".join(wrong[-MAX_GUESSES_SHOWN:]))
        return head + "\n".join(lines)

    # ------------------------------------------------------------ 分发 --
    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        head, _, tail = text.partition(" ")
        head, tail = head.strip(), tail.strip()
        if not text or head in ("状态", "看"):
            return self.status()
        if head in ("规则", "玩法"):
            return self.rules_text()
        if head in ("开", "开局", "开始"):
            return self.start(tail)
        if head in ("提示", "线索", "hint"):
            return self.hint()
        if head in ("放弃", "揭晓", "答案"):
            return self.give_up()
        return self.guess(text)

    @staticmethod
    def rules_text() -> str:
        n = len(content.words())
        cats = "、".join(content.categories())
        return (
            "🎯 一起猜猜猜 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"题库 {n} 个词，{len(content.categories())} 个类别：{cats}\n"
            f"奖励：简单 {REWARDS['简单']}｜普通 {REWARDS['普通']}｜困难 {REWARDS['困难']}"
            f"（每猜错一次打 {int(WRONG_PENALTY * 100)}% 折扣，最低 {int(REWARD_FLOOR * 100)}%）\n"
            f"提示 {HINT_COST} 堡贝一条，最多 5 条：类别 → 字数 → 首字 → 尾字 → 结构\n"
            f"{IDLE_SEC // 60} 分钟没人猜自动作废。\n"
            "📋 指令：猜猜猜 开｜猜猜猜 开 历史人物｜猜猜猜 开 困难｜"
            "猜猜猜 <你的答案>｜猜猜猜 提示｜猜猜猜 状态｜猜猜猜 放弃｜猜猜猜 规则"
        )
