# -*- coding: utf-8 -*-
"""状态存储：单文件 JSON + 原子写 + 协程锁。

为什么不用数据库
----------------
群数量是几十个量级，单文件能直接打开看、能直接备份、出问题能手工修。
数据量真到十万条再换 sqlite 也不迟——过早引入数据库只会让部署变重。

两条硬约束（都是踩过坑才加的）
------------------------------
1. **原子写**：先写 `.tmp` 再 `os.replace()`。直接覆写时进程被杀会留下
   半个坏文件，整群存档一次性报废。
2. **多编码回退**读：某些平台写出的 JSON 带 BOM，`json.load` 会抛
   `Unexpected UTF-8 BOM`，表现是插件启动即崩。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from . import utils

SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict[str, Any]:
    """按 utf-8-sig / utf-8 / gbk / latin-1 依次尝试，全失败返回空结构。"""
    if not path.is_file():
        return {}
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            raw = path.read_text(encoding=enc)
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError:
            return {}
    return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写：同目录 `.tmp` → `os.replace`。

    不用 `Path.write_text`：它在 Windows 上会把 `\\n` 转成 `\\r\\n`，
    仓库里的文件会莫名其妙变成 CRLF。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    with open(tmp, "wb") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def blank_group() -> dict[str, Any]:
    return {
        "global": {"created_at": utils.ts(), "rp_king": {}},
        "users": {},
        "redpackets": {},
        "games": {},
        "lottery": {},
    }


def blank_user(name: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "coins": 0,
        "earned": {"day": "", "amount": 0},
        "bank": {
            "dep": 0,
            "dep_ts": 0,
            "loan": None,
            "shark": None,
            "history": [],
            "coupon": True,
            "first_dep": False,
        },
        "stats": {
            "games": 0,
            "earned": 0,
            "spent": 0,
            "bankrupt": 0,
            "joined_at": utils.ts(),
        },
    }


class Store:
    """一层轻封装：负责落盘、并发保护、以及「取不到就建」。"""

    def __init__(self, path: str | Path, initial_coins: int = 0) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._loaded = False
        self._dirty = False
        # 新用户开户时直接给的启动资金。放在 Store 上而不是 user() 参数里，
        # 是因为调用方（main / 各游戏）不该关心「这个人是不是第一次来」。
        self.initial_coins = max(0, int(initial_coins))

    # ---------------------------------------------------------- 生命周期 --
    async def load(self) -> None:
        async with self._lock:
            raw = _read_json(self.path)
            if not raw:
                raw = {"_version": SCHEMA_VERSION, "groups": {}}
            raw.setdefault("_version", SCHEMA_VERSION)
            raw.setdefault("groups", {})
            self.data = raw
            self._loaded = True
            await self._migrate()

    async def _migrate(self) -> None:
        """结构升级钩子。当前只有 v1，占位以便将来加字段不破坏老存档。"""
        if int(self.data.get("_version", 0)) > SCHEMA_VERSION:
            # 存档来自更新版本：不强行降级，保留原样只读运行
            return
        self.data["_version"] = SCHEMA_VERSION

    @contextlib.asynccontextmanager
    async def lock(self) -> "Iterator[Store]":
        """所有「读-改-写」都要包在这里面。

        `save()` 内部不拿锁——由本上下文保证互斥，否则会自己等自己。
        """
        async with self._lock:
            yield self

    # -------------------------------------------------------------- 落盘 --
    def save(self) -> None:
        """同步落盘。必须在 `lock()` 内调用，否则并发写会互相覆盖。"""
        if not self._loaded:
            return
        _write_json(self.path, self.data)
        self._dirty = False

    @contextlib.asynccontextmanager
    async def transaction(self) -> "Iterator[Store]":
        """拿锁 → 改 → 落盘。中途抛异常则不落盘（保持内存与磁盘一致）。"""
        async with self._lock:
            yield self
            self.save()

    # -------------------------------------------------------------- 取数 --
    def group(self, gid: str) -> dict[str, Any]:
        groups = self.data.setdefault("groups", {})
        g = groups.get(str(gid))
        if not isinstance(g, dict):
            g = blank_group()
            groups[str(gid)] = g
        for key, default in blank_group().items():
            g.setdefault(key, default)
        return g

    def user(self, gid: str, uid: str, name: str = "") -> dict[str, Any]:
        g = self.group(gid)
        users = g.setdefault("users", {})
        u = users.get(str(uid))
        if not isinstance(u, dict):
            u = blank_user(name)
            u["coins"] = self.initial_coins
            users[str(uid)] = u
        base = blank_user()
        for key, default in base.items():
            u.setdefault(key, default)
        for key, default in base["bank"].items():
            u["bank"].setdefault(key, default)
        for key, default in base["stats"].items():
            u["stats"].setdefault(key, default)
        if name and u.get("name") != name:
            u["name"] = name
        return u

    def drop_group(self, gid: str) -> bool:
        return self.data.get("groups", {}).pop(str(gid), None) is not None
