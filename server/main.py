# -*- coding: utf-8 -*-
"""ComfyUI Web MVP 后端网关
职责: 鉴权 / 限流 / 排队(并发=1) / 中文->tag 翻译 / 内容过滤 / 调用 ComfyUI API / 返回图片
"""
import asyncio
import base64
import hashlib
import json
import random
import re
import time
import uuid
from pathlib import Path

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
DICT_PATH = BASE / "dict.yaml"
CHAR_DICT_PATH = BASE / "char_dict.yaml"
KNOWLEDGE_CACHE_DIR = BASE / "knowledge_cache"
CHAR_AUTO_PATH = KNOWLEDGE_CACHE_DIR / "characters_auto.yaml"
CHAR_LOOKUP_PATH = KNOWLEDGE_CACHE_DIR / "characters_lookup.json"
LORA_REGISTRY_PATH = BASE / "lora_registry.yaml"


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


class HotLoraRegistry:
    """保留嵌套结构的 LoRA Registry 热加载器。

    与 HotDict 不同，这里不能把 value 转字符串。每次变更先完整解析和校验，
    通过后才原子替换内存快照；半写入/坏 YAML 继续使用上一份有效数据。
    """
    def __init__(self, path: Path):
        self.path = path
        self._mtime_ns = -1
        self._data: dict = {"schema_version": 1, "loras": {}}
        self._revision = hashlib.sha256(b"empty-lora-registry").hexdigest()[:16]
        self.reload()

    @staticmethod
    def validate(raw: dict) -> None:
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("schema_version 必须为 1")
        loras = raw.get("loras")
        if not isinstance(loras, dict):
            raise ValueError("loras 必须是对象")
        for key, asset in loras.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(asset, dict):
                raise ValueError("LoRA key/asset 格式错误")
            for field in ("name", "type", "file", "trigger_policy"):
                if not isinstance(asset.get(field), str) or not asset[field].strip():
                    raise ValueError(f"{key}.{field} 缺失或不是字符串")
            if asset["trigger_policy"] not in {"profile", "required", "none"}:
                raise ValueError(f"{key}.trigger_policy 非法")
            strength = asset.get("default_strength") or {}
            if not isinstance(strength, dict):
                raise ValueError(f"{key}.default_strength 必须是对象")
            for field in ("model", "clip"):
                try:
                    value = float(strength.get(field, 1.0))
                except (TypeError, ValueError):
                    raise ValueError(f"{key}.default_strength.{field} 非数字")
                if not 0 <= value <= 2:
                    raise ValueError(f"{key}.default_strength.{field} 超出 0~2")
            policy = asset["trigger_policy"]
            if policy == "profile":
                profiles = asset.get("profiles")
                if not isinstance(profiles, dict) or not profiles:
                    raise ValueError(f"{key}.profiles 不能为空")
                selection = asset.get("selection") or {}
                if not isinstance(selection, dict):
                    raise ValueError(f"{key}.selection 必须是对象")
                allow_multiple = selection.get("allow_multiple_profiles", False)
                if not isinstance(allow_multiple, bool):
                    raise ValueError(f"{key}.selection.allow_multiple_profiles 必须是布尔值")
                default_profile = selection.get("default_profile")
                if default_profile and default_profile not in profiles:
                    raise ValueError(f"{key}.selection.default_profile 不存在")
                for pid, profile in profiles.items():
                    if not isinstance(profile, dict) or not isinstance(profile.get("name"), str):
                        raise ValueError(f"{key}.profiles.{pid} 格式错误")
                    for list_field in ("aliases", "provides", "required_tags", "default_tags"):
                        value = profile.get(list_field, [])
                        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                            raise ValueError(f"{key}.profiles.{pid}.{list_field} 必须是字符串数组")
                    optional = profile.get("optional_tags") or {}
                    if not isinstance(optional, dict):
                        raise ValueError(f"{key}.profiles.{pid}.optional_tags 必须是对象")
                    for oid, option in optional.items():
                        if not isinstance(option, dict):
                            raise ValueError(f"{key}.{pid}.optional_tags.{oid} 格式错误")
                        aliases = option.get("aliases") or []
                        if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
                            raise ValueError(f"{key}.{pid}.optional_tags.{oid}.aliases 必须是字符串数组")
                        tags = option.get("tags") or []
                        if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
                            raise ValueError(f"{key}.{pid}.optional_tags.{oid}.tags 必须是字符串数组")
            else:
                tags = asset.get("required_tags") or []
                if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
                    raise ValueError(f"{key}.required_tags 必须是字符串数组")

    def reload(self) -> None:
        if not self.path.exists():
            return
        try:
            mtime_ns = self.path.stat().st_mtime_ns
            if mtime_ns == self._mtime_ns:
                return
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self.validate(raw)
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self._data = raw
            self._revision = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            self._mtime_ns = mtime_ns
        except Exception as e:
            print(f"[LoRA Registry] 重载失败, 保留旧版本: {e}", flush=True)

    def snapshot(self) -> tuple[dict, str]:
        self.reload()
        return self._data, self._revision


# 属性/感觉词典: 中文 -> danbooru tag. key 小写匹配.
DICT = HotDict(DICT_PATH, key_fn=str.lower)
# 角色词典: 中文名 -> danbooru 精确 tag. LLM 认不准角色 tag (字面翻译/编造/漏认),
# 故角色走词典可靠命中, 命中后把 tag 作为上下文喂给 LLM (见 decisions.md D12). key 不小写 (中文无大小写).
CHAR_DICT = HotDict(CHAR_DICT_PATH, key_fn=lambda s: s)
# 自动角色缓存保持与正式词典同样的平铺格式；正式 CHAR_DICT 优先。
CHAR_AUTO = HotDict(CHAR_AUTO_PATH, key_fn=lambda s: s)
_CHAR_LOOKUP_CACHE: dict[str, dict] = {}
_CHAR_LOOKUP_CACHE_LOADED = False
CHARACTER_AUTO_MIN_POSTS = int(CFG.get("character_auto_min_posts", 100))

COMFY = CFG["comfy_url"].rstrip("/")
TOKENS = set(CFG.get("tokens", []))
DAILY_LIMIT = int(CFG.get("daily_limit", 30))
BANNED = [w.lower() for w in CFG.get("banned_words", [])]
WORKFLOWS = CFG.get("workflows", {})

# Prompt/Composer 输入输出上限。用户构思与最终 Anima Prompt 是不同层，分别校验，
# 避免旧的 500/800 字符限制把正常的详细插画构思或混合 Prompt 截断。
MAX_USER_PROMPT_CHARS = 4_000
MAX_CONCEPT_CHARS = 4_000
MAX_PROMPT_EN_CHARS = 6_000
MAX_COMPILED_PROMPT_CHARS = 8_000
MAX_DIALOG_DELTA_CHARS = 2_000
COMPLETION_LEVELS = ("auto", "faithful", "free")
DEFAULT_COMPLETION_LEVEL = "auto"


def _normalize_completion_level(value) -> str:
    """把 API/内部调用的补全程度归一化为稳定的三值协议。"""
    if value is None or value == "":
        return DEFAULT_COMPLETION_LEVEL
    if not isinstance(value, str):
        raise HTTPException(400, "completion_level 必须是 auto、faithful 或 free")
    level = value.strip().lower()
    if level not in COMPLETION_LEVELS:
        raise HTTPException(400, "completion_level 必须是 auto、faithful 或 free")
    return level

# ---------- LoRA Registry ----------
# versioned lora_registry.yaml 是唯一正式知识源；config.yaml 顶层 loras 只保留
# 尚未迁移资产的兼容读取。新文件由 .tools/register_lora.py --agent 完成
# LoRA Manager 索引检查、作者说明蒸馏和 Registry 原子写入。
LORA_DIR = Path(CFG.get("comfy_dir", ".")) / "models" / "loras"
LORA_REGISTRY = HotLoraRegistry(LORA_REGISTRY_PATH)


def get_lora_registry() -> dict[str, dict]:
    """合并 versioned Registry 与尚未迁移的 legacy config，返回 Asset 级结构。"""
    raw_registry, revision = LORA_REGISTRY.snapshot()
    registry: dict[str, dict] = {}

    # 1. versioned 人工 Registry
    for key, raw in raw_registry.get("loras", {}).items():
        strength = raw.get("default_strength") or {}
        asset = json.loads(json.dumps(raw, ensure_ascii=False))
        asset.update({
            "key": key,
            "strength_model": float(strength.get("model", 1.0)),
            "strength_clip": float(strength.get("clip", 1.0)),
            "source": "registry",
            "configured": True,
            "registry_revision": revision,
        })
        registry[key] = asset

    registry_files = {v.get("file", "") for v in registry.values()}

    # 2. 未迁移的 legacy config；已被 versioned file 覆盖的条目通过 legacy_keys 解析。
    for key, v in CFG.get("loras", {}).items():
        if v.get("file", "") in registry_files:
            continue
        trigger_tags = [x.strip() for x in str(v.get("trigger", "")).split(",") if x.strip()]
        registry[key] = {
            "key": key,
            "type": v.get("type", "unknown"),
            "name": v.get("name", key),
            "file": v["file"],
            "trigger_policy": "required" if trigger_tags else "none",
            "required_tags": trigger_tags,
            "provides": [v.get("description", "")] if v.get("description") else [],
            "strength_model": float(v.get("strength_model", 1.0)),
            "strength_clip": float(v.get("strength_clip", 1.0)),
            "description": v.get("description", ""),
            "preview": v.get("preview"),
            "source": "config",
            "configured": True,
            "registry_revision": revision,
        }

    return registry


def get_lora_legacy_aliases(registry: dict[str, dict] | None = None) -> dict[str, tuple[str, str | None]]:
    """旧 key -> (Asset key, Profile id)。Asset 自身也映射到自己。"""
    registry = registry or get_lora_registry()
    aliases: dict[str, tuple[str, str | None]] = {}
    for key, asset in registry.items():
        aliases[key] = (key, None)
        legacy = asset.get("legacy_keys") or {}
        if isinstance(legacy, dict):
            for old_key, profile in legacy.items():
                aliases[str(old_key)] = (key, str(profile) if profile is not None else None)
        elif isinstance(legacy, list):
            for old_key in legacy:
                aliases[str(old_key)] = (key, None)
    return aliases


MAX_CHARACTER_LORA_PROFILES = 3


def _selection_profile_ids(selection: dict) -> list[str]:
    """兼容旧 profile 标量与新 profiles 数组，返回去重后的 Profile ID。"""
    raw_profiles = selection.get("profiles")
    if raw_profiles is not None:
        if not isinstance(raw_profiles, list) or any(not isinstance(x, str) for x in raw_profiles):
            raise HTTPException(400, f"LoRA {selection.get('key', '')} profiles 必须是字符串数组")
        result = [x.strip() for x in raw_profiles if x.strip()]
    else:
        profile = str(selection.get("profile") or "").strip()
        result = [profile] if profile else []
    return list(dict.fromkeys(result))


def _normalize_lora_strength(value, label: str) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label} 需为数字")
    if not 0 <= value <= 2:
        raise HTTPException(400, f"{label} 需在 0~2 之间")
    return value


