# -*- coding: utf-8 -*-
"""海龟汤：只给汤面，全群靠「是 / 否」把真相盘出来。

这个玩法离不开模型
------------------
判断「这个问题算不算问到点子上」是纯语义工作，写规则写不出来。
所以本模块是**唯一强依赖 LLM 的玩法**，没配模型时只保留揭晓功能，
并把话说明白——「装了没反应」比「说了不能用」更劝退。

问要有成本
----------
每问一次扣 5 堡贝。不是为了赚钱，是因为无成本的提问会让人用
「是不是A」「是不是B」「是不是C」暴力枚举，二十轮就把汤底穷举完了。
"""
from __future__ import annotations

import random
from typing import Any

from ..core import content, utils
from ..core.llm import LlmFn
from ..core.wallet import Wallet

ASK_COST = 5
IDLE_SEC = 60 * 60
MAX_ASK = 60            # 单局提问上限，防穷举

_R = random.Random()

_SYSTEM = (
    "你在主持一场海龟汤游戏。玩家只能通过提问来推理真相，你只能回答四个词之一："
    "「是」「否」「无关」「是也不是」。规则："
    "问题必须能用是/否回答；与真相无关的细节答「无关」；"
    "部分成立答「是也不是」。只输出这四个词之一，不要解释，不要标点。"
)


def pick_verdict(text: str | None) -> str:
    """把模型输出压成四个词之一。

    模型很爱在「是」后面补一句解释，所以不能整句比对：只认开头或前六个字
    里出现的第一个关键词。顺序很重要——「是也不是」必须在「是」前面判，
    否则永远只会命中「是」。
    """
    raw = str(text or "").strip()
    for word in ("是也不是", "无关", "是", "否"):
        if raw.startswith(word) or word in raw[:6]:
            return word
    return "无关"


def _blank() -> dict[str, Any]:
    return {
        "active": False,
        "surface": "",
        "truth": "",
        "difficulty": "普通",
        "asked": [],        # [{"q": 问题, "a": 答案}]
        "ts": 0,
        "opener_name": "",
    }


