"""LoRA Registry、语义选择、Binding 与前端预览契约。"""
import hashlib
import json
import re
from pathlib import Path

import yaml
from fastapi import HTTPException

from server.settings import CFG, LORA_PREVIEWS, LORA_REGISTRY_PATH


MAX_CHARACTER_LORA_PROFILES = 3

def resolve_lora_preview(asset_key: str, explicit_preview=None) -> str | None:
    """显式 Registry URL 优先；否则按安全 Asset key 查找受控静态缩略图。"""
    if isinstance(explicit_preview, str) and explicit_preview.strip():
        return explicit_preview.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(asset_key)):
        return None
    for suffix in (".webp", ".png", ".jpg", ".jpeg"):
        candidate = LORA_PREVIEWS / f"{asset_key}{suffix}"
        if candidate.is_file():
            return f"/lora-previews/{candidate.name}?v={candidate.stat().st_mtime_ns}"
    return None

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
                # 旧 Registry 可能仍携带 allow_multiple_profiles；D54 起该字段只作兼容，
                # 是否能多选由“存在多个 Profile”这一结构事实决定。
                allow_multiple = selection.get("allow_multiple_profiles")
                if allow_multiple is not None and not isinstance(allow_multiple, bool):
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
            "preview": resolve_lora_preview(key, raw.get("preview")),
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
            "preview": resolve_lora_preview(key, v.get("preview")),
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

def _lora_multi_relation_names(selections: list[dict], registry: dict) -> list[str]:
    """Return stable English subject labels for named multi-character clauses."""
    names: list[str] = []
    for selection in selections:
        asset = registry.get(selection.get("key")) or {}
        if asset.get("type") != "character":
            continue
        profiles = asset.get("profiles") or {}
        for profile_id in _selection_profile_ids(selection):
            profile = profiles.get(profile_id) or {}
            name = ""
            for provided in profile.get("provides") or []:
                value = str(provided).strip()
                match = re.fullmatch(r"character\s+(.+)", value, flags=re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    break
                match = re.fullmatch(r"(.+?)\s+character\s+identity", value,
                                     flags=re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    break
            if not name:
                for provided in profile.get("provides") or []:
                    value = str(provided).strip()
                    match = re.fullmatch(r"(.+?)\s+identity", value, flags=re.IGNORECASE)
                    if match and re.search(r"[a-z]", match.group(1), flags=re.IGNORECASE):
                        name = match.group(1).replace("-", " ").strip()
                        break
            if not name:
                name = next(
                    (str(alias).strip() for alias in profile.get("aliases") or []
                     if re.search(r"[a-z]", str(alias), flags=re.IGNORECASE)),
                    "",
                )
            if not name:
                required = profile.get("required_tags") or []
                if required:
                    name = _lora_tag_key(str(required[0])).split(" (")[0].strip()
            name = re.sub(r"\s+", " ", name).strip()
            if name and name.lower() not in {existing.lower() for existing in names}:
                names.append(name)
    return names

def _lora_tag_key(tag: str) -> str:
    value = tag.lower().replace("\\", "").replace("_", " ").replace("-", " ")
    value = re.sub(r"[()\[\]{}]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.")
    return re.sub(r"^(?:wearing\s+)?(?:a|an|the)\s+", "", value)

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
    # A parenthesized character cluster may contain comma-separated appearance
    # anchors. It is not a bare identity prefix, and flattening it would remove
    # the ownership relation or leave unmatched punctuation after tokenization.
    if re.search(r"(?<!\\)[([{]", segment):
        return segment.strip()
    key = _lora_tag_key(segment)
    for identity in sorted(identity_keys, key=len, reverse=True):
        prefix = identity + " "
        if key.startswith(prefix):
            return key[len(prefix):].strip()
    return segment.strip()

def _split_prompt_commas(text: str) -> list[str]:
    """Split top-level prompt commas while preserving grouped character anchors."""
    parts: list[str] = []
    start = 0
    depth = 0
    escaped = False
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    stack: list[str] = []
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in opens:
            stack.append(char)
            depth += 1
            continue
        if char in pairs and stack and stack[-1] == pairs[char]:
            stack.pop()
            depth -= 1
            continue
        if char == "," and depth == 0:
            part = text[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts

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
    existing = _split_prompt_commas(body)
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