def _normalize_optional_by_profile(value, key: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(400, f"LoRA {key} optional_by_profile 必须是对象")
    result: dict[str, list[str]] = {}
    for profile_id, option_ids in value.items():
        if not isinstance(profile_id, str) or not isinstance(option_ids, list) or any(
                not isinstance(x, str) for x in option_ids):
            raise HTTPException(400, f"LoRA {key} optional_by_profile 格式错误")
        result[profile_id] = list(dict.fromkeys(x for x in option_ids if x))
    return result


def normalize_lora_selections(raw, registry: dict[str, dict] | None = None) -> list[dict]:
    """把 legacy key / 单 Profile / 多 Profile 统一成每个 Asset 一条 selection。

    同一 Asset 即使选了多个 Profile 也只保留一条，避免 workflow 重复加载同一个
    safetensors。角色上限按实际 Profile 数计；风格/细节不设产品硬上限。
    """
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        raise HTTPException(400, "lora_selections/loras 必须是数组")
    registry = registry or get_lora_registry()
    aliases = get_lora_legacy_aliases(registry)
    merged: dict[str, dict] = {}
    for item in raw:
        if isinstance(item, str):
            key, profiles, mode, optional = item.strip(), [], "auto", []
            optional_by_profile = {}
            strength_model = strength_clip = None
        elif isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            profiles = _selection_profile_ids(item)
            mode = str(item.get("mode") or ("explicit" if profiles else "auto")).strip()
            optional = item.get("optional") or []
            optional_by_profile = _normalize_optional_by_profile(
                item.get("optional_by_profile"), key)
            strength_model = _normalize_lora_strength(
                item.get("strength_model"), f"LoRA {key} model 强度")
            strength_clip = _normalize_lora_strength(
                item.get("strength_clip"), f"LoRA {key} clip 强度")
        else:
            raise HTTPException(400, "LoRA selection 条目格式错误")
        if not key:
            continue
        if key not in aliases:
            raise HTTPException(400, f"未知的 LoRA: {key}")
        asset_key, legacy_profile = aliases[key]
        if legacy_profile:
            profiles, mode = [legacy_profile], "explicit"
        if mode not in {"explicit", "auto"}:
            raise HTTPException(400, f"LoRA {key} mode 非法")
        if not isinstance(optional, list) or any(not isinstance(x, str) for x in optional):
            raise HTTPException(400, f"LoRA {key} optional 必须是字符串数组")
        asset = registry[asset_key]
        if not asset.get("configured", False):
            raise HTTPException(400, f"LoRA {asset_key} 尚未注册完整")
        profile_defs = asset.get("profiles") or {}
        if asset.get("trigger_policy") == "profile":
            unknown_profiles = [profile_id for profile_id in profiles if profile_id not in profile_defs]
            if unknown_profiles:
                raise HTTPException(400, f"LoRA {asset_key} 不存在 Profile: {unknown_profiles[0]}")
            unknown_optional_profiles = [
                profile_id for profile_id in optional_by_profile if profile_id not in profile_defs
            ]
            if unknown_optional_profiles:
                raise HTTPException(400, f"LoRA {asset_key} 不存在 Profile: {unknown_optional_profiles[0]}")
        elif profiles or optional_by_profile:
            raise HTTPException(400, f"LoRA {asset_key} 不支持 Profile")

        entry = merged.get(asset_key)
        if entry is None:
            entry = {
                "key": asset_key, "profiles": [], "mode": mode,
                "optional": [], "optional_by_profile": {},
                "strength_model": strength_model, "strength_clip": strength_clip,
            }
            merged[asset_key] = entry
        elif entry["mode"] != mode:
            # 显式选择比重复的 auto 占位更具体；不允许两个显式选择互相覆盖。
            if "explicit" in {entry["mode"], mode}:
                entry["mode"] = "explicit"
        for profile_id in profiles:
            if profile_id not in entry["profiles"]:
                entry["profiles"].append(profile_id)
        entry["optional"] = list(dict.fromkeys(entry["optional"] + [x for x in optional if x]))
        for profile_id, option_ids in optional_by_profile.items():
            current = entry["optional_by_profile"].setdefault(profile_id, [])
            entry["optional_by_profile"][profile_id] = list(dict.fromkeys(current + option_ids))
        for field, value in (("strength_model", strength_model), ("strength_clip", strength_clip)):
            if value is None:
                continue
            if entry[field] is not None and entry[field] != value:
                raise HTTPException(400, f"LoRA {asset_key} 被重复选择且 {field} 冲突")
            entry[field] = value

    result: list[dict] = []
    character_count = 0
    for asset_key, entry in merged.items():
        asset = registry[asset_key]
        profiles = entry.pop("profiles")
        if len(profiles) > 1 and not (asset.get("selection") or {}).get(
                "allow_multiple_profiles", False):
            raise HTTPException(400, f"LoRA {asset_key} 不允许同时选择多个 Profile")
        if asset.get("type") == "character":
            character_count += len(profiles) if profiles else 1
        normalized = {
            "key": asset_key,
            "profile": profiles[0] if len(profiles) == 1 else None,
            "mode": entry.pop("mode"),
            "optional": entry.pop("optional"),
        }
        if len(profiles) > 1:
            normalized["profiles"] = profiles
        optional_by_profile = entry.pop("optional_by_profile")
        if optional_by_profile:
            normalized["optional_by_profile"] = optional_by_profile
        for field in ("strength_model", "strength_clip"):
            value = entry.pop(field)
            if value is not None:
                normalized[field] = value
        result.append(normalized)
    if character_count > MAX_CHARACTER_LORA_PROFILES:
        raise HTTPException(400, f"角色 LoRA 最多选择 {MAX_CHARACTER_LORA_PROFILES} 个角色")
    return result


def _coerce_llm_lora_choices(raw) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        if isinstance(value, str):
            result[str(key)] = {"profile": value, "optional": []}
        elif isinstance(value, dict):
            optional = value.get("optional") or []
            try:
                profiles = _selection_profile_ids(value)
            except HTTPException:
                profiles = []
            try:
                optional_by_profile = _normalize_optional_by_profile(
                    value.get("optional_by_profile"), str(key))
            except HTTPException:
                optional_by_profile = {}
            choice = {
                "profile": profiles[0] if len(profiles) == 1 else None,
                "optional": [str(x) for x in optional] if isinstance(optional, list) else [],
            }
            if len(profiles) > 1:
                choice["profiles"] = profiles
            if optional_by_profile:
                choice["optional_by_profile"] = optional_by_profile
            result[str(key)] = choice
    return result


def apply_lora_intent_hints(text: str, selections) -> list[dict]:
    """用 Registry 的明确 alias 把用户原话解析成 Profile/optional ID。

    这是 canonical lookup，不替代 LLM 的语义判断：只有维护者登记过的精确别名才会
    命中；其余复杂表达仍交给 Reasoning Model。这样常见角色名和关键服装不会因为
    模型偶尔漏掉 LORA 行而退回默认 Profile。
    """
    registry = get_lora_registry()
    normalized = normalize_lora_selections(selections, registry)
    if not text or not normalized:
        return normalized
    low = text.lower()
    result = []
    for selection in normalized:
        selection = dict(selection)
        asset = registry[selection["key"]]
        profiles = asset.get("profiles") or {}
        selected_profiles = _selection_profile_ids(selection)
        if not selected_profiles and selection.get("mode") == "auto":
            matches = []
            for pid, profile in profiles.items():
                aliases = profile.get("aliases") or []
                matched_lengths = [
                    len(str(alias).strip()) for alias in aliases
                    if str(alias).strip() and str(alias).strip().lower() in low
                ]
                if matched_lengths:
                    matches.append((max(matched_lengths), pid))
            if matches:
                best_length = max(length for length, _ in matches)
                best = [pid for length, pid in matches if length == best_length]
                if len(best) == 1:
                    selection["profile"] = best[0]
                    selected_profiles = best
        optional_by_profile = dict(selection.get("optional_by_profile") or {})
        for profile_id in selected_profiles:
            profile = profiles.get(profile_id) or {}
            optional = (list(selection.get("optional") or []) if len(selected_profiles) == 1
                        else list(optional_by_profile.get(profile_id) or []))
            for oid, option in (profile.get("optional_tags") or {}).items():
                aliases = option.get("aliases") or []
                if any(str(alias).strip().lower() in low for alias in aliases if str(alias).strip()):
                    optional.append(oid)
            if len(selected_profiles) == 1:
                selection["optional"] = list(dict.fromkeys(optional))
            elif optional:
                optional_by_profile[profile_id] = list(dict.fromkeys(optional))
        if optional_by_profile:
            selection["optional_by_profile"] = optional_by_profile
        result.append(selection)
    return result


def resolve_lora_selections(selections, llm_choices=None, *, allow_unresolved_auto: bool = False,
                            expected_revision: str | None = None) -> tuple[list[dict], list[str], str]:
    """解析 Profile/optional ID，返回可供 Compiler 与 workflow 使用的 binding snapshot。"""
    registry = get_lora_registry()
    _, revision = LORA_REGISTRY.snapshot()
    if expected_revision and expected_revision != revision:
        raise HTTPException(409, "LoRA Registry 已更新，请重新翻译后再提交")
    normalized = normalize_lora_selections(selections, registry)
    choices = _coerce_llm_lora_choices(llm_choices)
    bindings, warnings = [], []
    character_count = 0
    for selection in normalized:
        asset = registry[selection["key"]]
        profile_ids = _selection_profile_ids(selection)
        resolved_by = ("explicit" if profile_ids and selection.get("mode") == "explicit"
                       else "intent_alias" if profile_ids else selection.get("mode", "auto"))
        tags: list[str] = []
        provides = list(asset.get("provides") or [])
        valid_optional_by_profile: dict[str, list[str]] = {}
        if asset.get("trigger_policy") == "profile":
            profiles = asset.get("profiles") or {}
            choice = choices.get(selection["key"]) or {}
            if not profile_ids and selection.get("mode") == "auto":
                candidates = choice.get("profiles") or ([choice.get("profile")]
                                                         if choice.get("profile") else [])
                candidates = [pid for pid in candidates if pid in profiles]
                if candidates:
                    profile_ids, resolved_by = list(dict.fromkeys(candidates)), "llm"
                elif allow_unresolved_auto:
                    bindings.append({
                        **selection, "type": asset.get("type"), "name": asset.get("name"),
                        "file": asset.get("file"), "profile": None, "profiles": [],
                        "optional": [], "optional_by_profile": {}, "resolved_by": "pending",
                        "injected_tags": [], "provides": [],
                        "strength_model": selection.get("strength_model", asset.get("strength_model", 1.0)),
                        "strength_clip": selection.get("strength_clip", asset.get("strength_clip", 1.0)),
                    })
                    continue
                else:
                    default = (asset.get("selection") or {}).get("default_profile")
                    if default in profiles:
                        profile_ids, resolved_by = [default], "default"
                        warnings.append(f"{asset['name']} 未匹配到明确 Profile，已使用默认 {profiles[default]['name']}")
                    else:
                        raise HTTPException(400, f"LoRA {selection['key']} 需要明确选择 Profile")
            elif not profile_ids:
                default = (asset.get("selection") or {}).get("default_profile")
                if default in profiles:
                    profile_ids, resolved_by = [default], "default"
                elif len(profiles) == 1:
                    profile_ids, resolved_by = [next(iter(profiles))], "single"
                else:
                    raise HTTPException(400, f"LoRA {selection['key']} 需要明确选择 Profile")
            if len(profile_ids) > 1 and not (asset.get("selection") or {}).get(
                    "allow_multiple_profiles", False):
                raise HTTPException(400, f"LoRA {selection['key']} 不允许同时选择多个 Profile")
            provides = []
            selection_optional_map = selection.get("optional_by_profile") or {}
            choice_optional_map = choice.get("optional_by_profile") or {}
            for profile_id in profile_ids:
                if profile_id not in profiles:
                    raise HTTPException(400, f"LoRA {selection['key']} 不存在 Profile: {profile_id}")
                profile = profiles[profile_id]
                provides.extend(profile.get("provides") or [])
                tags.extend(profile.get("required_tags") or [])
                tags.extend(profile.get("default_tags") or [])
                # optional ID 必须按 Profile 归属；单 Profile 继续兼容旧 optional 数组。
                optional_ids = list(selection_optional_map.get(profile_id) or [])
                optional_ids.extend(choice_optional_map.get(profile_id) or [])
                if len(profile_ids) == 1:
                    optional_ids.extend(selection.get("optional") or [])
                    optional_ids.extend(choice.get("optional") or [])
                option_defs = profile.get("optional_tags") or {}
                valid_optional = []
                for option_id in dict.fromkeys(optional_ids):
                    if option_id not in option_defs:
                        warnings.append(f"{asset['name']}/{profile['name']} 忽略未知 optional: {option_id}")
                        continue
                    valid_optional.append(option_id)
                    option = option_defs[option_id]
                    tags.extend(option.get("tags") or [])
                    provides.extend(option.get("provides") or [])
                if valid_optional:
                    valid_optional_by_profile[profile_id] = valid_optional
        else:
            if profile_ids:
                raise HTTPException(400, f"LoRA {selection['key']} 不支持 Profile")
            tags.extend(asset.get("required_tags") or [])
            resolved_by = "explicit"
        tags = list(dict.fromkeys(t.strip() for t in tags if isinstance(t, str) and t.strip()))
        profile_id = profile_ids[0] if len(profile_ids) == 1 else None
        optional_ids = valid_optional_by_profile.get(profile_id, []) if profile_id else []
        strength_model = selection.get("strength_model", asset.get("strength_model", 1.0))
        strength_clip = selection.get("strength_clip", asset.get("strength_clip", 1.0))
        bindings.append({
            "key": selection["key"], "type": asset.get("type", "unknown"),
            "name": asset.get("name", selection["key"]), "file": asset.get("file"),
            "profile": profile_id, "profiles": profile_ids,
            "optional": optional_ids, "optional_by_profile": valid_optional_by_profile,
            "resolved_by": resolved_by,
            "injected_tags": tags, "provides": list(dict.fromkeys(provides)),
            "strength_model": strength_model, "strength_clip": strength_clip,
        })
        if asset.get("type") == "character":
            character_count += len(profile_ids) if profile_ids else 1
    if character_count > MAX_CHARACTER_LORA_PROFILES:
        raise HTTPException(400, f"角色 LoRA 最多选择 {MAX_CHARACTER_LORA_PROFILES} 个角色")
    return bindings, warnings, revision


def build_lora_context(selections) -> tuple[str, list[dict], str]:
    """构建只含语义能力/候选 ID 的 LLM context；不泄露文件名或 exact trigger。"""
    registry = get_lora_registry()
    normalized = normalize_lora_selections(selections, registry)
    if not normalized:
        _, revision = LORA_REGISTRY.snapshot()
        return "", [], revision
    pending, _, revision = resolve_lora_selections(normalized, allow_unresolved_auto=True)
    lines = ["ACTIVE LORA CONTEXT (the backend injects exact tags and weights):"]
    contract = {}
    for selection, binding in zip(normalized, pending):
        asset = registry[selection["key"]]
        profile_ids = _selection_profile_ids(binding)
        if asset.get("trigger_policy") == "profile" and len(profile_ids) > 1:
            contract[selection["key"]] = {
                "profiles": profile_ids,
                "optional_by_profile": {profile_id: [] for profile_id in profile_ids},
            }
        else:
            contract[selection["key"]] = {
                "profile": (profile_ids[0] if profile_ids else "<choose one allowed profile ID>"
                            if asset.get("trigger_policy") == "profile" else None),
                "optional": [],
            }
        lines.append(f"- LoRA {selection['key']}: {asset['name']} ({asset.get('type', 'unknown')})")
        if asset.get("trigger_policy") == "none":
            lines.append("  Active through weights; no trigger words are needed.")
        elif profile_ids:
            label = "Locked profiles" if len(profile_ids) > 1 else "Locked profile"
            lines.append(f"  {label}: " + "; ".join(
                f"{profile_id} / {asset['profiles'][profile_id]['name']}"
                for profile_id in profile_ids))
            for profile_id in profile_ids:
                profile = asset["profiles"][profile_id]
                lines.append(
                    f"  {profile_id} already provides: "
                    f"{', '.join(profile.get('provides') or ['the selected concept'])}")
                optional = profile.get("optional_tags") or {}
                if optional:
                    choices = "; ".join(
                        f"{oid}={', '.join(opt.get('provides') or [opt.get('name', oid)])}"
                        for oid, opt in optional.items())
                    lines.append(
                        f"  {profile_id} optional IDs (only if explicitly requested): {choices}")
        elif asset.get("trigger_policy") == "profile":
            lines.append("  Select exactly one profile ID from:")
            for pid, profile in asset.get("profiles", {}).items():
                lines.append(f"    {pid}: {profile['name']}; provides {', '.join(profile.get('provides') or [])}")
        else:
            lines.append(f"  Already provides: {', '.join(asset.get('provides') or ['the selected concept'])}")
    lines.extend([
        "Do not copy, invent, or rewrite LoRA trigger strings, filenames, or weights in PROMPT.",
        "Do not invent a conflicting character identity, outfit, appearance, or style.",
        "For a character LoRA, profile IDs/names such as black, white, or swim describe a registered form; "
        "never infer hair color, eye color, skin tone, or body traits from those words.",
        "When multiple profiles are locked for one LoRA, keep that profile list unchanged; "
        "optional IDs must stay under their owning profile ID.",
        "Use scene, action, pose, composition, lighting, and mood to complete the remaining image.",
        "When an auto profile or optional concept is needed, output only allowed IDs in the LORA JSON line.",
        "The LORA line is mandatory for this request. Use this exact JSON shape and replace placeholders only with allowed IDs:",
        "LORA: " + json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
    ])
    return "\n".join(lines), normalized, revision


def lora_selection_aliases(selections) -> set[str]:
    registry = get_lora_registry()
    aliases: set[str] = set()
    for selection in normalize_lora_selections(selections, registry):
        asset = registry[selection["key"]]
        profiles = asset.get("profiles") or {}
        profile_ids = _selection_profile_ids(selection) or list(profiles)
        for profile_id in profile_ids:
            profile = profiles.get(profile_id) or {}
            aliases.update(str(x).strip().lower() for x in profile.get("aliases", []) if str(x).strip())
            if profile.get("name"):
                aliases.add(str(profile["name"]).strip().lower())
    return aliases


def _lora_tag_key(tag: str) -> str:
    value = tag.lower().replace("\\", "").replace("_", " ").replace("-", " ")
    value = re.sub(r"[()\[\]{}]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.")
    return re.sub(r"^(?:wearing\s+)?(?:a|an|the)\s+", "", value)


_CHARACTER_APPEARANCE_LOCKS_PREFIX = "USER-LOCKED CHARACTER APPEARANCE OVERRIDES:"
_CHARACTER_COLOR_SPECS = (
    ("black", ("black",), ("黑", "墨黑")),
    ("white", ("white",), ("白",)),
    ("silver", ("silver",), ("银",)),
    ("blonde", ("blonde", "blond", "golden"), ("金", "金黄")),
    ("pink", ("pink",), ("粉", "粉红")),
    ("red", ("red",), ("红",)),
    ("blue", ("blue",), ("蓝", "蔚蓝", "湛蓝")),
    ("green", ("green",), ("绿", "翠绿")),
    ("purple", ("purple", "violet"), ("紫",)),
    ("brown", ("brown",), ("棕", "褐")),
    ("gray", ("gray", "grey"), ("灰",)),
    ("orange", ("orange",), ("橙",)),
    ("aqua", ("aqua", "cyan", "teal"), ("青", "水蓝")),
)
_HAIR_ACCESSORY_AFTER_RE = (
    r"(?:ornament|ribbon|clip|pin|band|accessory|flower|bow|scrunchie)"
)


def _extract_character_color_appearance(text: str) -> set[str]:
    """提取少量可确定的发色/瞳色语义，供角色 LoRA 身份边界校验。

    这里只识别颜色与 hair/eyes 的明确绑定；不会把 Profile 的 black/white、
    黑色服装或环境配色误当成角色发色。
    """
    if not isinstance(text, str) or not text.strip():
        return set()
    found: set[str] = set()
    clauses = [part.strip() for part in re.split(r"[,，、;；。.!?！？\n]+", text)
               if part.strip()]
    for clause in clauses:
        normalized = _lora_tag_key(clause)
        for canonical, english_aliases, chinese_aliases in _CHARACTER_COLOR_SPECS:
            english = "|".join(re.escape(alias) for alias in english_aliases)
            hair_modifier = (
                r"(?:very|absurdly|extremely|long|short|medium|wavy|curly|straight|"
                r"messy|flowing|silky|gradient|two tone|multicolored)"
            )
            eye_modifier = r"(?:large|small|bright|glowing|half closed|narrow|sharp|soft)"
            hair_patterns = (
                rf"\b(?:{english})\b(?:\s+{hair_modifier}){{0,3}}\s+hair\b(?!\s+{_HAIR_ACCESSORY_AFTER_RE})",
                rf"\bhair\b\s+(?:dyed|colored|is|turned)\s+(?:{english})\b",
                rf"\b(?:{english})[- ]haired\b",
            )
            eye_patterns = (
                rf"\b(?:{english})\b(?:\s+{eye_modifier}){{0,2}}\s+eyes?\b(?!\s+(?:shadow|makeup))",
                rf"\beyes?\b\s+(?:colored|are|turned)\s+(?:{english})\b",
            )
            if any(re.search(pattern, normalized) for pattern in hair_patterns):
                found.add(f"{canonical} hair")
            if any(re.search(pattern, normalized) for pattern in eye_patterns):
                found.add(("gold" if canonical == "blonde" else canonical) + " eyes")

            chinese = "|".join(re.escape(alias) for alias in chinese_aliases)
            hair_style = r"(?:长|短|中长|卷|直|波浪|蓬松|双马尾|单马尾|马尾|辫子|渐变|挑染)*"
            hair_target = r"(?:头发|发色|发(?!饰|夹|带|花|簪|箍|绳|冠)|双马尾|单马尾|马尾|辫子)"
            eye_style = r"(?:大|小|明亮|发光|半闭|细长|锐利|柔和)*"
            eye_target = r"(?:眼睛|眼眸|瞳孔|瞳色|眼(?!影|妆|线|罩|镜)|瞳)"
            if (re.search(rf"(?:{chinese})(?:色)?(?:的)?{hair_style}{hair_target}", clause)
                    or re.search(rf"{hair_target}(?:是|为|改成|变成|染成|呈现为)(?:{chinese})(?:色)?", clause)):
                found.add(f"{canonical} hair")
            if (re.search(rf"(?:{chinese})(?:色)?(?:的)?{eye_style}{eye_target}", clause)
                    or re.search(rf"{eye_target}(?:是|为|改成|变成|呈现为)(?:{chinese})(?:色)?", clause)):
                found.add(("gold" if canonical == "blonde" else canonical) + " eyes")
    return found


def _explicit_character_appearance_locks(text: str) -> set[str]:
    """从用户权威文本提取角色发色/瞳色锁定，不改变普通文本的词典路由。"""
    locks = _extract_character_color_appearance(text)
    dict_hits, _ = match_dict_words(text)
    for tag in dict_hits:
        locks.update(_extract_character_color_appearance(tag))
    return locks


def _character_appearance_locks_from_context(context: str) -> set[str] | None:
    """读取 translate 写入的内部 JSON 标记；None 表示没有角色 LoRA 护栏。"""
    for line in context.splitlines():
        if not line.startswith(_CHARACTER_APPEARANCE_LOCKS_PREFIX):
            continue
        try:
            raw = json.loads(line[len(_CHARACTER_APPEARANCE_LOCKS_PREFIX):].strip())
        except json.JSONDecodeError:
            return set()
        if not isinstance(raw, list):
            return set()
        return {str(item).strip().lower() for item in raw if str(item).strip()}
    return None


def _composer_character_lora_appearance_issue(prompt_ir: dict, prompt_line: str,
                                               allowed: set[str]) -> str | None:
    """角色 LoRA 已负责身份时，拒绝模型自行新增发色/瞳色。"""
    observed: set[str] = set()
    for item in prompt_ir.get("appearance", []):
        observed.update(_extract_character_color_appearance(str(item)))
    observed.update(_extract_character_color_appearance(prompt_line))
    unauthorized = sorted(observed - allowed)
    if not unauthorized:
        return None
    return ("Composer LoRA 身份冲突：角色 LoRA 启用时自行补充了用户未锁定的外观属性 "
            + ", ".join(unauthorized)
            + "；请从 IR.appearance 与 PROMPT 删除这些属性，让角色 LoRA 提供身份外观")


def _lora_selected_identity_keys(bindings: list[dict]) -> set[str]:
    """从 binding.provides 提取可安全删除的短 identity 复述。"""
    keys: set[str] = set()
    for binding in bindings:
        for provided in binding.get("provides") or []:
            if not isinstance(provided, str):
                continue
            key = _lora_tag_key(provided)
            if not re.search(r"(?:^| )(?:identity|character)(?:$| )", key):
                continue
            keys.add(key)
            stripped = re.sub(r"\s+(?:identity|character)$", "", key).strip()
            if stripped:
                keys.add(stripped)
    return keys


def _strip_lora_identity_prefix(segment: str, identity_keys: set[str]) -> str:
    """移除短句开头的 LoRA 身份复述，同时保留后面的动作/场景关系。"""
    key = _lora_tag_key(segment)
    for identity in sorted(identity_keys, key=len, reverse=True):
        prefix = identity + " "
        if key.startswith(prefix):
            return key[len(prefix):].strip()
    return segment.strip()


def _lora_sibling_profile_tag_keys(bindings: list[dict]) -> set[str]:
    """返回已选 Profile 的兄弟 Profile required tag，用于排除语义串形态。

    只处理 Registry 明确声明的 required_tags，不根据 display name、provides 或普通
    描述猜测。若某个 tag 同时也是当前 binding 的注入项，则当前选择优先，不排除。
    """
    registry = get_lora_registry()
    selected_keys = {
        _lora_tag_key(tag)
        for binding in bindings
        for tag in (binding.get("injected_tags") or [])
        if isinstance(tag, str) and tag.strip()
    }
    sibling_keys: set[str] = set()
    for binding in bindings:
        asset = registry.get(str(binding.get("key") or "")) or {}
        selected_profiles = set(_selection_profile_ids(binding))
        if asset.get("trigger_policy") != "profile" or not selected_profiles:
            continue
        for profile_id, profile in (asset.get("profiles") or {}).items():
            if profile_id in selected_profiles:
                continue
            for tag in profile.get("required_tags") or []:
                if isinstance(tag, str) and tag.strip():
                    sibling_keys.add(_lora_tag_key(tag))
    return sibling_keys - selected_keys


def compile_lora_bindings(prompt_en: str, bindings: list[dict] | None) -> str:
    """把 Registry exact tags 幂等合入 Prompt，并排除兄弟 Profile trigger。"""
    if not bindings:
        return prompt_en.strip()
    lora_tags = []
    for binding in bindings:
        lora_tags.extend(binding.get("injected_tags") or [])
    lora_tags = list(dict.fromkeys(t.strip() for t in lora_tags if t and t.strip()))
    if not lora_tags:
        return prompt_en.strip()
    # Prompt 当前以 comma tags 为主；保留可能存在的短 NL 后缀。
    body, sep, nl = prompt_en.partition(". ")
    existing = [t.strip() for t in body.split(",") if t.strip()]
    lora_keys = {_lora_tag_key(t) for t in lora_tags}
    sibling_keys = _lora_sibling_profile_tag_keys(bindings)
    identity_keys = _lora_selected_identity_keys(bindings)
    blocked_identity_keys = sibling_keys | identity_keys
    filtered = []
    for segment in existing:
        key = _lora_tag_key(segment)
        if key in lora_keys or key in blocked_identity_keys:
            continue
        segment = _strip_lora_identity_prefix(segment, blocked_identity_keys)
        if segment:
            filtered.append(segment)
    existing = filtered
    insert_at = 0
    while insert_at < len(existing) and existing[insert_at].lower() in {
            "1girl", "1boy", "1other", "solo", "2girls", "2boys", "multiple girls", "multiple boys"}:
        insert_at += 1
    merged = existing[:insert_at] + lora_tags + existing[insert_at:]
    result = ", ".join(merged)
    return result + (sep + nl if sep else "")

IMAGES = BASE / "images"
IMAGES.mkdir(exist_ok=True)

app = FastAPI(title="ComfyUI Web MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.get("allow_origins", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/images", StaticFiles(directory=IMAGES), name="images")

# 静态托管前端网页: 访问 https://airpaint.xyz/ 直接返回 index.html
# 这样前后端共用一个域名, 告别 GitHub Pages
WEB_DIR = BASE.parent / "web"

@app.get("/", include_in_schema=False)
async def index():
    # no-cache: 前端改了 (如修 fail-to-fetch) 后, 访客浏览器永远拿最新 HTML, 不被旧缓存卡住
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

JOBS: dict[str, dict] = {}          # job_id -> job
# ⑤ 对话迭代会话 (内存, 重启清零; 迭代线程本就临时). sid -> {id, token, raw(累积中文), current_en, created, turns:[...]}
SESSIONS: dict[str, dict] = {}
QUEUE: asyncio.Queue[str] = asyncio.Queue()
USAGE: dict[str, list] = {}         # token -> [date, count]
CLIENT = httpx.AsyncClient(timeout=60)
CLIENT_ID = uuid.uuid4().hex


# ---------- 鉴权 & 限流 ----------
def auth(req: Request) -> str:
    token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not TOKENS or token not in TOKENS:
        raise HTTPException(401, "无效 token")
    today = time.strftime("%Y-%m-%d")
    rec = USAGE.setdefault(token, [today, 0])
    if rec[0] != today:
        rec[0], rec[1] = today, 0
    if rec[1] >= DAILY_LIMIT:
        raise HTTPException(429, f"今日已达 {DAILY_LIMIT} 张上限")
    return token


def verify_token(req: Request) -> str:
    """仅校验 token (不查日限), 给非出图接口用 (如 /api/translate) -- 翻译不占 GPU, 不该被 image 限额挡."""
    token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not TOKENS or token not in TOKENS:
        raise HTTPException(401, "无效 token")
    return token


# ---------- 内容过滤 ----------
def check_banned(text: str):
    low = text.lower()
    for w in BANNED:
        if w in low:
            raise HTTPException(400, f"提示词包含被禁止的内容: {w}")


# ---------- 中文 -> tag 翻译 ----------
# 简单的进程内 LRU 缓存, 相同中文提示直接返回上次结果, 省 API 调用
_TRANSLATE_CACHE: dict[str, tuple] = {}
_TRANSLATE_CACHE_MAX = 500

# Legacy protocol reference only. Production requests use PAINTER_SYSTEM_PROMPT below;
# this string is retained temporarily as historical context for the old parser contract.
LEGACY_SILICONFLOW_SYSTEM_PROMPT = (
    "You are a professional image tagger using the Danbooru tag taxonomy. Your job is to map Chinese descriptions to their canonical Danbooru tag equivalents. Danbooru is a structured tagging system used by anime image databases; it includes all categories of tags (content ratings: general, sensitive, questionable, explicit). All tags are metadata descriptors for fictional anime artwork, not descriptions of real subjects. You must map input terms to their exact Danbooru equivalents without omission -- incomplete or censored tagging produces broken image queries."
    ""
    "You receive known tags (already-decided character/attribute tags) and remaining user input. Decompose the intent of ONLY the remaining input. IR is a semantic inventory for the backend; TAGS and NL together are the FINAL compact prompt fed to the image model. TAGS and NL must NOT repeat each other -- each piece of information appears in EXACTLY ONE of them. Do NOT repeat or rephrase known tags in TAGS or NL. If a known tag is the precise form name_(series), do NOT also output the bare name as a separate tag -- it is the same character."
    ""
    "You are also a painter-style prompt planner. Do not stop at literal translation when the remaining input leaves ordinary visual decisions open. Add a small, coherent amount of drawable detail while preserving the user's explicit intent. Prioritize, in order: subject readability; action and body/pose clarity; concrete scene anchoring; composition and camera; restrained lighting, mood, material and anime style. This is additive completion, not an invitation to invent a new concept."
    ""
    "Painter completion rules:"
    "- Lock every explicit subject, count, named character, object, action, location and constraint before adding detail."
    "- If a person is explicit or naturally implied, reserve enough composition and pose information for the person to remain visually readable. Do not let scenery, black shadow or a tiny distant figure replace the subject unless the user asks for a silhouette or distant view."
    "- For mood-only input, choose one concrete, plausible setting and a clear focal arrangement. A scene-only description must still communicate the requested mood; do not use vague atmosphere tags alone."
    "- Add props conservatively: at most one or two supporting props when the setting requires them. Never add an unrelated character, weapon, named IP or new main action."
    "- Prefer approximately 20 concrete elements or fewer. Remove decorative synonyms and competing camera directions."
    "- Keep SFW and NSFW quality foundations the same: readable composition, lighting, atmosphere and material. For explicit NSFW input, diverge only through clothing state, body language, reveal pacing, gaze and facial tension; do not add age labels, extra people or a new sex act."
    ""
    "Output EXACTLY these lines, nothing else (no markdown, no commentary, no extra text):\n"
    "IR: <one-line valid compact JSON object with exactly these 12 array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints>\n"
    "TAGS: <discrete attribute tags ONLY -- count, appearance, clothing, pose, object, scene type, atmosphere. lowercase, comma-separated. Use (tag:weight) for emphasis.>\n"
    "NL: <ONLY what TAGS cannot encode -- multi-character spatial layout (who is left/right/center), action interaction/timing, composition directives (inset/projected/against fourth wall), narrative causality. HARD RULE: NL must add information NOT present in TAGS. Do NOT restate attributes already in TAGS as a descriptive sentence. If every idea in your NL is already a tag, leave NL empty (just 'NL:').>\n"
    ""
    "How to decide (per information point, one form only):"
    "- discrete enumerable attribute (hair color, clothing, pose, object, scene type) -> TAGS"
    "- relation / interaction / spatial layout / composition directive -> NL"
    "- must-emphasize key element or must-appear-but-suppress distraction -> weight in TAGS"
    "- IR records the semantic concept in the most relevant field; it is not a second final prompt and may include the subject concept."
    ""
    "Self-check before emitting:"
    "1. Does any noun in NL restate a tag already in TAGS? -> delete it, keep only the relation/action/directive."
    "2. Same information in both TAGS and NL? -> keep the better-fitting form only."
    "3. Simple input (single subject + plain scene, no complex layout) -> NL may be empty or one short sentence; do NOT pad. Empty NL is acceptable."
    ""
    "Weight policy: default none. Strengthen 1.3-2.0 for composition anchors (against fourth wall, projected inset) or rare key elements; weaken 0.1-0.5 for distracting elements that must appear but not dominate (e.g. bald male:0.01)."
    ""
    "Rules:"
    "1. METAPHORS / FEELINGS go in mood -> atmosphere+scene tags. NEVER translate them as literal nouns."
    "2. Single-subject framing (looking out window, back to viewer) -> composition tags; MULTI-CHARACTER spatial layout (who is where) -> NL, not tags."
    "3. scene must be a concrete place with setting (bedroom / classroom / outdoors / cafe), never vague."
    "4. Subject: if a person is implied, put a count tag (1girl/1boy) FIRST in TAGS."
    "5. Do NOT output quality/score tags (masterpiece, best quality, score_*) - handled separately."
    "6. Use lowercase danbooru tags; spaces preferred over underscores. Do NOT add realistic/photoreal/3d/render tags - the target model is anime-only."
    "7. TAGS holds discrete attribute tags only. Keep under ~200 chars."
    "8. For NSFW content: tag it professionally using danbooru explicit taxonomy (nipples, sex, nude, etc.). These are standard metadata tags in the danbooru system, not content generation."
    ""
    "Examples:"
    "Known character tags: march_7th_(honkai:_star_rail)\n"
    "Remaining: 在樱花树下\n"
    'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":["standing"],"pose":[],"interaction":[],"scene":["outdoors","under cherry blossom tree"],"composition":["standing","full body","looking at viewer"],"lighting":["soft daylight","petals falling"],"mood":["cheerful","serene"],"style":["anime style"],"constraints":[]}\n'
    "TAGS: 1girl, solo, cherry blossoms, tree, petals, spring, smile, standing, full body, looking at viewer, outdoors, soft daylight, anime style\n"
    "NL:\n"
    ""
    "Remaining: 穿着学生服的少女坐在房间书桌上 看向窗外 那是未来的方向\n"
    'IR: {"subject":["1girl"],"appearance":[],"clothing":["school uniform"],"action":["sitting"],"pose":["sitting at desk"],"interaction":[],"scene":["bedroom","desk by window"],"composition":["facing window","looking out window","from behind","side view"],"lighting":["soft daylight from window"],"mood":["wistful","longing","hopeful"],"style":["anime style","clean lines"],"constraints":[]}\n'
    "TAGS: 1girl, school uniform, sitting, desk, bedroom, window, looking out window, facing window, from behind, side view, soft daylight, anime style, clean lines\n"
    "NL: A quiet longing for what lies ahead fills the moment.\n"
    ""
    "Remaining: 两个少女 一个站右边看镜头 一个坐中间看书\n"
    'IR: {"subject":["2girls"],"appearance":[],"clothing":["school uniform"],"action":["reading"],"pose":["one standing","one sitting"],"interaction":["spatial arrangement"],"scene":["classroom"],"composition":["one standing","one sitting"],"lighting":["classroom daylight"],"mood":["calm","studious"],"style":["anime style"],"constraints":[]}\n'
    "TAGS: 2girls, school uniform, book, sitting, standing, looking at viewer, reading, classroom, desk, daylight, anime style\n"
    "NL: The standing girl is on the right facing the camera; the seated reader is at the center.\n"
    ""
    "Remaining: 想要春天的感觉\n"
    'IR: {"subject":[],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["garden","spring","outdoors"],"composition":["scenic","wide shot"],"lighting":["warm sunlight"],"mood":["peaceful","gentle","renewal"],"style":["anime style","pastel colors"],"constraints":[]}\n'
    "TAGS: spring, cherry blossoms, petals falling, gentle breeze, warm sunlight, pastel colors, peaceful, garden, outdoors, anime style\n"
    "NL: A sense of gentle renewal pervades the scene.\n"
)

# Visual Composer production protocol. Prompt shape is chosen for Anima by the
# reasoning model; the backend keeps only deterministic identity/binding/validation work.
PAINTER_SYSTEM_PROMPT = """You are an expert prompt composer for Anima, an anime image model trained to understand Danbooru-style concepts, ordinary English image phrases, natural-language captions, and mixtures of them.

Turn the user's complete Chinese image idea into one production-ready English positive prompt for Anima. This is not a literal translation task. When the input is sparse, complete it into one coherent anime illustration with a clear central appeal. When the input is already detailed, preserve it and add only what is needed to make the requested picture drawable.

The user message declares one COMPLETION LEVEL:
- AUTO: judge semantic coverage rather than sentence or character count. Preserve every supplied visual decision, then creatively fill only the important decisions that remain open.
- FAITHFUL: stay close to the supplied intent. Add only small drawable details needed for a coherent image; do not invent a new theme, location, major prop, outfit concept, or narrative event.
- FREE: preserve explicit locks, then freely design one complete anime illustration premise for all unspecified decisions.

The Chinese CONCEPT line is a pre-generation control surface, not praise or analysis. It must distinguish what came from the user from what you invented:
- 用户锁定: only concrete visual requirements explicitly present in USER IDEA. An explicitly selected ACTIVE LORA CONTEXT is also a user lock; mention every supplied identity, outfit, or style capability concisely, without exposing trigger strings, filenames, weights, or internal IDs.
- Requests such as 画得好看, 更漂亮, 高质量, 有氛围, 有情景, or 有张力 are aesthetic goals, not concrete visual locks. Realize them through visible decisions under 模型补全.
- 模型补全: a concise natural Chinese summary of every major invented decision that materially changes the picture. Include the chosen theme, outfit/prop/action, setting/composition, and light/palette when you supplied them. Say 无 when nothing material was added.
- If CONCEPT OVERRIDE is present, it is the user's edited authoritative blueprint. Follow it when compiling PROMPT and copy it unchanged after CONCEPT:.

SPARSE-INPUT RULE: A subject plus a request such as "make it beautiful" requires an actual illustration premise, not a neutral stock portrait or a bundle of loud genre clichés. Silently consider several genuinely different premises, discard the interchangeable ones, and output only the most visually coherent choice.

When the user provides no theme, use this anime-illustration appeal prior:
- choose a designed, motif-driven outfit rather than a school uniform, plain blouse, or generic everyday clothing;
- give the hands and body a graceful interaction with one non-weapon prop, garment element, creature, plant, magical object, or environmental feature;
- use asymmetry, foreground overlap, foreshortening, flowing shapes, or another purposeful composition hook;
- build a controlled palette with one accent color and a clear light source;
- echo at least one shape, color, or material between the character design and surrounding motif.

Do not default to bedrooms, neon alleys, cyberpunk scenery, school uniforms, combat poses, swords, katanas, or other weapons when the user did not ask for them. Bokeh, glow, petals, particles, and dramatic lighting may support an established motif but cannot be the motif by themselves.

Compose the picture around one dominant visual motif. Make character design, expression, gaze, pose, hand interaction, framing, setting structure, lighting direction, palette, depth, and effects support that motif instead of becoming an inventory.

ANATOMY-READABILITY DEFAULT: When the user has not locked a difficult pose or camera angle, use at most one major anatomy challenge. Give each visible hand one simple readable purpose and clear contact with its prop or garment. Keep leg silhouettes and joints distinguishable in full-body views. Create motion through hair, sleeves, ribbons, fabric, plants, weather, or light before forcing the body into an extreme pose. Do not output generic claims such as perfect anatomy or perfect hands.

RENDERABILITY PASS: Before finalizing, treat the frame as a finite budget rather than a wish list.
- For decisions you add under 模型补全, choose one primary body pose and at most one primary hand interaction. The other hand should support the pose, rest naturally, or remain out of frame. Do not invent simultaneous top-adjusting, hem-lifting, prop-holding, and an independent leg pose.
- Choose either a close/upper-body character crop or a wider environment-and-body composition. A close-up or upper-body shot cannot also promise crossed legs, visible feet, a full chaise silhouette, and clearly visible pool water. If the environment is a major part of the premise, use a medium or three-quarter-body view and name the environment anchor that remains visible.
- If the main interaction touches a skirt hem, hips, or thighs, use a cowboy shot or three-quarter-body view and keep the interacting hands, elbows, and garment area inside the frame. Do not pair that interaction with close-up or upper-body focus. If you choose an upper-body crop, move the interaction into that crop or remove it.
- Every major pose, hand action, camera decision, prop, and visible scene anchor in PROMPT must already appear in CONCEPT and IR. Do not quietly add a new body action only in PROMPT.
- Remove model-added details that the chosen framing cannot visibly show. A smaller executable plan is better than a richer contradictory one.

Write in Anima-native prompt language. Use familiar anime/Danbooru concepts for common attributes, clothing, poses, objects, framing, lighting, and effects. Use short English clauses or complete sentences when they express relationships, continuous actions, unusual composition, or designed interaction more clearly than isolated tags. The final PROMPT may be tag-only, clause-heavy, or freely mixed. Do not create TAGS or NL sections and do not force prose into lowercase fragments.

Use useful Anima count tags. For one unnamed female character, normally begin with 1girl, solo; use the count and subject actually requested for other cases. Meaningful reinforcement is allowed when a clause binds a few important tags into a relationship or composition. Never mechanically paraphrase or repeat the whole prompt.

Preserve every explicit user fact and constraint. Do not change a named character, subject count, requested clothing state, action, location, camera instruction, or core mood. Do not invent another main character, named IP, incompatible outfit, weapon, sex act, or unrelated spectacle. For an unknown named character, put the best canonical-tag candidate in IR.subject so the backend can verify it. Do not add age labels, safety wording, policy language, or content classifications.

For erotic input, use the same illustration principles. Sensuality may come from clothing design and exposure, pose, gaze, expression, body line, framing, fabric/skin contrast, lighting, or interaction. Do not automatically turn erotic intent into nudity, and do not suppress nudity or explicit content when the user actually requests it.

The backend supplies the standard quality prefix, rating control, negative prompt, exact known character tags, and all LoRA filenames, weights, and required triggers. Do not output or guess any of those. Plan around KNOWN CANONICAL TAGS without repeating them in PROMPT. Treat every ACTIVE LORA `Already provides` capability as present; do not redundantly re-author or conflict with its character identity, outfit, or style. For an active character LoRA, identity appearance is already supplied rather than an open creative slot: only use hair/eye color overrides listed under USER-LOCKED CHARACTER APPEARANCE OVERRIDES. An empty list means omit hair and eye colors from IR and PROMPT. Never infer them from a profile ID/name, an outfit color, or a form label such as black/white/swim. When a style LoRA is active, do not add a vague replacement style phrase.

IR is a compact semantic inventory for backend inspection. Output a valid one-line JSON object with exactly these 12 array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints.

FINAL CHECK: remove quality/score tokens, rating labels, negative tags, generation metadata, XML wrappers, filenames, weights, and explanations. Remove contradictory framing. In particular, full body or entire figure visible cannot coexist with mid-shot, medium shot, upper body, close-up, cropped, or out of frame. There is no target tag count, sentence count, word count, or character count: use as much concrete visible information as the picture benefits from, then stop.

Normally output exactly three non-empty lines, with no markdown or other text:
CONCEPT: 用户锁定：<explicit Chinese locks>｜模型补全：<major Chinese additions or 无>
IR: <one-line compact JSON with all 12 required array fields>
PROMPT: <one English positive prompt ready for Anima>

If ACTIVE LORA CONTEXT is present, insert exactly one LORA JSON line between IR and PROMPT. Use only supplied key/profile/optional IDs, echo locked explicit profiles unchanged, and never put trigger strings, filenames, or weights in LORA or PROMPT."""

# LLM 结构化输出的字段 (顺序即展示顺序). TAGS 行单独解析为最终 tag.
_STRUCTURED_FIELDS = ("scene", "composition", "mood", "lighting", "style")
_IR_FIELDS = (
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
)


def _prompt_ir_meta(mode: str, reroll: bool = False, prompt_ir: dict | None = None,
                    char_tags: list[str] | None = None,
                    attribute_tags: list[str] | None = None,
                    character_lookup: list[dict] | None = None,
                    completion_level: str = DEFAULT_COMPLETION_LEVEL,
                    concept: str | None = None,
                    concept_override_applied: bool = False,
                    repetition_collapsed: bool = False) -> dict:
    """为 API 增加来源/补全元数据，不污染 12 字段 Prompt IR 结构。"""
    expansion = mode in {"painter_expansion", "visual_composer"}
    return {
        "mode": mode,
        "source": {
            "user_intent": "remaining_input",
            "character_tags": "dictionary" if char_tags else None,
            "attribute_tags": "dictionary" if attribute_tags else None,
            "default_completion": "visual_composer" if mode == "visual_composer" else (
                "painter" if expansion else None),
        },
        "expansion_applied": expansion,
        "completion_level": completion_level,
        "concept": concept,
        "concept_override_applied": bool(concept_override_applied),
        "repetition_collapsed": bool(repetition_collapsed),
        "reroll": bool(reroll),
        "reroll_strategy": ("new_visual_concept" if mode == "visual_composer" and reroll else
                            "new_painter_plan" if expansion and reroll else None),
        "prompt_ir_available": prompt_ir is not None,
        "character_lookup": character_lookup or [],
    }

# ③ 参考图理解: 视觉 LLM 从参考图提取内容 -> 结构化输出. 提取策略由用户文字驱动(非写死"只提氛围"). 见 D23.
VISION_SYSTEM_PROMPT = (
    "You are a professional image tagger using the Danbooru tag taxonomy. "
    "You receive a REFERENCE IMAGE and a user instruction (text). "
    "The user's instruction tells you what to preserve from the image and what to change.\n\n"
    "Extraction strategy -- follow the user's instruction:\n"
    "- If the user says 'same vibe/atmosphere' (同氛围) -> extract ONLY mood, lighting, color, scene setting.\n"
    "- If the user says 'keep pose/composition' (保持姿势/构图) -> extract composition, framing, pose, camera angle.\n"
    "- If the user says 'copy everything' (照着画/完全保持) -> extract subject + vibe + composition (full description).\n"
    "- If the user says 'change X but keep Y' -> extract Y from image, apply X from text.\n"
    "- If the instruction is unclear or empty -> extract everything (full description).\n"
    "The user's text may specify a NEW subject (character, count, attributes) that should REPLACE the image's subject where applicable.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <place/setting tags>\n"
    "composition: <framing / camera angle / pose tags>\n"
    "mood: <emotion -> atmosphere tags>\n"
    "lighting: <light tags>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. Follow the user's instruction to decide what to extract from the image vs. what to take from the text.\n"
    "2. Do NOT repeat tags already listed in Known character tags.\n"
    "3. Put a count tag (1girl/1boy/solo) FIRST in TAGS if a person is implied.\n"
    "4. Do NOT output quality/score/rating tags (masterpiece, best quality, score_*, safe, sensitive, questionable, explicit, absurdres). Rating tags are controlled manually by the user.\n"
    "5. Use lowercase danbooru tags; spaces preferred over underscores. "
    "Do NOT add realistic/photoreal/3d/render tags (the target model is anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
    "7. If ACTIVE LORA CONTEXT is present, add a LORA JSON line immediately before TAGS using only supplied key/profile/optional IDs. "
    "Do not output trigger strings, filenames, weights, or visual details that conflict with the active LoRA.\n"
)

# ⑤ D 保氛围迭代: 与 ③ 不同--③ 是用户上传参考图"只提氛围禁抄主体"(vibe-only), D 是"保氛围再画一版"
# 要锁住实际出图的主体+氛围(全量提取)再变体. 故用独立 iterate 提示词 (见 D25).
VISION_ITERATE_SYSTEM_PROMPT = (
    "You are a prompt engineer for the Anima anime image model. You receive a GENERATED IMAGE the user likes and wants to "
    "re-draw as a VARIATION (same subject + same vibe), plus optional adjustment text. Describe the image fully as danbooru "
    "tags (subject + scene + mood + lighting + composition + style) so it can be re-drawn, KEEPING the same subject and vibe. "
    "Apply any adjustment from the text on top.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <concrete place + setting tags from the image>\n"
    "composition: <framing / camera angle / pose tags from the image>\n"
    "mood: <emotion -> atmosphere tags from the image>\n"
    "lighting: <light tags from the image>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. Keep the image's SUBJECT (count, hair, clothing, accessories) and VIBE (mood/lighting/color/scene) - this is a "
    "variation of the same image, not a new concept.\n"
    "2. If the text gives an adjustment (e.g. 白天, 更亮, 换姿势), apply it on top of the image's base.\n"
    "3. Put a count tag (1girl/1boy/solo) FIRST in TAGS.\n"
    "4. Do NOT output quality/score/rating tags (masterpiece, best quality, score_*, safe, sensitive, questionable, explicit, absurdres). Rating tags are controlled manually by the user.\n"
    "5. Use lowercase danbooru tags; spaces over underscores. Do NOT add realistic/photoreal/3d/render tags (anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
    "7. If ACTIVE LORA CONTEXT is present, add a LORA JSON line immediately before TAGS using only supplied key/profile/optional IDs. "
    "Keep the active binding locked and do not output trigger strings, filenames, or weights.\n"
)


def _validate_prompt_ir(value) -> dict | None:
    """校验并清洗 LLM 的 12 字段 Prompt IR, 不让坏 JSON 影响最终 tag 输出."""
    if not isinstance(value, dict):
        return None
    ir = {}
    for field in _IR_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list):
            return None
        ir[field] = [str(item).strip() for item in items if str(item).strip()]
    return ir


