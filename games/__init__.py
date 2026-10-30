# -*- coding: utf-8 -*-
"""玩法层。

每个游戏一个文件，共同约定：

    async def handle(ctx) -> str | None

`ctx` 由 main.py 传入，包含 group / user / wallet / store / llm / args。
游戏内部不碰文件、不碰锁——只改 `ctx.wallet` 和 `ctx.state`，
落盘由调用方统一在 `store.transaction()` 里完成。

这样拆的目的是让每个游戏的逻辑能单独跑测试：
不需要起 AstrBot，构造一个假的 ctx 就行。
"""
