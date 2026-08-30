"""本地 Prompt/角色知识、热更新词典与角色发现缓存。"""
import json
from pathlib import Path
import re

import yaml

from server.runtime import CLIENT
from server.settings import (
    CFG,
    CHAR_AUTO_PATH,
    CHAR_DICT_PATH,
    CHAR_LOOKUP_PATH,
    DICT_PATH,
    KNOWLEDGE_CACHE_DIR,
)


class HotDict:
    """YAML 词典 + mtime 热更新: 文件存盘后下次访问自动重载, 不用重启后端 (见 D21).
    用法等同 dict (.get / .items). 每次访问 stat 一下 mtime, 没变就跳过 (微秒级).
    解析失败 (如存盘写到一半 / YAML 笔误) 保留旧词典并打印警告, 不阻断翻译."""
    def __init__(self, path: Path, key_fn=str.lower):
        self.path = path
        self.key_fn = key_fn
        self._mtime = 0.0
        self._d: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            return
        try:
            m = self.path.stat().st_mtime
            if m == self._mtime:
                return
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            # 整体重建再整体赋值: 读端要么见旧要么见新, 不会读到半成品
            self._d = {self.key_fn(str(k).strip()): str(v).strip() for k, v in raw.items() if v is not None}
            self._mtime = m
        except Exception as e:
            print(f"[HotDict] 重载 {self.path.name} 失败, 保留旧词典: {e}", flush=True)

    def get(self, key):
        self.reload()
        return self._d.get(key)

    def items(self):
        self.reload()
        return self._d.items()

DICT = HotDict(DICT_PATH, key_fn=str.lower)
CHAR_DICT = HotDict(CHAR_DICT_PATH, key_fn=lambda value: value)
CHAR_AUTO = HotDict(CHAR_AUTO_PATH, key_fn=lambda value: value)
_CHAR_LOOKUP_CACHE: dict[str, dict] = {}
_CHAR_LOOKUP_CACHE_LOADED = False
CHARACTER_AUTO_MIN_POSTS = int(CFG.get("character_auto_min_posts", 100))


def _character_items():
    """历史正式角色词典优先，其次是 Danbooru exact 确认过的自动缓存。"""
    formal = list(CHAR_DICT.items())
    auto = list(CHAR_AUTO.items())
    formal_names = {name for name, _ in formal}
    items = formal + [(name, tag) for name, tag in auto if name not in formal_names]
    for name, tag in sorted(items, key=lambda item: len(item[0]), reverse=True):
        yield name, tag

def _character_names():
    return [name for name, _ in _character_items()]

def _normalize_character_candidate(item: str) -> str | None:
    """把候选归一化成 Danbooru 下划线 canonical 形式；非角色名返回 None."""
    s = str(item).strip()
    if not s:
        return None
    if "_(" in s or " (" in s:
        return s
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[ _][a-z][a-z0-9]*)+", s):
        return s.replace(" ", "_")
    return None

def _classify_danbooru_rows(rows: list[dict], candidate_tag: str,
                            min_posts: int = CHARACTER_AUTO_MIN_POSTS) -> dict:
    """用 exact canonical tag、角色分类和 post_count 判断是否值得自动缓存."""
    exact = next((row for row in rows if row.get("name") == candidate_tag), None)
    if not exact or exact.get("is_deprecated") or exact.get("category") != 4:
        return {"status": "absent", "canonical_tag": "", "post_count": 0}
    post_count = int(exact.get("post_count") or 0)
    return {
        "status": "likely_supported" if post_count >= min_posts else "weak",
        "canonical_tag": candidate_tag,
        "post_count": post_count,
    }

