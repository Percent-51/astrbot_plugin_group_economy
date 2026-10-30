# -*- coding: utf-8 -*-
"""题库加载。

两份数据跟着仓库走：`data/words.json`（猜词）和 `data/soups.json`（海龟汤）。
放仓库里而不是塞进代码，是因为**它们是内容不是逻辑**——想加词、换题、
甚至整份替换成自己的主题，都不该碰 Python。

读取一律走缓存，失败返回空列表：题库坏了游戏要能报错，但不能拖垮整个插件。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=4)
def _load(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    if not path.is_file():
        return {}
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError:
            return {}
    return {}


@lru_cache(maxsize=1)
def words() -> tuple[dict[str, Any], ...]:
    """猜词题库：[{a: 答案, c: 类别, d: 难度}]。"""
    raw = _load("words.json").get("words") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("a") or "").strip()
        if not answer:
            continue
        out.append({
            "a": answer,
            "c": str(item.get("c") or "名词"),
            "d": str(item.get("d") or "普通"),
        })
    return tuple(out)


@lru_cache(maxsize=1)
def categories() -> tuple[str, ...]:
    """按题库里出现的顺序返回类别，保证菜单顺序稳定。"""
    seen: list[str] = []
    for w in words():
        if w["c"] not in seen:
            seen.append(w["c"])
    return tuple(seen)


@lru_cache(maxsize=1)
def soups() -> tuple[dict[str, Any], ...]:
    """海龟汤题库：[{surface: 汤面, truth: 汤底, difficulty}]。"""
    raw = _load("soups.json").get("puzzles") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("surface") or "").strip()
        truth = str(item.get("truth") or "").strip()
        if not surface or not truth:
            continue
        out.append({
            "surface": surface,
            "truth": truth,
            "difficulty": str(item.get("difficulty") or "普通"),
        })
    return tuple(out)


def stats() -> dict[str, int]:
    return {"words": len(words()), "soups": len(soups()), "categories": len(categories())}
