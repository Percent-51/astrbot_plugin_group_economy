# -*- coding: utf-8 -*-
"""故事接龙：一人一句，把故事编下去。

这个玩法没有随机性、没有 LLM、没有输赢，却是群里最容易自来水的一个——
因为它让每句话都留名。别人写的东西会逼着你想下一句，这是社群内容
最好的生产方式。

给钱是为了给理由：每接一句 +3 堡贝。金额小到不能刷，但足够让潜水的人
愿意接一句。
"""
from __future__ import annotations

import random
from typing import Any

from ..core import utils
from ..core.wallet import Wallet

LINE_REWARD = 3
MAX_LINE_CHARS = 60
MAX_LINES = 40
IDLE_SEC = 60 * 60

OPENERS = (
    "深夜的便利店里，最后一盏灯忽然闪了三下。",
    "他在旧货市场买了一个闹钟，回家才发现它每天都会提前响一分钟。",
    "整栋楼只有七楼还亮着灯，而那里已经空了三年。",
    "雨停了，屋檐下站着一个撑着伞的人。",
    "小堡掀开锅盖，里面的汤还是热的——可这间厨房已经关了一个月。",
    "所有人都说自己看见了那颗流星，只有她听见了落地声。",
    "快递单上的寄件人那一栏，写的是他自己的名字。",
    "她在同学录里翻到一页没写名字的留言：「十年后见。」",
)

_R = random.Random()


def _blank() -> dict[str, Any]:
    return {"active": False, "lines": [], "ts": 0, "opener_name": ""}


class Story:
    """一个群的接龙。状态落在 group["games"]["story"]。"""

    def __init__(self, user: dict[str, Any], group: dict[str, Any],
                 wallet: Wallet, uid: str = "") -> None:
        self.user = user
        self.group = group
        self.w = wallet
        self.uid = str(uid or user.get("name") or "?")

    def _g(self) -> dict[str, Any]:
        games = self.group.setdefault("games", {})
        g = games.get("story")
        if not isinstance(g, dict):
            g = _blank()
            games["story"] = g
        for k, v in _blank().items():
            g.setdefault(k, v)
        return g

    def _name(self) -> str:
        return str(self.user.get("name") or "无名")

    def start(self, seed: str = "") -> str:
        g = self._g()
        if g.get("active"):
            n = len(g.get("lines") or [])
            return f"已经在接龙了（{n} 句）。发「接龙 你的句子」接着写，或「接龙 收尾」喵。"
        seed = str(seed or "").strip()[:MAX_LINE_CHARS]
        if not seed:
            seed = _R.choice(OPENERS)
        g.update(_blank())
        g["active"] = True
        g["lines"] = [{"name": "开场", "text": seed}]
        g["ts"] = utils.ts()
        g["opener_name"] = self._name()
        return (f"📖 故事接龙开始了\n━━━━━━━━━━━━━━\n{seed}\n━━━━━━━━━━━━━━\n"
                f"发「接龙 你的下一句」接着写，每句 +{LINE_REWARD} 堡贝喵。"
                f"（最多 {MAX_LINE_CHARS} 字）")

    def push(self, text: str) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在没有接龙。发「接龙 开」起个头喵。"
        text = str(text or "").strip()
        if not text:
            return "接什么呀？格式：接龙 他决定推开门"
        if len(text) > MAX_LINE_CHARS:
            return f"一句话最多 {MAX_LINE_CHARS} 字喵，太长了别人接不动。"
        lines = list(g.get("lines") or [])
        if len(lines) >= MAX_LINES:
            return f"已经 {MAX_LINES} 句了，该收尾啦。发「接龙 收尾」喵。"
        last = str((lines[-1] or {}).get("text") or "")
        if text == last:
            return "这句上一个人刚说过喵。"

        lines.append({"name": self._name(), "text": text})
        g["lines"] = lines
        g["ts"] = utils.ts()
        granted, capped = self.w.earn(LINE_REWARD)
        tail = "（已到每日上限）" if capped else f" +{granted}"
        return f"📖 第 {len(lines) - 1} 句已接{tail}\n{text}"

    def show(self) -> str:
        g = self._g()
        lines = list(g.get("lines") or [])
        if not lines:
            return "现在没有接龙喵。"
        body = "\n".join(
            (str(item.get("text")) if item.get("name") == "开场"
             else f"{item.get('text')}　——{item.get('name')}")
            for item in lines)
        return f"📖 当前故事（{len(lines) - 1 if g.get('active') else len(lines)} 句）\n{body}"

    def close(self) -> str:
        g = self._g()
        if not g.get("active"):
            return "现在没有接龙喵。"
        lines = list(g.get("lines") or [])
        authors = [str(i.get("name")) for i in lines if i.get("name") != "开场"]
        uniq = []
        for a in authors:
            if a not in uniq:
                uniq.append(a)
        body = "\n".join(
            (str(item.get("text")) if item.get("name") == "开场"
             else f"{item.get('text')}　——{item.get('name')}")
            for item in lines)
        g.update(_blank())
        who = "、".join(uniq[:8]) or "无人"
        return (f"📖 收尾了！{len(lines) - 1} 句，{len(uniq)} 人参与（{who}）\n"
                f"━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━\n下次再来编喵。")

    # ------------------------------------------------------------ 分发 --
    async def handle(self, arg: str) -> str:
        text = str(arg or "").strip()
        head, _, tail = text.partition(" ")
        head, tail = head.strip(), tail.strip()
        if head in ("规则", "玩法"):
            return self.rules_text()
        if head in ("开", "开局", "开始"):
            return self.start(tail)
        if head in ("收尾", "结束", "完"):
            return self.close()
        if head in ("看", "全文", "回顾") or not text:
            return self.show()
        if head in ("接", "续"):
            return self.push(tail) if tail else "接什么呀？格式：接龙 他推开门"
        # 没写「接」也当接龙内容，别让人因为少打一个字就白写
        return self.push(text)

    @staticmethod
    def rules_text() -> str:
        return (
            "📖 故事接龙 · 规则\n"
            "━━━━━━━━━━━━━━\n"
            "一个人起头，其他人一人一句往下接，最后合成一个完整故事。\n"
            f"每接一句 +{LINE_REWARD} 堡贝（计入每日上限），单句最多 {MAX_LINE_CHARS} 字，"
            f"最多 {MAX_LINES} 句。\n"
            f"{IDLE_SEC // 60} 分钟没人接自动收尾。\n"
            "📋 指令：接龙 开｜接龙 开 <自己写的开头>｜接龙 你的句子｜接龙 看｜接龙 收尾｜接龙 规则"
        )