def _load_character_lookup_cache() -> None:
    global _CHAR_LOOKUP_CACHE_LOADED, _CHAR_LOOKUP_CACHE
    if _CHAR_LOOKUP_CACHE_LOADED:
        return
    _CHAR_LOOKUP_CACHE_LOADED = True
    if not CHAR_LOOKUP_PATH.exists():
        return
    try:
        data = json.loads(CHAR_LOOKUP_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _CHAR_LOOKUP_CACHE = data
    except Exception as exc:
        print(f"[character lookup] cache load failed: {exc}", flush=True)

def _save_character_lookup_cache() -> None:
    KNOWLEDGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CHAR_LOOKUP_PATH.write_text(
        json.dumps(_CHAR_LOOKUP_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def _record_auto_character(name: str, canonical_tag: str) -> None:
    """只写入独立 auto cache，不覆盖正式 char_dict.yaml。"""
    if CHAR_DICT.get(name):
        return
    KNOWLEDGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if CHAR_AUTO_PATH.exists():
        try:
            data = yaml.safe_load(CHAR_AUTO_PATH.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(f"[character lookup] auto cache load failed: {exc}", flush=True)
    data[name] = canonical_tag
    CHAR_AUTO_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

async def lookup_character(name: str, candidate_tag: str) -> dict:
    """验证新角色候选；失败只返回状态，不阻断主 Prompt 流程."""
    _load_character_lookup_cache()
    key = f"{name}|{candidate_tag}"
    if key in _CHAR_LOOKUP_CACHE:
        return _CHAR_LOOKUP_CACHE[key]
    result = {
        "name": name,
        "candidate_tag": candidate_tag,
        "canonical_tag": "",
        "post_count": 0,
        "status": "absent",
        "source": "danbooru",
        "error": "",
    }
    if not re.fullmatch(r"[A-Za-z0-9_():+'\-]+", candidate_tag or ""):
        result["error"] = "invalid candidate tag"
    else:
        try:
            response = await CLIENT.get(
                "https://danbooru.donmai.us/tags.json",
                params={
                    "search[name_matches]": candidate_tag,
                    "search[order]": "post_count",
                    "limit": 20,
                },
                headers={"User-Agent": "AirPaint-character-lookup/1.0"},
                timeout=12,
            )
            if response.status_code != 200:
                result["status"] = "unavailable"
                result["error"] = f"HTTP {response.status_code}"
            else:
                result.update(_classify_danbooru_rows(response.json(), candidate_tag))
        except Exception as exc:
            result["status"] = "unavailable"
            result["error"] = str(exc)
    # Network failures are transient; do not poison the cache permanently.
    if result["status"] != "unavailable":
        _CHAR_LOOKUP_CACHE[key] = result
        try:
            _save_character_lookup_cache()
        except Exception as exc:
            print(f"[character lookup] cache save failed: {exc}", flush=True)
    if result["status"] == "likely_supported":
        _record_auto_character(name, result["canonical_tag"])
    return result

def match_characters(text: str) -> tuple[list[str], str]:
    """子串匹配历史正式/自动 exact 角色名. 正式词典优先, 返回 tag 列表和剩余文本."""
    found_tags: list[str] = []
    remaining = text
    for name, tag in _character_items():
        if name in text:
            found_tags.append(tag)
            remaining = remaining.replace(name, "")
    return found_tags, remaining

def match_dict_words(text: str) -> tuple[list[str], str]:
    """子串匹配属性/感觉词典(最长优先, len>=2 防"白"匹配"白天"误伤). 返回 (tag 列表, 移除命中后的剩余).
    修: 原精确匹配逗号段, NSFW 词嵌在短语里("裸足少女")不命中 -> 走 LLM -> Qwen3 安全过滤丢词 (见 D26).
    最长优先: 先匹配长 key("白天"->daytime) 再短 key("白"->white), 避免短 key 先吃掉长 key 的一部分."""
    hits: list[str] = []
    remaining = text
    for key, val in sorted(DICT.items(), key=lambda x: len(x[0]), reverse=True):
        if len(key) < 2:
            continue  # 跳过单字键, 防子串误伤
        if key in remaining:
            hits.extend(t.strip() for t in str(val).split(",") if t.strip())
            remaining = remaining.replace(key, "")
    return hits, remaining
