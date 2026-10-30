# -*- coding: utf-8 -*-
"""时间与格式化工具。

全项目统一走北京时间（UTC+8）：群友看的是自己的表，
用 UTC 会出现「今天签到过了」在 08:00 才重置这种反直觉现象。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    """当前北京时间。"""
    return datetime.now(TZ)


def today() -> str:
    """今天的日期键（YYYY-MM-DD），用作 daily 分桶。"""
    return now().date().isoformat()


def ts() -> int:
    """当前 Unix 时间戳（秒）。"""
    return int(now().timestamp())


def fmt_coins(n: int) -> str:
    """堡贝展示：三档量级用不同单位，避免长串数字看不清。"""
    n = int(n or 0)
    if abs(n) >= 100_000_000:
        return f"{n / 100_000_000:.2f}亿"
    if abs(n) >= 10_000:
        return f"{n / 10_000:.2f}万"
    return str(n)


def duration_cn(seconds: float) -> str:
    """把秒数写成人话：3天2小时 / 5小时 / 12分钟。"""
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        d, rem = divmod(seconds, 86400)
        h = rem // 3600
        return f"{d}天{h}小时" if h else f"{d}天"
    if seconds >= 3600:
        return f"{seconds // 3600}小时"
    if seconds >= 60:
        return f"{seconds // 60}分钟"
    return f"{seconds}秒"
