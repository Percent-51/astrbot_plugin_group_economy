# -*- coding: utf-8 -*-
"""小堡剧场：丢一个主题，现场写一小段戏。

和「AI 讲个故事」的区别在于**角色是群里的人**：随机抽几个在场的人当角色，
让他们在戏里说蠢话。群友真正会转发的就是这个。

收费的逻辑
----------
一次 50 堡贝。这不是抠门——LLM 调用有真实成本，而且免费无限生成会让
同一个主题被刷十遍，群里全是一模一样的小剧场。收费让它变成「值得发一次
才发」的动作。
"""
from __future__ import annotations

import random
from typing import Any

from ..core import utils
from ..core.llm import LlmFn
from ..core.wallet import Wallet

TICKET = 50
MAX_CHARS = 400
MAX_ROLES = 4

_R = random.Random()

_SYSTEM = (
    "你是一个短剧编剧。根据用户给的主题和角色名，写一小段现场短剧。"
    "要求：纯对话体，不要旁白解说；4~8 轮；每轮以「名字：台词」格式；"
    "风格轻松、有梗、结尾要有一个反转或包袱；不要写任何解释性文字；"
    "总长度不超过 300 字。"
)


class Theater:
    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "", llm: LlmFn | None = None) -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")
        self.llm = llm

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    def _cast(self) -> list[str]:
        """从本群出现过的人里抽几个当角色，不够就用固定配角。"""
        users = self.group.get("users") or {}
        names = [str((u or {}).get("name") or "") for u in users.values() if isinstance(u, dict)]
        names = [n for n in names if n and n != self._name()][:40]
        _R.shuffle(names)
        cast = [self._name()] + names[: MAX_ROLES - 1]
        filler = ["小堡", "蟹老板", "路人甲", "扫地阿姨"]
        while len(cast) < 2:
            cast.append(filler[len(cast) % len(filler)])
        return cast

    async def play(self, theme: str) -> str:
        theme = str(theme or "").strip()
        if not theme:
            return ("要个主题呀喵。格式：剧场 两个人在抢最后一份蟹黄堡\n"
                    f"（一次 {TICKET} 堡贝）")
        if self.llm is None:
            return ("这台机器没配模型，开不了剧场喵。\n"
                    "在管理面板里配一个 provider 就能用了。")
        if not self.w.spend(TICKET):
            return f"看一场 {TICKET} 堡贝，你只有 {utils.fmt_coins(self.w.coins)}。"

        cast = self._cast()
        prompt = f"主题：{theme}\n角色：{'、'.join(cast)}\n请开始写。"
        text = await self.llm(prompt, _SYSTEM)
        if not text:
            # 模型失败了必须退钱，不然就是白扣
            self.w.add(TICKET)
            return "编剧今天罢工了（模型没返回内容），钱退你了喵。"

        text = text[:MAX_CHARS]
        return (f"🎭 小堡剧场 ·《{theme}》\n"
                f"━━━━━━━━━━━━━━\n{text}\n"
                f"━━━━━━━━━━━━━━\n演员：{'、'.join(cast)}　（-{TICKET} 堡贝）")

    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        if text in ("规则", "玩法"):
            return self.rules_text()
        return await self.play(text)

    @staticmethod
    def rules_text() -> str:
        return (
            "🎭 小堡剧场 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            f"丢一个主题，随机把群里几个人的名字编进一出短剧。一次 {TICKET} 堡贝。\n"
            "写不出来（模型失败）会退钱。\n"
            "📋 指令：剧场 两个人在抢最后一份蟹黄堡｜剧场 规则"
        )