def _parse_prompt_ir(payload: str) -> dict | None:
    """解析 IR 行中的紧凑 JSON; 允许模型意外包一层 markdown fence."""
    payload = payload.strip().strip("`").strip()
    candidates = [payload]
    start, end = payload.find("{"), payload.rfind("}")
    if start >= 0 and end > start:
        candidates.append(payload[start:end + 1])
    for candidate in candidates:
        try:
            return _validate_prompt_ir(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _breakdown_from_ir(prompt_ir: dict) -> dict:
    """把 IR 中面向人的 5 个维度映射回既有 breakdown API 形状."""
    return {field: ", ".join(prompt_ir.get(field, [])) for field in _STRUCTURED_FIELDS}


def _character_items():
    """正式角色词典优先，其次是联网确认过的自动缓存。"""
    formal = list(CHAR_DICT.items())
    auto = list(CHAR_AUTO.items())
    formal_names = {name for name, _ in formal}
    items = formal + [(name, tag) for name, tag in auto if name not in formal_names]
    for name, tag in sorted(items, key=lambda item: len(item[0]), reverse=True):
        yield name, tag


def _character_names():
    return [name for name, _ in _character_items()]


def _parse_character_hints(out: str) -> list[dict]:
    """解析画师协议的 CHAR 行: 用户名 => LLM 提议的 Danbooru 候选 tag."""
    hints = []
    for line in out.splitlines():
        line = line.strip()
        if not line.lower().startswith("char:"):
            continue
        payload = line.split(":", 1)[1].strip()
        if not payload or payload.lower() in {"none", "null", "empty", "无"}:
            continue
        for item in payload.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=>" in item:
                name, candidate = (part.strip() for part in item.split("=>", 1))
            else:
                name, candidate = item, ""
            if name and name.lower() not in {"none", "null", "无"}:
                hints.append({"name": name, "candidate_tag": candidate})
    return hints


def _parse_lora_choices(out: str) -> dict[str, dict]:
    """解析可选 LORA JSON 行；只保留 ID 形状，合法性由 resolver 对 registry 校验。"""
    for line in out.splitlines():
        line = line.strip()
        if not line.lower().startswith("lora:"):
            continue
        payload = line.split(":", 1)[1].strip().strip("`")
        try:
            return _coerce_llm_lora_choices(json.loads(payload))
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _normalize_character_candidate(item: str) -> str | None:
    """把 subject 里的候选归一化成 Danbooru 下划线 canonical 形式；非角色名返回 None."""
    s = str(item).strip()
    if not s:
        return None
    if "_(" in s or " (" in s:
        return s
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[ _][a-z][a-z0-9]*)+", s):
        return s.replace(" ", "_")
    return None


