# -*- coding: utf-8 -*-
"""LLM 调用的唯一入口。

为什么单独一层
--------------
AstrBot 的 provider 接口在不同版本间动过（`text_chat` / `text_chat_stream`
/ 返回对象里叫 `completion_text` 还是 `content`）。游戏模块不该关心这些，
所以这里用**鸭子类型**兜住：有什么方法就调什么，返回文本统一成 str。

拿不到 provider 时返回 None，由调用方降级——**绝不能因为没配模型就崩**，
这是插件最常见的差评来源（「装了没反应」）。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str | None]]


def _pick_text(resp: Any) -> str:
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp.strip()
    for attr in ("completion_text", "text", "content"):
        val = getattr(resp, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # 有些版本返回 dict
    if isinstance(resp, dict):
        for key in ("completion_text", "text", "content"):
            val = resp.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def make_caller(context: Any, max_chars: int = 600) -> LlmFn | None:
    """从 AstrBot context 造一个 `async (prompt, system) -> str|None`。

    provider 不可用时返回 None，调用方据此走降级文案。
    """
    getter = getattr(context, "get_using_provider", None)
    if not callable(getter):
        return None
    try:
        provider = getter()
    except Exception as exc:  # provider 未配置时这里就会抛
        logger.info("[group_economy] 没有可用的 LLM provider：%s", exc)
        return None
    if provider is None:
        return None

    async def call(prompt: str, system: str = "") -> str | None:
        kwargs: dict[str, Any] = {"prompt": prompt}
        if system:
            kwargs["system_prompt"] = system
        try:
            chat = getattr(provider, "text_chat", None)
            if chat is None:
                chat = getattr(provider, "text_chat_stream", None)
            if chat is None:
                return None
            resp = chat(**kwargs)
            if hasattr(resp, "__await__"):
                resp = await resp
            # 流式返回时结果是生成器，逐块拼
            if hasattr(resp, "__aiter__"):
                chunks = []
                async for part in resp:
                    chunks.append(_pick_text(part))
                return "".join(chunks).strip()[:max_chars] or None
            text = _pick_text(resp)
            return text[:max_chars] or None
        except Exception as exc:
            logger.warning("[group_economy] LLM 调用失败：%s", exc)
            return None

    return call