class Soup:
    """一个群的一碗汤。状态落在 group["games"]["soup"]。"""

    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "", llm: LlmFn | None = None) -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")
        self.llm = llm

    def _g(self) -> dict[str, Any]:
        games = self.group.setdefault("games", {})
        g = games.get("soup")
        if not isinstance(g, dict):
            g = _blank()
            games["soup"] = g
        for k, v in _blank().items():
            g.setdefault(k, v)
        return g

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    # ------------------------------------------------------------ 开局 --
    def start(self, arg: str = "") -> str:
        g = self._g()
        if g.get("active"):
            return "已经有一碗汤在桌上了，先把它喝掉（或「海龟汤 揭晓」）喵。"
        pool = list(content.soups())
        if not pool:
            return "题库读不出来，先检查 data/soups.json 喵。"
        arg = str(arg or "").strip()
        if arg:
            sub = [p for p in pool if p["difficulty"] == arg]
            if not sub:
                return "难度只有 简单 / 普通 / 困难 三档喵。"
            pool = sub
        picked = _R.choice(pool)
        g.update(_blank())
        g["active"] = True
        g["surface"] = picked["surface"]
        g["truth"] = picked["truth"]
        g["difficulty"] = picked["difficulty"]
        g["ts"] = utils.ts()
        g["opener_name"] = self._name()
        tip = "" if self.llm else "\n（⚠️ 这台机器没配模型，只能「揭晓」，不能回答提问）"
        return (f"🍲 海龟汤（{picked['difficulty']}·{len(pool)} 题池）\n"
                f"━━━━━━━━━━━━━━\n{picked['surface']}\n"
                f"━━━━━━━━━━━━━━\n"
                f"发「海龟汤 问 他是自杀吗」来盘，每次 {ASK_COST} 堡贝。"
                f"想放弃就「海龟汤 揭晓」喵。{tip}")

    # ------------------------------------------------------------ 提问 --
    async def ask(self, question: str) -> str:
        g = self._g()
        q = str(question or "").strip()
        if not g.get("active"):
            return "现在桌上没有汤。发「海龟汤 开」喵。"
        if not q:
            return "问什么呀？格式：海龟汤 问 他是自杀吗"
        if self.llm is None:
            return "这台机器没配模型，判断不了「是/否」喵。只能发「海龟汤 揭晓」看答案。"
        asked = list(g.get("asked") or [])
        if len(asked) >= MAX_ASK:
            return f"这局已经问了 {MAX_ASK} 次，汤底就摆在那儿了，先揭晓吧喵。"
        if not self.w.spend(ASK_COST):
            return f"提问要 {ASK_COST} 堡贝，你只有 {utils.fmt_coins(self.w.coins)}。"

        prompt = (f"汤面：{g['surface']}\n"
                  f"汤底（玩家看不到）：{g['truth']}\n"
                  f"本轮玩家提问：{q}")
        answer = await self.llm(prompt, _SYSTEM)
        verdict = pick_verdict(answer)
        asked.append({"q": q, "a": verdict})
        g["asked"] = asked
        g["ts"] = utils.ts()
        return (f"🍲 问：{q}\n　答：{verdict}　（-{ASK_COST} 堡贝，"
                f"已问 {len(asked)} 次）")

    def log(self) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在桌上没有汤喵。"
        asked = list(g.get("asked") or [])
        if not asked:
            return "还没人提问。发「海龟汤 问 ...」喵。"
        lines = [f"🍲 已知（{len(asked)} 问）"]
        for item in asked[-10:]:
            lines.append(f"　{item.get('q')}　→　{item.get('a')}")
        return "\n".join(lines)

    def surface(self) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在桌上没有汤喵。"
        return f"🍲 汤面\n━━━━━━━━━━━━━━\n{g['surface']}\n━━━━━━━━━━━━━━"

    def reveal(self) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在桌上没有汤喵。"
        asked = len(g.get("asked") or [])
        surface, truth = g.get("surface"), g.get("truth")
        g.update(_blank())
        return (f"🍲 揭晓\n━━━━━━━━━━━━━━\n汤面：{surface}\n\n汤底：{truth}\n"
                f"━━━━━━━━━━━━━━\n这局问了 {asked} 次。")

    # ------------------------------------------------------------ 分发 --
    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        head, _, tail = text.partition(" ")
        head, tail = head.strip(), tail.strip()
        if head in ("规则", "玩法"):
            return self.rules_text()
        if head in ("开", "开局", "开始"):
            return self.start(tail)
        if head in ("问", "提问"):
            return await self.ask(tail or text)
        if head in ("汤面", "题面"):
            return self.surface()
        if head in ("已知", "记录", "线索"):
            return self.log()
        if head in ("揭晓", "答案", "放弃", "开奖"):
            return self.reveal()
        if not text:
            return self.surface()
        # 不带「问」也当提问，少打一个字不该被罚
        return await self.ask(text)

    @staticmethod
    def rules_text() -> str:
        n = len(content.soups())
        return (
            "🍲 海龟汤 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"题库 {n} 题，难度分 简单/普通/困难。\n"
            "只给「汤面」，真相（汤底）隐藏。大家轮流提**可以用是/否回答**的问题，"
            "小堡只答「是」「否」「无关」「是也不是」。\n"
            f"每次提问 {ASK_COST} 堡贝（防暴力穷举），单局最多 {MAX_ASK} 问。\n"
            f"{IDLE_SEC // 60} 分钟没人问自动过期。\n"
            "📋 指令：海龟汤 开｜海龟汤 开 困难｜海龟汤 问 他是自杀吗｜"
            "海龟汤 汤面｜海龟汤 已知｜海龟汤 揭晓｜海龟汤 规则"
        )