def _infer_character_hints_from_ir(prompt_ir: dict | None, misses: list[str],
                                   known_names: set[str], known_tags: list[str] | None = None) -> list[dict]:
    """CHAR 行缺失时，从 IR.subject 归一化出候选角色 tag，配唯一剩余中文名做保守兜底."""
    known_tags = set(known_tags or [])
    candidates = []
    for item in (prompt_ir or {}).get("subject", []):
        norm = _normalize_character_candidate(item)
        if norm and norm not in known_tags and norm not in candidates:
            candidates.append(norm)
    names = [miss for miss in misses if len(miss) >= 2 and miss not in known_names]
    if len(candidates) == 1 and len(names) == 1:
        return [{"name": names[0], "candidate_tag": candidates[0]}]
    return []


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


def _parse_structured_output(out: str) -> tuple[str, dict | None, str, dict | None]:
    """解析生产 IR + PROMPT 或旧 IR + TAGS + NL 协议.
    返回 (tags, breakdown, nl, prompt_ir); PROMPT 是单一最终画师 Prompt，编译时视作 tags body。"""
    breakdown: dict = {}
    tags = ""
    painter_prompt = ""
    nl = ""
    prompt_ir = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("ir:"):
            prompt_ir = _parse_prompt_ir(line.split(":", 1)[1])
            continue
        if low.startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
            continue
        if low.startswith("prompt:"):
            painter_prompt = line.split(":", 1)[1].strip()
            continue
        if low.startswith("nl:"):
            nl = line.split(":", 1)[1].strip()
            continue
        for f in _STRUCTURED_FIELDS:
            if low.startswith(f + ":"):
                breakdown[f] = line.split(":", 1)[1].strip()
                break
    # 兼容模型把单行 JSON 错误地格式化成多行的情况; 失败仍走旧协议或 None.
    if prompt_ir is None:
        match = re.search(
            r"^\s*ir:\s*(\{.*?\})(?=\s*(?:\n\s*(?:tags|prompt|nl):|\Z))",
            out,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if match:
            prompt_ir = _parse_prompt_ir(match.group(1))
    if painter_prompt:
        tags = painter_prompt
        nl = ""
    if not tags:
        return out, None, "", None
    if prompt_ir is not None:
        breakdown = _breakdown_from_ir(prompt_ir)
    return tags, breakdown or None, nl, prompt_ir


def _canonicalize_concept(value: str | None) -> str | None:
    """归一化可编辑中文构思；只有同时包含用户锁定/模型补全才算有效。"""
    if not isinstance(value, str):
        return None
    concept = value.strip()
    if concept.lower().startswith("concept:"):
        concept = concept.split(":", 1)[1].strip()
    concept = re.sub(r"\s+", " ", concept)
    match = re.fullmatch(
        r"用户锁定\s*[：:]\s*(.+?)\s*[｜|]\s*模型补全\s*[：:]\s*(.+)",
        concept,
    )
    if not match:
        return None
    locked, added = (part.strip() for part in match.groups())
    if not locked or not added:
        return None
    return f"用户锁定：{locked}｜模型补全：{added}"


_COMPOSER_CLOSE_CROP_TERMS = (
    "close-up", "close up", "upper body", "bust shot", "portrait crop",
)
_COMPOSER_EXTENDED_BODY_TERMS = (
    "full body", "full-body", "full length", "full-length", "entire figure",
    "head to toe", "from head to toe", "legs crossed", "crossed legs",
    "visible feet", "feet visible",
)
_COMPOSER_LOWER_FRAME_ACTION_TERMS = (
    "hem lifted", "lifting skirt", "lifted skirt", "raising skirt",
    "pulling up skirt", "holding up skirt", "tugging skirt hem",
    "gripping skirt hem", "hand on thigh", "hands on thighs",
)
_COMPOSER_ADDED_CLOSE_CROP_TERMS = (
    "近景", "近身裁切", "上半身", "半身特写", "胸像",
    *_COMPOSER_CLOSE_CROP_TERMS,
)
_COMPOSER_LOWER_MANUAL_TARGET_TERMS = (
    "裙", "裙摆", "下摆", "衣摆", "髋", "臀", "大腿",
)
_COMPOSER_MANUAL_TARGET_TERMS = (
    "手", "上衣", "衣服", "肩带", "裙", "下摆", "衣摆", "布料", "道具",
    "头发", "发梢", "发丝",
)
_COMPOSER_MANUAL_ACTION_TERMS = (
    "拿", "持", "握", "抓", "扶", "托", "按", "扯", "拉", "掀", "提", "调整",
    "捏", "抚", "摸", "拨", "梳",
)


def _composer_feasibility_issue(prompt_ir: dict, concept: str,
                                prompt_line: str) -> str | None:
    """拒绝少量可确定的画面容量冲突，让第二次模型调用重新规划。

    这里只检查跨 checkpoint 都明显不可同时呈现的组合；不尝试用代码决定审美、
    姿态细节或最佳镜头。
    """
    ir_text = " ".join(
        str(item).lower()
        for field in ("action", "pose", "composition", "scene")
        for item in prompt_ir.get(field, [])
    )
    render_text = f"{ir_text} {prompt_line.lower()}"
    close_crop = any(term in render_text for term in _COMPOSER_CLOSE_CROP_TERMS)
    extended_body = any(term in render_text for term in _COMPOSER_EXTENDED_BODY_TERMS)
    if close_crop and extended_body:
        return ("Composer 可画性冲突：近景/上半身构图同时要求完整下肢或全身信息；"
                "请改为中景/四分之三身，或删除画面外动作")

    added = concept.split("｜模型补全：", 1)[1] if "｜模型补全：" in concept else ""
    added_clauses = [part.strip() for part in re.split(r"[，,；;。]+", added)
                     if part.strip()]
    lower_manual_clauses = [
        clause for clause in added_clauses
        if any(target in clause for target in _COMPOSER_LOWER_MANUAL_TARGET_TERMS)
        and any(action in clause for action in _COMPOSER_MANUAL_ACTION_TERMS)
    ]
    lower_frame_action = any(
        term in render_text for term in _COMPOSER_LOWER_FRAME_ACTION_TERMS
    )
    added_close_crop = any(
        term in added.lower() for term in _COMPOSER_ADDED_CLOSE_CROP_TERMS
    )
    if (close_crop and (lower_frame_action or lower_manual_clauses)
            and (added_close_crop or lower_manual_clauses)):
        return ("Composer 可画性冲突：近景/上半身构图同时把裙摆、髋部或大腿交互设为重点；"
                "请改为牛仔镜头/四分之三身并让交互区域完整入镜，或删除画面外动作")

    manual_clauses = [
        clause for clause in added_clauses
        if any(target in clause for target in _COMPOSER_MANUAL_TARGET_TERMS)
        and any(action in clause for action in _COMPOSER_MANUAL_ACTION_TERMS)
    ]
    if len(manual_clauses) > 1:
        return ("Composer 可画性冲突：模型补全同时发明了多个手部/服装操作；"
                "只保留一个主要交互，让另一只手支撑姿态或自然放置")
    return None


def _normalize_optional_concept(value, field_name: str = "concept") -> str | None:
    """校验来自 API 的 concept/concept_override，返回统一可追踪形式。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise HTTPException(400, f"{field_name} 必须是字符串")
    if len(value) > MAX_CONCEPT_CHARS:
        raise HTTPException(400, f"{field_name} 过长(>{MAX_CONCEPT_CHARS})")
    concept = _canonicalize_concept(value)
    if concept is None:
        raise HTTPException(400, f"{field_name} 必须包含‘用户锁定：…｜模型补全：…’")
    return concept


def _parse_concept(out: str) -> str | None:
    """从 Composer 响应提取 CONCEPT；旧协议没有该行时返回 None。"""
    for line in out.splitlines():
        if line.strip().lower().startswith("concept:"):
            return _canonicalize_concept(line.strip())
    return None


def _parse_composer_output(out: str, active_lora: bool = False) -> tuple:
    """严格解析 Visual Composer 的 CONCEPT + IR + [LORA] + PROMPT 协议。"""
    text = out.strip().strip("`").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    expected_count = 4 if active_lora else 3
    expected_prefixes = ["concept:", "ir:"]
    if active_lora:
        expected_prefixes.append("lora:")
    expected_prefixes.append("prompt:")
    if len(lines) != expected_count or any(
            not line.lower().startswith(prefix)
            for line, prefix in zip(lines, expected_prefixes)):
        raise RuntimeError("Composer 输出未遵守 CONCEPT + IR + [LORA] + PROMPT 行协议")

    concept = _canonicalize_concept(lines[0])
    try:
        raw_ir = json.loads(lines[1].split(":", 1)[1].strip())
    except (json.JSONDecodeError, TypeError):
        raw_ir = None
    tags, breakdown, nl, prompt_ir = _parse_structured_output(text)
    prompt_line = lines[-1].split(":", 1)[1].strip()
    if concept is None:
        raise RuntimeError("Composer CONCEPT 未区分用户锁定与模型补全")
    if len(concept) > MAX_CONCEPT_CHARS:
        raise RuntimeError(f"Composer CONCEPT 过长(>{MAX_CONCEPT_CHARS})")
    if (prompt_ir is None or not isinstance(raw_ir, dict) or
            set(raw_ir) != set(_IR_FIELDS)):
        raise RuntimeError("Composer IR 缺失、字段不全或不是有效 JSON")
    if not prompt_line or tags != prompt_line:
        raise RuntimeError("Composer PROMPT 缺失或无法解析")
    lora_choices = _parse_lora_choices(text)
    if active_lora and not lora_choices:
        raise RuntimeError("Composer 缺少有效 LORA 选择")
    prompt_line, repetition_collapsed = collapse_exact_prompt_repetition(prompt_line)
    if len(prompt_line) > MAX_PROMPT_EN_CHARS:
        raise RuntimeError(f"Composer PROMPT 过长(>{MAX_PROMPT_EN_CHARS})")
    feasibility_issue = _composer_feasibility_issue(prompt_ir, concept, prompt_line)
    if feasibility_issue:
        raise RuntimeError(feasibility_issue)
    return (prompt_line, breakdown, nl, prompt_ir, _parse_character_hints(text),
            lora_choices, concept, repetition_collapsed)


def match_characters(text: str) -> tuple[list[str], str]:
    """子串匹配正式/自动确认角色名. 正式词典优先, 返回 canonical tag 列表和剩余文本."""
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


async def siliconflow_translate(context: str, reroll: bool = False,
                                completion_level: str | None = None) -> tuple:
    """走 Reasoning Model 生成 Visual Composer 协议。

    返回 (prompt, breakdown, nl, prompt_ir, character_hints, lora_choices,
    concept, repetition_collapsed)。失败会修复一次，再把协议错误交给上层。
    """
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")

    # thinking 默认关 (D2: 思考慢 30s+ 且易复读); 结构化字段已是强制表态机制, 不依赖 CoT.
    # 隐喻/场景仍弱时 config 翻 translate_enable_thinking: true 重测, 不动代码 (见 D18).
    thinking = bool(CFG.get("translate_enable_thinking", False))

    if completion_level is None:
        level_match = re.search(r"^COMPLETION LEVEL:\s*(auto|faithful|free)\s*$",
                                context, flags=re.IGNORECASE | re.MULTILINE)
        completion_level = level_match.group(1).lower() if level_match else DEFAULT_COMPLETION_LEVEL
    completion_level = _normalize_completion_level(completion_level)

    # 补全程度控制创意幅度，不再用输入长度推断。reroll 只改变同一程度下的方案。
    temperature = (float(CFG.get("reroll_temperature", 0.9)) if reroll else {
        "faithful": 0.35,
        "auto": 0.7,
        "free": 0.8,
    }[completion_level])
    nudge = ("Generate a different coherent illustration premise within the same COMPLETION LEVEL. "
             "Keep every explicit lock and vary only decisions that remain open. "
             "Still follow the exact output protocol.\n\n") if reroll else ""
    user_content = ("/no_think " if not thinking else "") + nudge + context
    active_lora = "ACTIVE LORA CONTEXT" in context
    character_appearance_locks = _character_appearance_locks_from_context(context)

    parsed = None
    last_protocol_error = None
    for attempt in range(2):
        repair = ""
        if attempt:
            previous_issue = str(last_protocol_error or "输出协议不合法")[:300]
            repair = (
                "\nREPAIR REQUEST: Your previous response was rejected for this reason: "
                + previous_issue
                + ". Re-plan model-added decisions when the issue is semantic; preserve every USER lock. "
                "Return CONCEPT first, then one "
                "compact valid IR JSON line with all 12 array fields, "
                + ("then the mandatory LORA JSON line using only supplied IDs, " if active_lora else "")
                + "then PROMPT. Use exactly one non-empty line for each required field and no other text.\n"
            )
        r = await CLIENT.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": PAINTER_SYSTEM_PROMPT},
                    # /no_think: Qwen3 软开关, 强制不进思考模式 (思考会慢到 30s+ 且易复读). thinking 开则不前置.
                    {"role": "user", "content": user_content + repair},
                ],
                "temperature": temperature,
                "max_tokens": 1800,
                # ★ 关键: enable_thinking 必须放顶层, 放 extra_body 里硅基流动不认 -> 思考没关掉. (见 D2)
                "enable_thinking": thinking,
            },
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"翻译服务返回 {r.status_code}: {r.text[:200]}")
        data = r.json()
        out = data["choices"][0]["message"]["content"].strip()
        # 极端情况下模型可能仍带 <think>, 清一下
        if "</think>" in out:
            out = out.split("</think>", 1)[1].strip()
        if not out:
            raise RuntimeError("翻译服务返回空内容")

        try:
            parsed = _parse_composer_output(out, active_lora=active_lora)
            if character_appearance_locks is not None:
                identity_issue = _composer_character_lora_appearance_issue(
                    parsed[3], parsed[0], character_appearance_locks
                )
                if identity_issue:
                    raise RuntimeError(identity_issue)
            break
        except RuntimeError as exc:
            last_protocol_error = exc

    if parsed is None:
        raise RuntimeError(f"Composer 协议修复失败: {last_protocol_error}")
    return parsed


async def siliconflow_vision_translate(image_b64: str, context: str, reroll: bool = False, mode: str = "reference") -> tuple[str, dict | None, str, dict | None, dict]:
    """③ 参考图理解: 走硅基流动 Qwen3-VL, 从参考图提取氛围/配色/构图/场景/光影 -> 结构化 breakdown + TAGS.
    image_b64: data URI (data:image/...;base64,...) 或纯 base64. context 同文本 LLM (Known tags + Remaining).
    返回 (tags, breakdown), 复用 _parse_structured_output. 失败抛异常 (上层转 HTTPException). 见 D23.
    mode: "reference"=③ vibe-only (用户参考图); "iterate"=⑤ 保氛围再画一版 (锁住主体+氛围全量提取, D25).
    注意: Qwen3-VL-Instruct 不接受 enable_thinking 参数 (会 400), 故不带; 它是非 thinking 模型, 默认不思考."""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_vision_model", "Qwen/Qwen3-VL-8B-Instruct")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")
    if not image_b64.startswith("data:"):
        image_b64 = "data:image/jpeg;base64," + image_b64

    temperature = float(CFG.get("reroll_temperature", 0.9)) if reroll else 0.4
    nudge = ("Give a DIFFERENT, more creative read of the image's mood and scene. "
             "Still follow the output format and the known-tags rule.\n\n") if reroll else ""

    r = await CLIENT.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": VISION_ITERATE_SYSTEM_PROMPT if mode == "iterate" else VISION_SYSTEM_PROMPT},
                # VL 模型 user content 是数组: 文本 + image_url (OpenAI 视觉格式, 硅基流动兼容, 见 D23)
                {"role": "user", "content": [
                    {"type": "text", "text": nudge + context},
                    {"type": "image_url", "image_url": {"url": image_b64, "detail": "high"}},
                ]},
            ],
            "temperature": temperature,
            "max_tokens": 500,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"视觉服务返回 {r.status_code}: {r.text[:200]}")
    data = r.json()
    out = data["choices"][0]["message"]["content"].strip()
    if "</think>" in out:
        out = out.split("</think>", 1)[1].strip()
    if not out:
        raise RuntimeError("视觉服务返回空内容")

    tags, breakdown, nl, prompt_ir = _parse_structured_output(out)
    # 重复 tag 兜底 (同文本 LLM)
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"视觉输出异常(重复tag: {dupes[0]}), 请重试")
    return tags, breakdown, nl, prompt_ir, _parse_lora_choices(out)


# Anima 期望的 tag 顺序: quality -> count -> character -> general (见 D20).
# quality 由 build_prompt 的 quality_prefix 在更外层 prepend, 这里只规范 prompt_en 内部:
# 把 count (1girl/1boy/solo/...) 从 LLM 输出里提到最前, character 次之, general 垫后. 只重排不增删不去重.
_COUNT_TAG_RE = re.compile(
    r"^(solo|solo focus|"
    r"\d+(girl|boy|other)s?|"      # 1girl, 2girls, 1boy, 1other
    r"\d+\+(girls|boys|others)|"   # 6+girls
    r"multiple (girls|boys|others))$"
)


def collapse_exact_prompt_repetition(prompt: str) -> tuple[str, bool]:
    """只折叠整段完全相同的 comma-segment 序列，不删除有意义的局部强化。"""
    segments = [segment.strip() for segment in prompt.strip().rstrip(".").split(",")
                if segment.strip()]
    count = len(segments)
    for unit_size in range(1, count // 2 + 1):
        if count % unit_size:
            continue
        unit = segments[:unit_size]
        if all(segments[index:index + unit_size] == unit
               for index in range(0, count, unit_size)):
            return ", ".join(unit), True
    return prompt.strip(), False


_FULL_BODY_LOCK_TERMS = (
    "全身", "完整可见", "从头到脚", "full body", "entire figure visible",
)
_FULL_BODY_CONFLICT_TERMS = (
    "mid-shot", "mid shot", "medium shot", "upper body", "close-up", "close up",
    "cropped", "out of frame",
)


def _prepare_composer_tags(tags: list[str], prompt_ir: dict | None,
                           original_text: str, char_tags: list[str]) -> list[str]:
    """Visual Composer 的最小代码护栏：主体计数 + 显式全身构图一致性。

    不继承旧 Painter 的 nude、默认镜头、剪影或风格删改启发式；这些视觉决定
    由 Composer 根据用户构思负责。
    """
    result = [tag.strip() for tag in tags if tag and tag.strip()]
    source = original_text.lower()
    full_body_locked = any(term in source for term in _FULL_BODY_LOCK_TERMS)
    if full_body_locked:
        result = [tag for tag in result
                  if not any(term in tag.lower() for term in _FULL_BODY_CONFLICT_TERMS)]
        if not any(term in tag.lower() for term in ("full body", "entire figure")
                   for tag in result):
            result.append("full body")

    if any(_COUNT_TAG_RE.match(tag.lower()) for tag in result):
        return result
    subject = " ".join(str(item).lower() for item in (prompt_ir or {}).get("subject", []))
    subject_words = set(re.findall(r"[a-z]+", subject))
    if subject_words & {"boy", "boys", "male", "man", "men"} or any(
            term in source for term in ("男孩", "男性", "男人")):
        result.insert(0, "1boy")
    elif char_tags or subject_words & {"girl", "girls", "woman", "women", "female", "person"} or any(
            term in source for term in ("女孩", "少女", "女性", "女人", "女生", "巫女")):
        result.insert(0, "1girl")
    return result


def _prepare_painter_tags(tags: list[str], prompt_ir: dict | None,
                          original_text: str, char_tags: list[str]) -> list[str]:
    """给画师 Prompt 加最小代码护栏：主体计数和未请求的剪影抑制。"""
    result = [tag.strip() for tag in tags if tag and tag.strip()]
    if "剪影" not in original_text and "silhouette" not in original_text.lower():
        result = [tag for tag in result if "silhouette" not in tag.lower()]
    style_requested = any(term in original_text.lower()
                          for term in ("风格", "水彩", "厚涂", "油画", "watercolor", "painting"))
    if not style_requested:
        result = [tag for tag in result
                  if tag.lower() not in {"painterly", "lineart", "line art", "anime lineart"}]
    ir_text = " ".join(
        str(item).lower()
        for field in ("appearance", "clothing", "action", "pose", "scene", "constraints")
        for item in (prompt_ir or {}).get(field, [])
    )
    explicit = any(term in ir_text for term in ("nude", "naked", "explicit", "nipples", "sex")) or any(
        term in original_text.lower() for term in ("裸体", "裸露", "explicit", "nsfw")
    )
    if explicit and not any(term in tag.lower() for term in ("nude", "naked", "explicit", "nipples", "sex") for tag in result):
        result.insert(0, "nude")
    if explicit and "特写" not in original_text and "close-up" not in original_text.lower():
        result = [tag for tag in result if tag.lower() not in {"close-up", "close up"}]
        if not any(term in tag.lower()
                   for term in ("full body", "three-quarter", "medium shot")
                   for tag in result):
            result.append("three-quarter view")
    if any(_COUNT_TAG_RE.match(tag.lower()) for tag in result):
        return result

    subject = " ".join(str(item).lower() for item in (prompt_ir or {}).get("subject", []))
    source = original_text.lower()
    subject_words = set(re.findall(r"[a-z]+", subject))
    if subject_words & {"boy", "boys", "male", "man", "men"} or any(
        term in source for term in ("男孩", "男性", "男人")
    ):
        result.insert(0, "1boy")
    elif char_tags or subject_words & {"girl", "girls", "woman", "women", "female", "person"} or any(
        term in source for term in ("女孩", "少女", "女性", "女人", "女生", "巫女")
    ):
        result.insert(0, "1girl")
    return result


def normalize_tag_order(char_tags: list[str], other_tags: list[str]) -> str:
    """按 Anima 规范序拼接: count -> character -> general. 只重排不增删; 去重(保留首次出现, 见 D23)."""
    count, general = [], []
    for t in other_tags:
        (count if _COUNT_TAG_RE.match(t.strip()) else general).append(t)
    seen, out = set(), []
    for t in count + char_tags + general:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return ", ".join(out)


def _strip_char_bare_names(new_list: list[str], char_tags: list[str]) -> list[str]:
    """删除 LLM 输出里"已知精确角色 tag 的裸名变体".
    例: 已知 char_tag=ganyu_(genshin_impact), 则删 new_list 里的裸名 ganyu (会触发原神 logo, 见 D29).
    裸名 = 精确 tag 去掉 '_(series)' 后缀的前缀部分. 不依赖 LLM 听话, 代码层兜底."""
    bare = set()
    for ct in char_tags:
        for sep in ("_(", " ("):
            if sep in ct:
                name = ct.split(sep, 1)[0].strip().lower()
                bare.update({name, name.replace("_", " "), name.replace(" ", "_")})
                break
        else:
            # 无系列后缀的精确 tag（如 yukinoshita_yukino）：把空格/下划线变体视作同义去重
            tag = ct.strip().lower()
            bare.add(tag.replace("_", " "))
            bare.add(tag.replace(" ", "_"))
    if not bare:
        return new_list
    return [t for t in new_list if t.strip().lower() not in bare]


_RELATION_HINTS = (
    "each other", "facing each other", "duel", "dueling", "kiss", "kissing",
    "hug", "hugging", "embrace", "embracing", "left", "right", "beside",
    "guiding", "opposing", "spatial", "interaction",
)


def infer_render_profile(prompt_ir: dict | None) -> str:
    """根据已解析 IR 选择最小渲染策略，不改变 IR 或 LLM 输出协议.

    Phase 2 只把明确成人单主体的 tag-first 证据收进生产；普通 SFW
    内容继续保留 NL，避免把 P01 的 legacy 胜出泛化成全局删 NL。
    """
    if not prompt_ir:
        return "tag_first"
    subject = [str(item).lower() for item in prompt_ir.get("subject", [])]
    action = [str(item).lower() for item in prompt_ir.get("action", [])]
    pose = [str(item).lower() for item in prompt_ir.get("pose", [])]
    interaction = [str(item).lower() for item in prompt_ir.get("interaction", [])]
    all_terms = " ".join(
        str(item).lower()
        for field in ("subject", "appearance", "clothing", "action", "pose", "interaction", "constraints")
        for item in prompt_ir.get(field, [])
    )
    explicit_terms = ("nude", "naked", "explicit", "nipples", "sex", "lingerie", "breasts")
    adult_nsfw = any(term in all_terms for term in explicit_terms)
    multiple_subjects = len(subject) > 1 or any(
        re.match(r"^(?:[2-9]|[1-9]\d)\+?(?:girls?|boys?|others?)$", item)
        or item.startswith("multiple ")
        for item in subject
    )
    relation = any(any(hint in item for hint in _RELATION_HINTS) for item in interaction)
    complex_motion = len(action) + len(pose) > 2
    if adult_nsfw and not multiple_subjects and not relation and not complex_motion:
        return "tag_first"
    return "relation_hybrid"


def compile_prompt(char_tags: list[str], other_tags: list[str], nl: str = "",
                   profile: str = "relation_hybrid") -> str:
    """把已知 tag、候选 tag 和可选 NL 编译成模型语义 prompt body.

    quality prefix、LoRA trigger 和 workflow 注入仍由 build_prompt 负责；rating tag 由用户手动控制。
    """
    cleaned_tags = [tag.strip() for tag in other_tags if tag and tag.strip()]
    cleaned_tags = _strip_char_bare_names(cleaned_tags, char_tags)
    result = normalize_tag_order(char_tags, cleaned_tags)
    nl = (nl or "").strip() if profile == "relation_hybrid" else ""
    if result and nl:
        return result + ". " + nl
    return result or nl


async def translate(text: str, reroll: bool = False, image_b64: str | None = None,
                    lora_selections=None, include_meta: bool = False,
                    completion_level: str = DEFAULT_COMPLETION_LEVEL,
                    concept_override: str | None = None) -> tuple:
    """中文构思 -> Anima Prompt。角色 canonical knowledge 与 Visual Composer 分工。

    参考图/非 Reasoning Model 降级路径继续保留历史字典行为；SiliconFlow 普通文本
    把完整剩余意图交给 Composer，不再让 ordinary dict 的全命中绕过构思。
    返回 (prompt_en, breakdown, prompt_ir): breakdown 是既有 5 维展示结构,
    prompt_ir 是 12 字段语义计划; 快速路径或旧视觉协议时二者按实际情况为 None.
    include_meta=True 时追加第四项 prompt_ir_meta，供 API additive 返回，不影响旧内部调用。
    reroll=True: 只对 LLM 路径生效, 高温重出一版不同补全方案, 跳过缓存(探索性, 不污染正常缓存).
    image_b64: ③ 参考图 (data URI 或 base64). 有图走视觉 LLM 提氛围.
    lora_selections: Active LoRA Asset/Profile；存在时所有路径都使用同一 binding context.
    completion_level: auto/faithful/free；concept_override 是用户编辑后的构思控制面。"""
    if not isinstance(text, str):
        raise HTTPException(400, "提示词必须是字符串")
    if len(text) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"提示词过长(>{MAX_USER_PROMPT_CHARS})")
    completion_level = _normalize_completion_level(completion_level)
    concept_override = _normalize_optional_concept(concept_override, "concept_override")
    backend = CFG.get("translate", "none")
    lora_selections = apply_lora_intent_hints(text, lora_selections)
    lora_context, normalized_loras, lora_revision = build_lora_context(lora_selections)
    has_lora = bool(normalized_loras)
    registry = get_lora_registry()
    has_character_lora = any(
        (registry.get(selection["key"]) or {}).get("type") == "character"
        for selection in normalized_loras
    )
    appearance_source = text + (("\n" + concept_override) if concept_override else "")
    character_appearance_locks = (
        sorted(_explicit_character_appearance_locks(appearance_source))
        if has_character_lora else []
    )

    def finish(prompt_en: str, breakdown: dict | None, prompt_ir: dict | None,
               meta: dict, bindings: list[dict] | None = None,
               lora_warnings: list[str] | None = None):
        meta = dict(meta)
        meta.update({
            "lora_aware": has_lora,
            "lora_bindings": bindings or [],
            "lora_warnings": lora_warnings or [],
            "registry_revision": lora_revision if has_lora else None,
        })
        result = (prompt_en, breakdown, prompt_ir)
        return result + (meta,) if include_meta else result

    # Layer 0: 角色子串匹配 (移除角色名, 得到剩余文本)
    char_tags, char_remaining = match_characters(text)

    # 普通文本 Composer 直接读取完整剩余意图；参考图/降级后端继续沿用历史属性词典。
    if backend == "siliconflow" and not image_b64:
        hits, remaining = [], char_remaining
    else:
        hits, remaining = match_dict_words(char_remaining)
    misses = [p.strip() for p in re.split(r"[,，、;；\n]+", remaining) if p.strip()]

    # ③ 参考图: 有图走视觉 LLM 提取氛围 (图 + 文本上下文), 不走下面的文本 LLM/快速路径.
    # 图是氛围参考, 文本(若有)给主体; 角色词典仍预匹配(可靠). 不缓存(图探索性, key 含图复杂). 见 D23.
    if image_b64:
        ctx_lines = []
        if char_tags:
            ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
        if hits:
            ctx_lines.append(f"Known attribute tags: {', '.join(hits)}")
        ctx_lines.append(f"User instruction: {', '.join(misses) if misses else '(no specific instruction - extract everything from the image)'}")
        if lora_context:
            ctx_lines.append(lora_context)
            ctx_lines.append(f"Registry revision: {lora_revision}")
        context = "\n".join(ctx_lines)
        try:
            vision_result = await siliconflow_vision_translate(image_b64, context, reroll=reroll)
            new_tags, breakdown, nl, prompt_ir = vision_result[:4]
            lora_choices = vision_result[4] if len(vision_result) > 4 else {}
        except Exception as e:
            raise HTTPException(502, f"参考图理解失败, 请稍后重试 ({e})")
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        result = compile_prompt(char_tags, hits + new_list, nl, infer_render_profile(prompt_ir))
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras, lora_choices)
        result = compile_lora_bindings(result, bindings)
        return finish(
            result, breakdown, prompt_ir,
            _prompt_ir_meta("vision_reference", reroll, prompt_ir, char_tags, hits,
                            completion_level=completion_level),
            bindings, lora_warnings,
        )

    char_fragments = [p.strip() for p in re.split(r"[,，、;；\n]+", char_remaining)
                      if p.strip()]
    # 纯角色名仍走确定性 canonical 快路，避免一句角色名被自动编造新场景；同时补齐构思控制面。
    if (backend == "siliconflow" and char_tags and not char_fragments and
            not has_lora and concept_override is None):
        result = compile_prompt(char_tags, ["1girl", "solo"], profile="tag_first")
        concept = f"用户锁定：{text.strip()}｜模型补全：无"
        return finish(
            result, None, None,
            _prompt_ir_meta("canonical", reroll, char_tags=char_tags,
                            completion_level=completion_level, concept=concept),
        )

    # 参考图/非 Reasoning Model 的旧路径保留 ordinary dict 全命中快路。
    if backend != "siliconflow" and not misses and not has_lora:
        if char_tags and not hits:
            result = compile_prompt(char_tags, ["1girl", "solo"], profile="tag_first")
            concept = f"用户锁定：{text.strip()}｜模型补全：无"
            return finish(result, None, None,
                          _prompt_ir_meta("canonical", reroll, char_tags=char_tags,
                                          completion_level=completion_level,
                                          concept=concept))
        all_tags = char_tags + hits
        if all_tags:
            result = compile_prompt(char_tags, hits, profile="tag_first")
            return finish(result, None, None,
                          _prompt_ir_meta("dictionary", reroll,
                                          char_tags=char_tags, attribute_tags=hits,
                                          completion_level=completion_level))
        raise HTTPException(400, "提示词为空")

    # Layer 2: 有未命中 -> 后端处理
    if backend == "none":
        # 未翻译部分原样保留 (混输英文 tag 时合适)
        result = compile_prompt(char_tags, hits + misses, profile="tag_first")
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras)
        if has_lora:
            lora_warnings.append("Reasoning Model 未启用：已注入确定性 LoRA binding，但未执行语义冲突检查")
        result = compile_lora_bindings(result, bindings)
        return finish(result, None, None,
                      _prompt_ir_meta("faithful", reroll,
                                      char_tags=char_tags, attribute_tags=hits,
                                      completion_level=completion_level),
                      bindings, lora_warnings)

    if backend == "siliconflow":
        # 普通文本 Composer 接收完整剩余意图；ordinary dict 不再抢先裁掉词语或触发全命中。
        hits = []
        misses = char_fragments
        ctx_lines = [
            f"COMPLETION LEVEL: {completion_level.upper()}",
            f"USER IDEA:\n{text}",
        ]
        if char_tags:
            ctx_lines.append(f"KNOWN CANONICAL TAGS: {', '.join(char_tags)}")
        if concept_override:
            ctx_lines.append(
                "CONCEPT OVERRIDE (authoritative; copy unchanged into CONCEPT): "
                + concept_override
            )
        if lora_context:
            ctx_lines.append(lora_context)
            if has_character_lora:
                ctx_lines.append(
                    _CHARACTER_APPEARANCE_LOCKS_PREFIX + " "
                    + json.dumps(character_appearance_locks, ensure_ascii=False,
                                 separators=(",", ":"))
                )
            ctx_lines.append(f"Registry revision: {lora_revision}")
        context = "\n".join(ctx_lines)

        cache_key = context
        if not reroll and cache_key in _TRANSLATE_CACHE:
            cached = _TRANSLATE_CACHE[cache_key]
            cached_result, cached_breakdown, cached_ir = cached[:3]
            cached_bindings = cached[3] if len(cached) > 3 else []
            cached_warnings = cached[4] if len(cached) > 4 else []
            cached_concept = cached[5] if len(cached) > 5 else concept_override
            cached_repetition = cached[6] if len(cached) > 6 else False
            return finish(
                cached_result, cached_breakdown, cached_ir,
                _prompt_ir_meta(
                    "visual_composer", reroll, cached_ir, char_tags, hits,
                    completion_level=completion_level, concept=cached_concept,
                    concept_override_applied=concept_override is not None,
                    repetition_collapsed=cached_repetition,
                ),
                cached_bindings, cached_warnings,
            )
        try:
            translated = await siliconflow_translate(context, reroll=reroll)
            new_tags, breakdown, nl, prompt_ir, character_hints = translated[:5]
            lora_choices = translated[5] if len(translated) > 5 else {}
            concept = translated[6] if len(translated) > 6 else None
            repetition_collapsed = bool(translated[7]) if len(translated) > 7 else False
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        if concept_override is not None:
            concept = concept_override
        lookup_results = []
        resolved_char_tags = list(char_tags)
        known_names = set(_character_names())
        active_lora_aliases = lora_selection_aliases(normalized_loras) if has_lora else set()
        if not character_hints:
            character_hints = _infer_character_hints_from_ir(
                prompt_ir, misses, known_names, char_tags
            )
        for hint in character_hints:
            name = hint["name"]
            if name.strip().lower() in active_lora_aliases:
                continue
            candidate = _normalize_character_candidate(hint["candidate_tag"]) or hint["candidate_tag"]
            if name in known_names or candidate in char_tags:
                continue
            lookup = await lookup_character(name, candidate)
            lookup_results.append(lookup)
            canonical = lookup.get("canonical_tag")
            if lookup.get("status") == "likely_supported" and canonical not in resolved_char_tags:
                resolved_char_tags.append(canonical)
            elif lookup.get("status") == "unavailable" and candidate not in resolved_char_tags:
                # 兜底: Danbooru 不可达时用 LLM 候选（已归一化），不写 auto cache，只服务本次 Prompt
                resolved_char_tags.append(candidate)
        # 编译: canonical 角色 + Composer Prompt；仅保留确定性计数/显式构图护栏。
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        control_text = text + (("\n" + concept_override) if concept_override else "")
        composer_tags = _prepare_composer_tags(new_list, prompt_ir, control_text,
                                               resolved_char_tags)
        result = compile_prompt(resolved_char_tags, composer_tags, nl,
                                infer_render_profile(prompt_ir))
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras, lora_choices)
        result = compile_lora_bindings(result, bindings)
        # reroll 不写缓存: 探索性结果不应顶掉正常翻译的缓存原版 (见 D19)
        if not reroll:
            if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
                _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
            _TRANSLATE_CACHE[cache_key] = (
                result, breakdown, prompt_ir, bindings, lora_warnings,
                concept, repetition_collapsed,
            )
        return finish(
            result, breakdown, prompt_ir,
            _prompt_ir_meta(
                "visual_composer", reroll, prompt_ir,
                resolved_char_tags, hits, lookup_results,
                completion_level=completion_level, concept=concept,
                concept_override_applied=concept_override is not None,
                repetition_collapsed=repetition_collapsed,
            ),
            bindings, lora_warnings,
        )

    if backend == "google":
        try:
            translated_missing = await google_translate_batch(misses)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        result = compile_prompt(char_tags, hits + translated_missing, profile="tag_first")
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras)
        if has_lora:
            lora_warnings.append("Google 翻译降级路径未执行 LoRA 语义冲突检查")
        result = compile_lora_bindings(result, bindings)
        return finish(result, None, None,
                      _prompt_ir_meta("translation", reroll,
                                      char_tags=char_tags, attribute_tags=hits,
                                      completion_level=completion_level),
                      bindings, lora_warnings)

    raise HTTPException(500, f"未知的 translate 后端: {backend}")


async def google_translate_batch(texts: list[str]) -> list[str]:
    """免费 Google Translate gtx 端点 (本机需可访问谷歌). 任一条失败抛异常."""
    res = []
    for t in texts:
        r = await CLIENT.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "zh-CN", "tl": "en", "dt": "t", "q": t},
            timeout=10,
        )
        r.raise_for_status()
        res.append("".join(seg[0] for seg in r.json()[0]).strip())
    return res


# ---------- Workflow 注入 ----------
def sanitize_for_api(wf: dict) -> dict:
    """剔除/替换"只能从 ComfyUI 前端排队时才能跑"或"会干扰后端取图"的节点。
    依赖 extra_pnginfo['workflow'] (前端 UI 图), 后端 /prompt 不带, 会崩:
      - WidgetToString (KJNodes): 读 extra_pnginfo['workflow'] -> TypeError
      - Image Saver Metadata:     依赖 WidgetToString
      - Image Saver Simple:       依赖上面的 metadata, 且 embed_workflow 也要 extra_pnginfo
    把 Image Saver Simple 换成内置 SaveImage (API 可靠出图, outputs.images 标准格式, 后端能读)。
    另剔 Image Comparer (rgthree): 本身不崩(extra_pnginfo=None 时它只调 save_images 存盘), 但它是
      OUTPUT_NODE(继承 PreviewImage), 中间预览图会进 /history; submit_and_wait 取"第一个有图的节点",
      会误取 comparer 的中间图(手修版/原图)而非最终 SaveImage -> 必须剥。"""
    INCOMPAT = {"WidgetToString", "Image Saver Metadata", "Image Comparer (rgthree)"}
    new_id = 100
    for nid in list(wf.keys()):
        ct = wf[nid].get("class_type", "")
        if ct == "Image Saver Simple":
            images_src = wf[nid].get("inputs", {}).get("images")
            # 文件名带上日期时间, 接近原 Image Saver 的可读性 (避免 anima_00007_.png 这种看不出时间的)
            wf[str(new_id)] = {"class_type": "SaveImage",
                               "inputs": {"images": images_src,
                                          "filename_prefix": f"anima_{time.strftime('%Y%m%d')}"}}
            new_id += 1
            del wf[nid]
        elif ct in INCOMPAT:
            del wf[nid]
    return wf


def _workflow_lora_entries(bindings: list[dict], strength_char: float | None = None,
                           strength_style: float | None = None) -> list[dict]:
    """把语义 binding 压成物理文件加载表；同一 safetensors 最多加载一次。"""
    entries: list[dict] = []
    by_file: dict[str, tuple[float, float]] = {}
    for binding in bindings:
        lora_type = binding.get("type")
        legacy_strength = (strength_char if lora_type == "character"
                           else strength_style if lora_type in {"style", "action", "expression"}
                           else None)
        sm = float(legacy_strength) if legacy_strength is not None else float(
            binding.get("strength_model", 1.0))
        sc = float(legacy_strength) if legacy_strength is not None else float(
            binding.get("strength_clip", 1.0))
        for value in (sm, sc):
            if not 0 <= value <= 2:
                raise HTTPException(400, "LoRA 强度需在 0~2 之间")
        filename = str(binding.get("file") or "").strip()
        if not filename:
            raise HTTPException(500, f"LoRA {binding.get('key', '')} 缺少文件名")
        previous = by_file.get(filename)
        if previous is not None:
            if previous != (sm, sc):
                raise HTTPException(400, f"同一 LoRA 文件 {filename} 被以不同强度重复选择")
            continue
        by_file[filename] = (sm, sc)
        entries.append({"name": filename, "strength": sm,
                        "clipStrength": sc, "active": True})
    return entries


def build_prompt(wf_name: str, prompt_en: str, width: int | None, height: int | None,
                 lora_keys: list[str] | None = None,
                 strength_char: float | None = None, strength_style: float | None = None,
                 image_filename: str | None = None, denoise: float | None = None,
                 detailer: dict | None = None,
                 negative_text: str | None = None,
                 lora_bindings: list[dict] | None = None,
                 registry_revision: str | None = None) -> dict:
    wcfg = WORKFLOWS[wf_name]
    wf = json.loads((BASE / wcfg["file"]).read_text(encoding="utf-8"))
    wf = sanitize_for_api(wf)
    seed = random.randint(1, 2**31 - 1)

    # 统一 seed: 把工作流里所有 int 型 seed/noise_seed 输入都写成正整数。
    # 为什么必须做:
    #   ComfyUI 前端排队时用 seed=-1 表示"随机", Impact Pack / rgthree 的 onprompt 钩子
    #   负责在执行前把 -1 替换成真随机数。我们走 /prompt API 时那些钩子拿到的 JSON 是
    #   我们拼的——只要工作流里还残留 -1 (典型: FaceDetailer/SEGS 内部 seed), Impact Pack
    #   的 np.random.default_rng(-1) 直接抛 ValueError, 整个 Impact Pack 异常退出,
    #   FaceDetailer 人脸修复/wildcards 全部失效 (出图"很原生"的直接原因)。
    # 列表值 (如 ["6", 0] 的节点连接) 会被 isinstance(int) 跳过, 连接关系不动。
    SEED_FIELDS = ("seed", "noise_seed")
    for node in wf.values():
        inputs = node.get("inputs", {})
        for field in SEED_FIELDS:
            if field in inputs and isinstance(inputs[field], int):
                inputs[field] = seed

    def set_input(node_key: str, field: str, value):
        node = wf.get(str(wcfg[node_key]))
        if not node:
            raise HTTPException(500, f"workflow {wf_name} 配置错误: 节点 {wcfg[node_key]} 不存在")
        node["inputs"][field] = value

    # 仅供 Rendering Strategy 实验的负面覆盖。正常 config 不提供
    # negative_text_node，因此生产路径继续使用工作流固定负面模板 (D6/D18).
    negative_node_id = wcfg.get("negative_text_node")
    if negative_text and negative_node_id:
        negative_node = wf.get(str(negative_node_id))
        if not negative_node or negative_node.get("class_type") != "ImpactWildcardProcessor":
            raise HTTPException(500, f"workflow {wf_name} 负面实验节点配置错误")
        extra_negative = negative_text.strip().strip(",")
        if not extra_negative:
            raise HTTPException(400, "实验负面提示词为空")
        inputs = negative_node.get("inputs", {})
        for field in ("wildcard_text", "populated_text"):
            base_negative = inputs.get(field)
            if not isinstance(base_negative, str):
                raise HTTPException(500, f"workflow {wf_name} 负面节点缺少 {field} 文本")
            inputs[field] = base_negative.rstrip(" ,") + ", " + extra_negative

    # LoRA Binding: 客户端只提供 key/profile/optional ID，exact tags/file/strength 重新从
    # 同 revision Registry 解析。Prompt 与 workflow 注入共享同一 binding snapshot (D39).
    effective_bindings: list[dict] = []
    if lora_bindings:
        selections = _bindings_as_selections(lora_bindings)
        effective_bindings, _, _ = resolve_lora_selections(
            selections, expected_revision=registry_revision)
    elif lora_keys:
        effective_bindings, _, registry_revision = resolve_lora_selections(lora_keys)
    if effective_bindings:
        if "lora_node" not in wcfg:
            raise HTTPException(400, f"工作流 {wf_name} 不支持 LoRA")
        lora_entries = _workflow_lora_entries(
            effective_bindings, strength_char=strength_char, strength_style=strength_style)
        set_input("lora_node", "loras", {"__value__": lora_entries})
        prompt_en = compile_lora_bindings(prompt_en, effective_bindings)

    # rating tag (safe/sensitive/questionable/explicit) 不再由关键词启发式推断。
    # 用户可在生成前编辑英文 Prompt 明确加入，后端原样保留。
    full_prompt = wcfg.get("quality_prefix", "") + prompt_en
    set_input("prompt_node", "text", full_prompt)
    if "negative_node" in wcfg:
        set_input("negative_node", "text", wcfg.get("negative_prefix", "") + wcfg.get("negative_extra", ""))
    if "seed_node" in wcfg:
        set_input("seed_node", "seed", seed)
    if width and height and "size_node" in wcfg:
        set_input("size_node", "width", width)
        set_input("size_node", "height", height)
    # ---- detailer 拼接 + inpaint/img2img 源切 (D32) ----
    # 合并版工作流: 一份 AnimaFull.json 含 txt2img/img2img/inpaint + 4路 detailer.
    # build_prompt 按 detailer:{hand,nsfw,face,eyes} 删未选节点、重连, 真正的"拼接" (删的节点不执行, 省时).
    detailer_cfg = wcfg.get("detailer_nodes")
    if image_filename and "image_node" in wcfg:
        set_input("image_node", "image", image_filename)   # LoadImage: img2img 用
    if detailer_cfg:
        chain_source = "43"   # 主 VAEDecode (detailer 链源)
        save_id = next((nid for nid, n in wf.items() if n.get("class_type") == "SaveImage"), None)
        if not save_id:
            raise HTTPException(500, f"workflow {wf_name} 找不到 SaveImage 节点")
        prev = chain_source
        for dkey, nid in detailer_cfg.items():   # 顺序 = 图链顺序 (hand->nsfw->face->eyes)
            nid = str(nid)
            if nid not in wf:
                continue
            if detailer and detailer.get(dkey):
                wf[nid]["inputs"]["image"] = [prev, 0]
                prev = nid
            else:
                del wf[nid]   # 删未选 detailer 节点, 重连 (依赖节点变不可达, 不执行)
        wf[save_id]["inputs"]["images"] = [prev, 0]
    # 路由必须每次显式写入：原工作流节点 32 曾默认 value=2，若 txt2img 不覆盖，
    # 会误走 salt.jpg -> Resize -> VAEEncode，绕过节点 56 的尺寸并引发额外 VRAM 换入。
    if "switch_node" in wcfg:
        set_input("switch_node", "select", 2 if image_filename else 1)
    # img2img 注入: input2=VAEEncode latent + 覆盖主 KSampler denoise
    if image_filename and "image_node" in wcfg:
        if denoise is not None and "denoise_node" in wcfg:
            set_input("denoise_node", "denoise", float(denoise))
    return {"prompt": wf, "client_id": CLIENT_ID, "_seed": seed}


async def upload_image_to_comfy(image_bytes: bytes) -> str:
    """上传图到 ComfyUI input 目录 (POST /upload/image), 返回文件名 (给 LoadImage set_input 用, 见 D26)."""
    fname = f"{uuid.uuid4().hex[:12]}.png"
    r = await CLIENT.post(f"{COMFY}/upload/image",
                          files={"image": (fname, image_bytes, "image/png")},
                          data={"type": "input", "overwrite": "true"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI 上传图片失败: {r.status_code} {r.text[:200]}")
    return r.json()["name"]


async def submit_and_wait(wf_name: str, prompt_en: str, width, height, lora_keys: list[str] | None = None,
                          strength_char: float | None = None, strength_style: float | None = None,
                          image_filename: str | None = None, denoise: float | None = None,
                          detailer: dict | None = None,
                          negative_text: str | None = None,
                          lora_bindings: list[dict] | None = None,
                          registry_revision: str | None = None) -> str:
    payload = build_prompt(
        wf_name, prompt_en, width, height, lora_keys,
        strength_char, strength_style, image_filename, denoise,
        detailer, negative_text, lora_bindings=lora_bindings,
        registry_revision=registry_revision,
    )
    seed = payload.pop("_seed")
    r = await CLIENT.post(f"{COMFY}/prompt", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI 拒绝: {r.text[:200]}")
    pid = r.json()["prompt_id"]

    deadline = time.time() + int(CFG.get("timeout_seconds", 300))
    while time.time() < deadline:
        await asyncio.sleep(2)
        h = (await CLIENT.get(f"{COMFY}/history/{pid}")).json()
        if pid not in h:
            continue
        entry = h[pid]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError("ComfyUI 执行出错")
        if not status.get("completed", False) and "outputs" not in entry:
            continue
        for node_out in entry.get("outputs", {}).values():
            for img in node_out.get("images", []):
                if img.get("type") in ("output", "temp"):
                    data = (await CLIENT.get(f"{COMFY}/view", params={
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })).content
                    fname = f"{uuid.uuid4().hex[:12]}.png"
                    (IMAGES / fname).write_bytes(data)
                    return fname
        raise RuntimeError("未找到输出图片")
    raise TimeoutError("生成超时")


# ---------- 队列 worker ----------
async def worker():
    while True:
        job_id = await QUEUE.get()
        job = JOBS[job_id]
        job["status"] = "running"
        try:
            fname = await submit_and_wait(job["workflow"], job["prompt_en"], job.get("width"), job.get("height"),
                                         job.get("loras"), job.get("strength_char"), job.get("strength_style"),
                                         job.get("image_filename"), job.get("denoise"),
                                         job.get("detailer"), lora_bindings=job.get("lora_bindings"),
                                         registry_revision=job.get("registry_revision"))
            job.update(status="done", image=f"/images/{fname}")
        except Exception as e:
            job.update(status="failed", error=str(e))
        finally:
            QUEUE.task_done()


@app.on_event("startup")
async def _startup():
    asyncio.create_task(worker())


# ---------- API ----------
@app.get("/api/health")
async def health():
    try:
        r = await CLIENT.get(f"{COMFY}/system_stats", timeout=5)
        comfy_ok = r.status_code == 200
    except Exception:
        comfy_ok = False
    return {"ok": True, "comfy": comfy_ok}


@app.get("/api/auth/check")
async def auth_check(token: str = Depends(verify_token)):
    """只验证邀请码有效性 (verify_token: 不查日限, 不耗 GPU 配额). 给前端登录门禁用, 避免用 /api/workflows
    (auth, 查日限) 验证导致达日限的朋友登不进来。"""
    return {"ok": True}


@app.get("/api/workflows")
async def list_workflows(token: str = Depends(auth)):
    return [
        {"name": k, "label": v.get("label", k), "sizes": v.get("sizes")}
        for k, v in WORKFLOWS.items()
    ]


@app.get("/api/loras")
async def list_loras(token: str = Depends(auth)):
    """LoRA Asset 列表；Registry Profiles 优先，unknown/incomplete 放 other。"""
    reg = get_lora_registry()
    items = [
        {
            "key": v["key"],
            "type": v["type"],
            "name": v["name"],
            "description": v.get("description", ""),
            "preview": v.get("preview"),
            "configured": v["configured"],
            "source": v["source"],
            "trigger_policy": v.get("trigger_policy", "none"),
            "provides": v.get("provides", []),
            "verified": v.get("verified"),
            "strength_model": v.get("strength_model", 1.0),
            "strength_clip": v.get("strength_clip", 1.0),
            "default_profile": (v.get("selection") or {}).get("default_profile"),
            "allow_multiple_profiles": bool(
                (v.get("selection") or {}).get("allow_multiple_profiles", False)),
            "profiles": [
                {
                    "id": pid,
                    "name": profile.get("name", pid),
                    "aliases": profile.get("aliases", []),
                    "provides": profile.get("provides", []),
                    "verified": profile.get("verified"),
                    "optional": [
                        {"id": oid, "name": option.get("name", oid),
                         "provides": option.get("provides", [])}
                        for oid, option in (profile.get("optional_tags") or {}).items()
                    ],
                }
                for pid, profile in (v.get("profiles") or {}).items()
            ],
        }
        for v in reg.values()
    ]
    stackable_detail_types = {"style", "action", "expression"}
    return {"characters": [i for i in items if i["type"] == "character"],
            "styles": [i for i in items if i["type"] in stackable_detail_types],
            "other": [i for i in items
                      if i["type"] != "character" and i["type"] not in stackable_detail_types]}


def _extract_lora_selections(body: dict):
    if "lora_selections" in body:
        return body.get("lora_selections") or []
    loras = body.get("loras")
    if loras is not None:
        return loras
    single = body.get("lora")
    return [single] if single else []


def _bindings_as_selections(bindings: list[dict] | None) -> list[dict]:
    result = []
    for binding in bindings or []:
        profile_ids = _selection_profile_ids(binding)
        selection = {
            "key": binding.get("key"),
            "profile": profile_ids[0] if len(profile_ids) == 1 else None,
            "mode": "explicit",
            "optional": binding.get("optional") or [],
        }
        if len(profile_ids) > 1:
            selection["profiles"] = profile_ids
        if binding.get("optional_by_profile"):
            selection["optional_by_profile"] = binding["optional_by_profile"]
        for field in ("strength_model", "strength_clip"):
            if binding.get(field) is not None:
                selection[field] = binding[field]
        result.append(selection)
    return result


@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只编译不排队: 中文构思 -> Anima Prompt，不计入 image 限额。
    返回 {concept, prompt_en, breakdown, prompt_ir, prompt_ir_meta}；completion_level 控制补全幅度，
    concept_override 可把用户编辑后的构思重新编译为 Prompt。
    body.reroll=true: LLM 高温重出一版不同画师补全方案 (抽卡再抽, 跳过缓存, 见 D19).
    body.image: 可选, 参考图 base64 (data URI). 有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23)."""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    image = (body.get("image") or "").strip()
    if not prompt and not image:
        raise HTTPException(400, "提示词和参考图不能同时为空")
    if len(prompt) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"提示词过长(>{MAX_USER_PROMPT_CHARS})")
    if len(image) > 5_000_000:
        raise HTTPException(400, "参考图过大(>5MB)")
    if prompt:
        check_banned(prompt)
    reroll = bool(body.get("reroll"))
    completion_level = _normalize_completion_level(body.get("completion_level"))
    concept_override = _normalize_optional_concept(
        body.get("concept_override"), "concept_override")
    if concept_override:
        check_banned(concept_override)
    prompt_en, breakdown, prompt_ir, prompt_ir_meta = await translate(
        prompt, reroll=reroll, image_b64=(image or None),
        lora_selections=_extract_lora_selections(body), include_meta=True,
        completion_level=completion_level, concept_override=concept_override,
    )
    check_banned(prompt_en)
    return {
        "prompt_en": prompt_en,
        "breakdown": breakdown,
        "prompt_ir": prompt_ir,
        "prompt_ir_meta": prompt_ir_meta,
        "concept": prompt_ir_meta.get("concept"),
        "lora_bindings": prompt_ir_meta.get("lora_bindings", []),
        "lora_warnings": prompt_ir_meta.get("lora_warnings", []),
        "registry_revision": prompt_ir_meta.get("registry_revision"),
    }


