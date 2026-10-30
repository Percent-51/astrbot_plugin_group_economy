# -*- coding: utf-8 -*-
"""群聊经济体系 —— 入口。

职责只有三件：
1. 初始化存储与内核
2. 把群消息解析成「命令 + 参数」
3. 分发给 core（银行）或 games（游戏），拼返回文本

具体玩法一律不写在这个文件里。判断标准很简单：**这个函数需要知道
某个游戏的规则吗？** 需要，就它属于 games/。

设计取舍
--------
命令不做前缀强制：群友记不住「插件名：子命令」这种形式，
所以用最短的自然词（堡贝 / 签到 / 银行 / 彩票），
配合一张别名表兜住「堡币」「钱包」这类说法。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core import utils
from .core.store import Store
from .core.wallet import Wallet, total_wealth

PLUGIN_NAME = "astrbot_plugin_group_economy"
VERSION = "v0.1.0"

DEFAULT_COINS = 500
DEFAULT_DAILY_CAP = 300
CHECKIN_REWARD = 50

# 命令别名：左边是群友可能说的话，右边是内部命令名。
ALIASES: dict[str, str] = {
    "堡贝": "钱包", "堡币": "钱包", "余额": "钱包", "我的堡贝": "钱包",
    "经济菜单": "经济", "经济帮助": "经济", "玩法": "经济",
    "每日签到": "签到", "打卡": "签到",
    "银行": "银行", "小堡银行": "银行", "存钱": "银行", "取钱": "银行",
    "彩票": "彩票", "刮刮乐": "彩票",
    "炸弹": "炸弹", "小堡炸弹": "炸弹",
    "接龙": "故事接龙", "故事接龙": "故事接龙",
    "剧场": "小堡剧场", "小堡剧场": "小堡剧场",
    "猜猜猜": "一起猜猜猜", "一起猜猜猜": "一起猜猜猜",
    "海龟汤": "海龟汤", "汤": "海龟汤",
}

SUPPORTED = {
    "经济", "钱包", "签到", "排行",
    "银行", "彩票", "炸弹", "故事接龙", "小堡剧场", "一起猜猜猜", "海龟汤",
}


def _menu_text() -> str:
    return (
        "🏦 群聊经济体系\n"
        "────────────────\n"
        "💰 堡贝　　查余额\n"
        "📅 签到　　每天领 50 堡贝\n"
        "🏦 银行　　存钱吃利息 / 借钱 / 高利贷\n"
        "🎰 彩票　　刮刮乐 · 每日幸运号\n"
        "💣 炸弹　　扫雷局，踩雷的人赔钱\n"
        "📖 故事接龙　全群一人一句编故事\n"
        "🎭 小堡剧场　丢个主题，现场编群聊\n"
        "🎯 一起猜猜猜　猜一个藏起来的词\n"
        "🍲 海龟汤　　只问是/否，盘出真相\n"
        "🏆 排行　　　　存钱榜 / 破产榜\n"
        "────────────────\n"
        "堡贝是本插件内的货币，与任何其他插件不互通。"
    )


class GroupEconomyPlugin(Star):
    def __init__(self, context: Context, config: "AstrBotConfig | dict | None" = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "economy.json", initial_coins=self.initial_coins)
        self._boot = asyncio.Event()
        self._boot_task: asyncio.Task | None = None
        try:
            self._boot_task = asyncio.create_task(self._startup())
        except RuntimeError:
            self._boot_task = None
        logger.info("[GroupEconomy] %s 已加载：%s", VERSION, self.data_dir)

    async def _startup(self) -> None:
        try:
            await self.store.load()
        except Exception as exc:  # 存档坏了不能拖垮整个插件
            logger.error("[GroupEconomy] 存档加载失败，本次运行使用空存档：%s", exc)
            self.store.data = {"_version": 1, "groups": {}}
        finally:
            self._boot.set()

    async def _ready(self) -> None:
        if not self._boot.is_set():
            await self._boot.wait()

    # ---------------------------------------------------------------- 配置 --
    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def daily_cap(self) -> int:
        return self._cfg_int("daily_cap", DEFAULT_DAILY_CAP)

    @property
    def initial_coins(self) -> int:
        return self._cfg_int("initial_coins", DEFAULT_COINS)

    @property
    def checkin_reward(self) -> int:
        return self._cfg_int("checkin_reward", CHECKIN_REWARD)

    def _wallet(self, gid: str, uid: str, name: str) -> Wallet:
        return Wallet(self.store.user(gid, uid, name), self.daily_cap)

    @staticmethod
    def _parse(raw: str) -> tuple[str, str]:
        text = (raw or "").strip().lstrip("/／").strip()
        for sep in (":", "："):
            if sep in text:
                head, _, tail = text.partition(sep)
                return head.strip(), tail.strip()
        head, _, tail = text.partition(" ")
        return head.strip(), tail.strip()

    # ---------------------------------------------------------------- 命令 --
    def _cmd_wallet(self, gid: str, uid: str, name: str) -> str:
        u = self.store.user(gid, uid, name)
        w = Wallet(u, self.daily_cap)
        bank = u.get("bank") or {}
        dep = int(bank.get("dep", 0) or 0)
        lines = [
            f"💰 {name or uid} 的账户",
            f"现金：{utils.fmt_coins(w.coins)} 堡贝",
            f"存款：{utils.fmt_coins(dep)} 堡贝",
            f"总资产：{utils.fmt_coins(total_wealth(u))} 堡贝",
        ]
        if self.daily_cap > 0:
            lines.append(f"今日已赚：{w.earned_today} / {self.daily_cap}")
        return "\n".join(lines)

    def _cmd_checkin(self, gid: str, uid: str, name: str) -> str:
        u = self.store.user(gid, uid, name)
        day = utils.today()
        daily = u.setdefault("daily", {})
        if str(daily.get("checkin_day") or "") == day:
            now_coins = Wallet(u, self.daily_cap).coins
            return f"今天已经签到过了，明天再来喵。\n账上 {now_coins} 堡贝还在。"
        daily["checkin_day"] = day
        w = Wallet(u, self.daily_cap)
        granted, capped = w.earn(self.checkin_reward)
        tail = f"（今日已达上限，只到账 {granted}）" if capped else ""
        return (
            f"📅 签到成功{tail}\n"
            f"+{granted} 堡贝　当前 {w.coins} 堡贝"
        )

    def _cmd_rank(self, gid: str) -> str:
        g = self.store.group(gid)
        users = g.get("users") or {}
        if not users:
            return "还没有人开户，发「签到」开个户先喵。"
        rows = sorted(
            ((str(uid), u) for uid, u in users.items() if isinstance(u, dict)),
            key=lambda kv: total_wealth(kv[1]),
            reverse=True,
        )
        lines = ["🏆 财富榜"]
        for i, (uid, u) in enumerate(rows[:10], 1):
            nm = str(u.get("name") or uid)
            lines.append(f"{i}. {nm}　{utils.fmt_coins(total_wealth(u))} 堡贝")
        broke = [u for _, u in rows if int((u.get("stats") or {}).get("bankrupt", 0) or 0) > 0]
        if broke:
            lines.append("")
            lines.append("🪦 破产榜")
            for u in broke[:5]:
                n = int((u.get("stats") or {}).get("bankrupt", 0) or 0)
                lines.append(f"· {u.get('name') or '匿名'}　破产 {n} 次")
        return "\n".join(lines)

    # -------------------------------------------------------------- 事件入口 --
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE,
        priority=20000,
    )
    async def on_message(self, event: AstrMessageEvent):
        await self._ready()
        raw = event.get_message_str() or ""
        cmd, arg = self._parse(raw)
        cmd = ALIASES.get(cmd, cmd)
        if cmd not in SUPPORTED:
            return

        gid = str(event.get_group_id() or "")
        uid = str(event.get_sender_id() or "")
        name = str(event.get_sender_name() or "").strip()
        is_private = not gid
        if is_private:
            # 单个私聊没有「群经济」概念，用固定桶承载，避免读写时报错
            gid = f"private:{uid}"

        try:
            if cmd == "经济":
                text = _menu_text()
            elif cmd == "钱包":
                text = self._cmd_wallet(gid, uid, name)
            elif cmd == "排行":
                text = self._cmd_rank(gid)
            elif cmd == "签到":
                async with self.store.transaction():
                    text = self._cmd_checkin(gid, uid, name)
            else:
                # 其余交给 games 层；本版尚未接入，先给明确回执而不是静默
                text = f"「{cmd}」这个玩法还在接，先记着喵。"
            event.stop_event()
            yield event.plain_result(text)
        except Exception as exc:
            logger.error("[GroupEconomy] 处理 %s 失败：%s", cmd, exc, exc_info=True)
            event.stop_event()
            yield event.plain_result("小堡算错了账，等一下再试喵。")

    async def terminate(self) -> None:
        task = self._boot_task
        self._boot_task = None
        if task and not task.done():
            task.cancel()
        if self.store.data:
            try:
                self.store.save()
            except Exception as exc:
                logger.warning("[GroupEconomy] 退出落盘失败：%s", exc)