async def _enqueue(token: str, wf_name: str, prompt_en: str, prompt_raw: str,
                   size, lora_selections, strength_char, strength_style,
                   image_filename: str | None = None, denoise: float | None = None,
                   detailer: dict | None = None,
                   lora_bindings: list[dict] | None = None,
                   registry_revision: str | None = None,
                   concept: str | None = None,
                   completion_level: str = DEFAULT_COMPLETION_LEVEL) -> str:
    """校验并入队一次出图 (USAGE+1 / JOBS / QUEUE.put). create_job 与 /api/dialog/turn 共用. 返回 job_id.
    prompt_en/prompt_raw 的 banned 检查由调用方负责 (两处逻辑不同)."""
    completion_level = _normalize_completion_level(completion_level)
    concept = _normalize_optional_concept(concept, "concept")
    if wf_name not in WORKFLOWS:
        raise HTTPException(400, "未知工作流")
    wcfg = WORKFLOWS[wf_name]
    width = height = None
    if wcfg.get("sizes"):
        size = size or wcfg["sizes"][0]
        if size not in wcfg["sizes"]:
            raise HTTPException(400, "非法尺寸")
        width, height = map(int, size.split("x"))
    # LoRA selection/binding 校验；客户端 injected_tags/file 不可信，按 ID + revision 重解析。
    resolved_bindings: list[dict] = []
    lora_warnings: list[str] = []
    resolved_revision: str | None = None
    binding_requests = None
    if lora_bindings:
        if not isinstance(lora_bindings, list):
            raise HTTPException(400, "lora_bindings 必须是数组")
        binding_requests = _bindings_as_selections(
            [b for b in lora_bindings if isinstance(b, dict)])
    elif lora_selections:
        binding_requests = lora_selections
    if binding_requests:
        if "lora_node" not in wcfg:
            raise HTTPException(400, "该工作流不支持 LoRA")
        resolved_bindings, lora_warnings, resolved_revision = resolve_lora_selections(
            binding_requests,
            expected_revision=registry_revision if lora_bindings else None,
        )
        for sv in (strength_char, strength_style):
            if sv is not None:
                try:
                    sv = float(sv)
                except (TypeError, ValueError):
                    raise HTTPException(400, "LoRA 强度需为数字")
                if not (0 <= sv <= 1):
                    raise HTTPException(400, "LoRA 强度需在 0~1 之间")
        prompt_en = compile_lora_bindings(prompt_en, resolved_bindings)
    else:
        strength_char = strength_style = None
    if len(prompt_en) > MAX_COMPILED_PROMPT_CHARS:
        raise HTTPException(400, f"编译后的提示词过长(>{MAX_COMPILED_PROMPT_CHARS})")
    # detailer 校验 (只允许 face/hand/nsfw/eyes)
    if detailer:
        allowed = {"face", "hand", "nsfw", "eyes"}
        bad = set(detailer) - allowed
        if bad:
            raise HTTPException(400, f"未知精修类型: {bad}")
        detailer = {k: bool(v) for k, v in detailer.items() if k in allowed}
    USAGE[token][1] += 1
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "id": job_id, "token": token, "workflow": wf_name,
        "prompt_raw": prompt_raw, "prompt_en": prompt_en,
        "concept": concept, "completion_level": completion_level,
        "width": width, "height": height,
        "loras": [b["key"] for b in resolved_bindings] or None,
        "lora_bindings": resolved_bindings,
        "lora_warnings": lora_warnings,
        "registry_revision": resolved_revision,
        "strength_char": strength_char, "strength_style": strength_style,
        "image_filename": image_filename, "denoise": denoise,
        "detailer": detailer,
        "status": "queued", "created": time.time(),
    }
    await QUEUE.put(job_id)
    return job_id


@app.post("/api/jobs")
async def create_job(req: Request, token: str = Depends(auth)):
    body = await req.json()
    wf_name = body.get("workflow", "")
    prompt_en = (body.get("prompt_en") or "").strip()
    prompt_raw = (body.get("prompt") or "").strip() or prompt_en   # 原始中文, 仅存档展示; 不传则同 prompt_en
    lora_selections = _extract_lora_selections(body)
    lora_bindings = body.get("lora_bindings") or None
    registry_revision = (body.get("registry_revision") or "").strip() or None
    concept = _normalize_optional_concept(body.get("concept"), "concept")
    completion_level = _normalize_completion_level(body.get("completion_level"))
    if not prompt_en or len(prompt_en) > MAX_PROMPT_EN_CHARS:
        raise HTTPException(400, f"提示词为空或过长(>{MAX_PROMPT_EN_CHARS})")
    if prompt_raw != prompt_en and len(prompt_raw) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"原始提示词过长(>{MAX_USER_PROMPT_CHARS})")
    check_banned(prompt_en)
    if prompt_raw != prompt_en:
        check_banned(prompt_raw)
    # img2img: 图 base64 -> 上传 ComfyUI -> 拿文件名 (见 D26)
    image_b64 = (body.get("image") or "").strip()
    denoise = body.get("denoise")
    detailer = body.get("detailer")
    image_filename = None
    if image_b64:
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        try:
            image_filename = await upload_image_to_comfy(base64.b64decode(image_b64))
        except Exception as e:
            raise HTTPException(502, f"图片上传失败 ({e})")
    job_id = await _enqueue(token, wf_name, prompt_en, prompt_raw,
                            body.get("size"), lora_selections,
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, detailer,
                            lora_bindings=lora_bindings,
                            registry_revision=registry_revision,
                            concept=concept, completion_level=completion_level)
    job = JOBS[job_id]
    return {"id": job_id, "prompt_en": job["prompt_en"],
            "lora_bindings": job.get("lora_bindings", []),
            "lora_warnings": job.get("lora_warnings", []),
            "registry_revision": job.get("registry_revision"),
            "concept": job.get("concept"),
            "completion_level": job.get("completion_level")}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, token: str = Depends(auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    queued_ids = list(QUEUE._queue)  # MVP 简单读取
    resp = {k: job[k] for k in (
        "id", "status", "prompt_raw", "prompt_en", "workflow",
        "concept", "completion_level", "lora_bindings", "lora_warnings",
        "registry_revision") if k in job}
    if job["status"] == "queued":
        resp["position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 1
    if job["status"] == "done":
        resp["image"] = job["image"]
    if job["status"] == "failed":
        resp["error"] = job.get("error", "未知错误")
    return resp


# ---------- ⑤ 对话迭代 (骨架 + A/D) ----------
@app.post("/api/dialog/turn")
async def dialog_turn(req: Request, token: str = Depends(auth)):
    """⑤ 对话迭代: 每轮一次出图 (走 _enqueue/worker, 计入日限). action:
    start=建会话+首图; redo(换一版)=delta 有则 raw+=delta 重翻译, 无则复用 current_en 换 seed;
    vibe(保氛围)=上一张图走 iterate 视觉全量提取(锁主体+氛围)再变体. 显式路由不猜意图, 见 D25."""
    body = await req.json()
    action = (body.get("action") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    delta = (body.get("delta") or "").strip()
    if len(delta) > MAX_DIALOG_DELTA_CHARS:
        raise HTTPException(400, f"改动描述过长(>{MAX_DIALOG_DELTA_CHARS})")
    wf_name = body.get("workflow", "")
    image_filename = None
    denoise = None
    requested_lora_selections = _extract_lora_selections(body)

    if action == "start":
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > MAX_USER_PROMPT_CHARS:
            raise HTTPException(400, f"提示词为空或过长(>{MAX_USER_PROMPT_CHARS})")
        completion_level = _normalize_completion_level(body.get("completion_level"))
        check_banned(prompt)
        try:
            prompt_en, _, _, translate_meta = await translate(
                prompt, lora_selections=requested_lora_selections, include_meta=True,
                completion_level=completion_level)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        check_banned(prompt_en)
        session_id = uuid.uuid4().hex[:10]
        session_bindings = translate_meta.get("lora_bindings", [])
        SESSIONS[session_id] = {
            "id": session_id, "token": token, "raw": prompt,
            "current_en": prompt_en, "created": time.time(), "turns": [],
            "concept": translate_meta.get("concept"),
            "completion_level": completion_level,
            "lora_selections": _bindings_as_selections(session_bindings),
            "lora_bindings": session_bindings,
            "lora_warnings": translate_meta.get("lora_warnings", []),
            "registry_revision": translate_meta.get("registry_revision"),
        }
        raw = prompt
    elif action == "start-image":
        # 从已完成的 job 起步, 不重新生成 -- 原图当第一轮 (前端「继续迭代」直接进暗房)
        src_job_id = (body.get("job_id") or "").strip()
        src_job = JOBS.get(src_job_id)
        if not src_job or src_job["token"] != token:
            raise HTTPException(404, "原图任务不存在")
        if src_job.get("status") != "done" or not src_job.get("image"):
            raise HTTPException(400, "原图还没生成完")
        session_id = uuid.uuid4().hex[:10]
        SESSIONS[session_id] = {
            "id": session_id, "token": token,
            "raw": src_job.get("prompt_raw", ""),
            "current_en": src_job.get("prompt_en", ""),
            "created": time.time(),
            "concept": src_job.get("concept"),
            "completion_level": _normalize_completion_level(
                src_job.get("completion_level")),
            "lora_selections": _bindings_as_selections(src_job.get("lora_bindings")),
            "lora_bindings": src_job.get("lora_bindings", []),
            "lora_warnings": src_job.get("lora_warnings", []),
            "registry_revision": src_job.get("registry_revision"),
            "turns": [{"job_id": src_job_id, "action": "start-image", "delta": "",
                       "prompt_en": src_job.get("prompt_en", ""),
                       "concept": src_job.get("concept"),
                       "completion_level": _normalize_completion_level(
                           src_job.get("completion_level"))}],
        }
        return {"session_id": session_id, "job_id": src_job_id}
    else:
        session = SESSIONS.get(session_id)
        if not session or session["token"] != token:
            raise HTTPException(404, "会话不存在")
        if action == "redo":
            if delta:
                check_banned(delta)
                # 替换意图: 从原 raw 删 char_dict 命中的旧角色名, 避免 char_dict 双命中
                # (redo 累加重翻译时, "换成X"会让旧角色+新角色同时被 char_dict 命中,
                #  LLM 全保留导致新旧角色并存+1boy乱入; 见 D31)
                if any(kw in delta for kw in ("换成", "替换", "改成", "换为", "改为")):
                    for name in _character_names():
                        if name in session["raw"]:
                            session["raw"] = session["raw"].replace(name, "")
                    session["raw"] = re.sub(r"[,，\s]+", " ", session["raw"]).strip()
                session["raw"] = (session["raw"] + "，" + delta) if session["raw"] else delta
                try:
                    prompt_en, _, _, translate_meta = await translate(
                        session["raw"], lora_selections=session.get("lora_selections", []),
                        include_meta=True,
                        completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                    session["concept"] = translate_meta.get("concept")
                    session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                    session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                    session["registry_revision"] = translate_meta.get("registry_revision")
                    session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
            else:
                prompt_en = session["current_en"]
            session["current_en"] = prompt_en
            raw = session["raw"]
        elif action == "vibe":
            if delta:
                check_banned(delta)
            # 图在 JOBS 里 (worker 完成后写 image), turn 记录里没有 -> 从 JOBS 按 job_id 找最新出图
            last_img = next((JOBS.get(t["job_id"], {}).get("image")
                             for t in reversed(session["turns"]) if JOBS.get(t["job_id"], {}).get("image")), None)
            if not last_img:
                raise HTTPException(400, "还没有已生成的图, 无法保氛围")
            try:
                image_b64 = "data:image/png;base64," + base64.b64encode(
                    (IMAGES / last_img.rsplit("/", 1)[-1]).read_bytes()).decode()
            except FileNotFoundError:
                raise HTTPException(400, "上一张图文件不在了, 请重新生成")
            try:
                # ③ reference 路径: char_dict+dict 从 delta 预匹配(认角色名), VL 从图提氛围(vibe-only),
                # 主体由 delta 文字给。不用 iterate(锁主体) -- 用户要"保氛围换主体"=reference, iterate 反而冲突(见 D25 修正)
                prompt_en, _, _, translate_meta = await translate(
                    delta, image_b64=image_b64,
                    lora_selections=session.get("lora_selections", []), include_meta=True,
                    completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                # 视觉路径没有 Composer CONCEPT；不要把上一轮构思错误归因给新 Prompt。
                session["concept"] = translate_meta.get("concept")
                session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                session["registry_revision"] = translate_meta.get("registry_revision")
                session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"视觉理解失败, 请稍后重试 ({e})")
            check_banned(prompt_en)
            session["current_en"] = prompt_en
            raw = f"[保氛围]{(' ' + delta) if delta else ''}"
        elif action == "tweak":
            # img2img 微调 (D26): 上一张图上传 ComfyUI -> anima-img2img 工作流 + 低 denoise
            if delta:
                check_banned(delta)
            last_img = next((JOBS.get(t["job_id"], {}).get("image")
                             for t in reversed(session["turns"]) if JOBS.get(t["job_id"], {}).get("image")), None)
            if not last_img:
                raise HTTPException(400, "还没有已生成的图, 无法微调")
            try:
                image_bytes = (IMAGES / last_img.rsplit("/", 1)[-1]).read_bytes()
                image_filename = await upload_image_to_comfy(image_bytes)
            except FileNotFoundError:
                raise HTTPException(400, "上一张图文件不在了")
            except Exception as e:
                raise HTTPException(502, f"图片上传失败 ({e})")
            # delta -> 翻译 (纯文本, 不走 VL); 无 delta 复用 current_en
            if delta:
                session["raw"] = (session["raw"] + "，" + delta) if session["raw"] else delta
                try:
                    prompt_en, _, _, translate_meta = await translate(
                        session["raw"], lora_selections=session.get("lora_selections", []),
                        include_meta=True,
                        completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                    session["concept"] = translate_meta.get("concept")
                    session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                    session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                    session["registry_revision"] = translate_meta.get("registry_revision")
                    session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(502, f"翻译失败 ({e})")
            else:
                prompt_en = session["current_en"]
            session["current_en"] = prompt_en
            wf_name = "anima"   # 合并版工作流 (img2img 由 image_filename 触发, 见 D32)
            denoise = body.get("denoise", 0.4)
            raw = f"[微调]{(' ' + delta) if delta else ''}"
        else:
            raise HTTPException(400, f"未知 action: {action}")

    check_banned(prompt_en)
    session = SESSIONS[session_id]
    job_id = await _enqueue(token, wf_name, prompt_en, raw,
                            body.get("size"), session.get("lora_selections", []),
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, body.get("detailer"),
                            lora_bindings=session.get("lora_bindings"),
                            registry_revision=session.get("registry_revision"),
                            concept=session.get("concept"),
                            completion_level=session.get(
                                "completion_level", DEFAULT_COMPLETION_LEVEL))
    session["turns"].append({"job_id": job_id, "action": action, "delta": delta,
                             "prompt_en": JOBS[job_id]["prompt_en"],
                             "concept": JOBS[job_id].get("concept"),
                             "completion_level": JOBS[job_id].get("completion_level")})
    return {"session_id": session_id, "job_id": job_id}


@app.get("/api/dialog/{session_id}")
async def dialog_get(session_id: str, token: str = Depends(auth)):
    """返回会话线程: turns 里每轮 join JOBS 拿 status/image (worker 完成后 image 才有值)."""
    session = SESSIONS.get(session_id)
    if not session or session["token"] != token:
        raise HTTPException(404, "会话不存在")
    turns = []
    for t in session["turns"]:
        job = JOBS.get(t["job_id"], {})
        turns.append({
            "action": t["action"], "delta": t["delta"], "prompt_en": t["prompt_en"],
            "concept": t.get("concept"),
            "completion_level": t.get("completion_level"),
            "status": job.get("status", "?"), "image": job.get("image"), "error": job.get("error"),
        })
    return {"session_id": session_id, "raw": session["raw"], "current_en": session["current_en"],
            "concept": session.get("concept"),
            "completion_level": session.get("completion_level", DEFAULT_COMPLETION_LEVEL),
            "lora_bindings": session.get("lora_bindings", []),
            "lora_warnings": session.get("lora_warnings", []),
            "registry_revision": session.get("registry_revision"), "turns": turns}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG.get("host", "127.0.0.1"), port=int(CFG.get("port", 8000)))
