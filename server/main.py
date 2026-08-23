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
                default_profile = (asset.get("selection") or {}).get("default_profile")
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

# ---------- LoRA 注册表: config 手动 + 自动扫描 Civitai ----------
# 三层叠加 (优先级高->低): config.yaml 手动配置 > Civitai hash lookup 自动补全 > 裸文件名
# config 里有的条目: 用 config 的 trigger/type/name (人判断最准, 含服装变体)
# config 里没有的 .safetensors: 按 SHA256 查 Civitai 取 trainedWords/modelName/tags
#   有 trainedWords -> 自动可用; 没有 -> 标记 "未配置", 需手动加 config
LORA_DIR = Path(CFG.get("comfy_dir", ".")) / "models" / "loras"
LORA_CACHE_FILE = BASE / "lora_cache.json"
LORA_REGISTRY = HotLoraRegistry(LORA_REGISTRY_PATH)
# {filename_stem: {sha256, trainedWords, modelName, tags, baseModel, type, fetchedAt}}
_lora_auto: dict[str, dict] = {}
_lora_auto_loaded = False


def _load_lora_cache():
    """从磁盘加载自动扫描缓存 (避免每次重启都请求 Civitai)."""
    global _lora_auto, _lora_auto_loaded
    if _lora_auto_loaded:
        return
    try:
        if LORA_CACHE_FILE.exists():
            _lora_auto = json.loads(LORA_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[LoRA] 缓存文件读取失败: {e}", flush=True)
    _lora_auto_loaded = True


def _save_lora_cache():
    """持久化自动扫描缓存到磁盘."""
    try:
        LORA_CACHE_FILE.write_text(json.dumps(_lora_auto, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[LoRA] 缓存文件写入失败: {e}", flush=True)


def _read_sha256(filepath: Path) -> str | None:
    """读 SHA256: 优先 LoraManager .metadata.json -> .sha256 文件 -> 现算 (大文件慢)."""
    stem = filepath.stem
    d = filepath.parent
    meta = d / f"{stem}.metadata.json"
    if meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            sha = (m.get("sha256") or "").strip().lower()
            if sha:
                return sha
        except Exception:
            pass
    sha_file = d / f"{stem}.sha256"
    if sha_file.exists():
        try:
            return sha_file.read_text(encoding="utf-8").strip().lower()
        except Exception:
            pass
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _read_lora_metadata(filepath: Path) -> dict:
    result = {}
    meta = filepath.with_name(f"{filepath.stem}.metadata.json")
    if meta.exists():
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                result.update(raw)
        except Exception:
            pass
    # ComfyUI Manager/Civitai Helper 可能把完整索引写在同名 .civitai.info。
    # 这里只读取结构化字段作为 inventory candidate；HTML description 仍须人工蒸馏，
    # 不允许自动污染正式 Registry。
    civitai_info = filepath.with_name(f"{filepath.stem}.civitai.info")
    if civitai_info.exists():
        try:
            raw = json.loads(civitai_info.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                model = raw.get("model") or {}
                files = raw.get("files") or []
                file_info = next(
                    (item for item in files if isinstance(item, dict)
                     and item.get("name") == filepath.name),
                    files[0] if files and isinstance(files[0], dict) else {},
                )
                hashes = file_info.get("hashes") or {}
                result.setdefault("sha256", str(hashes.get("SHA256") or "").lower())
                result.setdefault("trainedWords", raw.get("trainedWords") or [])
                result.setdefault("model_name", model.get("name") or raw.get("name") or filepath.stem)
                result.setdefault("tags", model.get("tags") or [])
                result.setdefault("baseModel", raw.get("baseModel") or "")
                result["metadata_source"] = "civitai.info"
        except Exception:
            pass
    return result


def _classify_lora_type(tags) -> tuple[str, list[str]]:
    tag_names = [t.get("name", "") if isinstance(t, dict) else str(t) for t in (tags or [])]
    low = {name.strip().lower() for name in tag_names}
    lora_type = "character" if "character" in low else (
        "style" if any(tag in low for tag in ("style", "artist", "artstyle")) else "unknown")
    return lora_type, tag_names


def _lora_fingerprint(filepath: Path) -> str:
    stat = filepath.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


async def _civitai_lookup(sha256: str) -> dict | None:
    """按 SHA256 查 Civitai API, 返回 {trainedWords, modelName, tags, baseModel, type} 或 None."""
    try:
        r = await CLIENT.get(
            f"https://civitai.com/api/v1/model-versions/by-hash/{sha256}", timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        model = d.get("model") or {}
        # Civitai model.tags 是 [{name, count}] 对象数组, 提取 name 列表再判类型
        # (修: 旧代码 "character" in tags 当字符串数组用, 永远 False -> 全 unknown)
        lora_type, tag_names = _classify_lora_type(model.get("tags") or [])
        return {
            "trainedWords": d.get("trainedWords") or [],
            "modelName": model.get("name", ""),
            "tags": tag_names,
            "baseModel": d.get("baseModel", ""),
            "type": lora_type,
        }
    except Exception:
        return None


async def scan_loras(force: bool = False) -> dict:
    """扫描 LoRA inventory；metadata 预过滤非图片文件，失败项允许 refresh 重试。"""
    global _lora_auto
    _load_lora_cache()
    if not LORA_DIR.exists():
        return {"scanned": 0, "new": 0, "failed": 0, "excluded": 0, "total_auto": len(_lora_auto)}
    registry, _ = LORA_REGISTRY.snapshot()
    registry_files = {v.get("file", "") for v in registry.get("loras", {}).values()}
    config_files = {v.get("file", "") for v in CFG.get("loras", {}).values()} | registry_files
    sfs = sorted(LORA_DIR.glob("*.safetensors"))
    new_count = failed = excluded = 0
    image_bases = {"Anima", "NoobAI", "SDXL", "Illustrious", "Unknown", ""}
    for fp in sfs:
        if fp.name in config_files:
            continue
        key = fp.stem
        fingerprint = _lora_fingerprint(fp)
        cached = _lora_auto.get(key) or {}
        cached_status = cached.get("status", "resolved" if cached else "")
        cached_base = str(cached.get("baseModel") or "").strip()
        # refresh 也不能把已经确认是视频底模的巨型文件重新做 SHA256。部分
        # Wan 文件的 sidecar metadata 只写 Unknown，但旧 Civitai 结果已经足够
        # 证明它不属于当前图片工作流；文件指纹变化后仍应先排除，再谈联网刷新。
        known_video_name = key.lower().startswith("wan_") or key.lower().startswith("detailz-wan")
        if ((cached_base and cached_base not in image_bases) or known_video_name):
            _lora_auto[key] = {
                **cached,
                "sha256": cached.get("sha256", ""),
                "trainedWords": cached.get("trainedWords", []),
                "modelName": cached.get("modelName") or key,
                "tags": cached.get("tags", []),
                "baseModel": cached_base or "Wan Video",
                "type": cached.get("type", "unknown"),
                "status": "excluded",
                "fingerprint": fingerprint,
                "fetchedAt": time.time(),
            }
            excluded += 1
            continue
        if (not force and cached.get("fingerprint") == fingerprint
                and cached_status in {"resolved", "excluded"}):
            continue
        metadata = _read_lora_metadata(fp)
        metadata_base = (metadata.get("base_model") or metadata.get("baseModel") or "").strip()
        if metadata_base and metadata_base not in image_bases:
            _lora_auto[key] = {
                "sha256": (metadata.get("sha256") or "").strip().lower(),
                "trainedWords": [], "modelName": metadata.get("model_name") or key,
                "tags": [], "baseModel": metadata_base, "type": "unknown",
                "status": "excluded", "fingerprint": fingerprint,
                "fetchedAt": time.time(),
            }
            excluded += 1
            continue
        metadata_words = metadata.get("trainedWords") or []
        if isinstance(metadata_words, list) and metadata_words:
            metadata_type, metadata_tags = _classify_lora_type(metadata.get("tags") or [])
            status = "resolved" if metadata_type in {"character", "style"} else "incomplete"
            _lora_auto[key] = {
                "sha256": str(metadata.get("sha256") or "").lower(),
                "trainedWords": metadata_words,
                "modelName": metadata.get("model_name") or key,
                "tags": metadata_tags,
                "baseModel": metadata_base,
                "type": metadata_type,
                "status": status,
                "fingerprint": fingerprint,
                "metadataSource": metadata.get("metadata_source", "metadata"),
                "fetchedAt": time.time(),
            }
            new_count += 1
            continue
        sha = _read_sha256(fp)
        if not sha:
            _lora_auto[key] = {
                "sha256": "", "trainedWords": [], "modelName": key, "tags": [],
                "baseModel": metadata_base, "type": "unknown", "status": "failed",
                "fingerprint": fingerprint, "fetchedAt": time.time(),
            }
            failed += 1
            continue
        info = await _civitai_lookup(sha)
        if info is None:
            _lora_auto[key] = {"sha256": sha, "trainedWords": [], "modelName": key,
                                "tags": [], "baseModel": metadata_base, "type": "unknown",
                                "status": "failed", "fingerprint": fingerprint,
                                "fetchedAt": time.time()}
            failed += 1
        elif info.get("baseModel") and info["baseModel"] not in image_bases:
            _lora_auto[key] = {"sha256": sha, "fetchedAt": time.time(),
                               "status": "excluded", "fingerprint": fingerprint, **info}
            excluded += 1
        else:
            status = "resolved" if info.get("type") in {"character", "style"} else "incomplete"
            _lora_auto[key] = {"sha256": sha, "fetchedAt": time.time(),
                               "status": status, "fingerprint": fingerprint, **info}
            new_count += 1
        await asyncio.sleep(0.3)  # 对 Civitai 友好
    # 文件已删除/改名的旧 stem 清理；excluded 保留状态供诊断，但不会进入 registry/API。
    valid_stems = {fp.stem for fp in sfs}
    _lora_auto = {k: v for k, v in _lora_auto.items() if k in valid_stems}
    _save_lora_cache()
    return {"scanned": len(sfs), "new": new_count, "failed": failed,
            "excluded": excluded, "total_auto": len(_lora_auto)}


def get_lora_registry() -> dict[str, dict]:
    """合并 versioned registry > legacy config > 自动 inventory，返回 Asset 级结构。"""
    _load_lora_cache()
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

    # 3. 自动 inventory：unknown/incomplete 也保留，API 放到 other 供 onboarding。
    covered_files = {v.get("file", "") for v in registry.values()}
    for key, info in _lora_auto.items():
        fname = f"{key}.safetensors"
        if (fname in covered_files or info.get("status") == "excluded"
                or (info.get("baseModel") and info.get("baseModel") not in
                    {"Anima", "NoobAI", "SDXL", "Illustrious", "Unknown"})):
            continue
        trained = info.get("trainedWords", [])
        candidate_tags = []
        for group in trained:
            candidate_tags.extend(x.strip() for x in str(group).split(",") if x.strip())
        registry[key] = {
            "key": key,
            "type": info.get("type", "unknown"),
            "name": info.get("modelName", key),
            "file": fname,
            "trigger_policy": "required" if candidate_tags else "none",
            "required_tags": candidate_tags,
            "provides": [],
            "strength_model": 1.0,
            "strength_clip": 1.0,
            "description": "",
            "preview": None,
            "source": "civitai",
            "configured": False,
            "inventory_status": info.get("status", "incomplete"),
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


def normalize_lora_selections(raw, registry: dict[str, dict] | None = None) -> list[dict]:
    """把新 selection、旧 loras 字符串和 legacy key 统一成 Asset/Profile 选择。"""
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        raise HTTPException(400, "lora_selections/loras 必须是数组")
    registry = registry or get_lora_registry()
    aliases = get_lora_legacy_aliases(registry)
    result = []
    seen = set()
    type_counts: dict[str, int] = {}
    for item in raw:
        if isinstance(item, str):
            key, profile, mode, optional = item.strip(), None, "auto", []
        elif isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            profile = str(item.get("profile") or "").strip() or None
            mode = str(item.get("mode") or ("explicit" if profile else "auto")).strip()
            optional = item.get("optional") or []
        else:
            raise HTTPException(400, "LoRA selection 条目格式错误")
        if not key:
            continue
        if key not in aliases:
            raise HTTPException(400, f"未知的 LoRA: {key}")
        asset_key, legacy_profile = aliases[key]
        if legacy_profile:
            profile, mode = legacy_profile, "explicit"
        if mode not in {"explicit", "auto"}:
            raise HTTPException(400, f"LoRA {key} mode 非法")
        if not isinstance(optional, list) or any(not isinstance(x, str) for x in optional):
            raise HTTPException(400, f"LoRA {key} optional 必须是字符串数组")
        asset = registry[asset_key]
        if not asset.get("configured", False):
            raise HTTPException(400, f"LoRA {asset_key} 尚未注册完整")
        identity = (asset_key, profile)
        if identity in seen:
            continue
        seen.add(identity)
        lora_type = asset.get("type", "unknown")
        type_counts[lora_type] = type_counts.get(lora_type, 0) + 1
        if lora_type in {"character", "style"} and type_counts[lora_type] > 1:
            raise HTTPException(400, f"当前只支持同时选择一个 {lora_type} LoRA")
        result.append({"key": asset_key, "profile": profile, "mode": mode,
                       "optional": list(dict.fromkeys(optional))})
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
            result[str(key)] = {
                "profile": str(value.get("profile") or "").strip() or None,
                "optional": [str(x) for x in optional] if isinstance(optional, list) else [],
            }
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
        if not selection.get("profile") and selection.get("mode") == "auto":
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
        profile = profiles.get(selection.get("profile")) or {}
        optional = list(selection.get("optional") or [])
        for oid, option in (profile.get("optional_tags") or {}).items():
            aliases = option.get("aliases") or []
            if any(str(alias).strip().lower() in low for alias in aliases if str(alias).strip()):
                optional.append(oid)
        selection["optional"] = list(dict.fromkeys(optional))
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
    for selection in normalized:
        asset = registry[selection["key"]]
        profile_id = selection.get("profile")
        resolved_by = ("explicit" if profile_id and selection.get("mode") == "explicit"
                       else "intent_alias" if profile_id else selection.get("mode", "auto"))
        optional_ids = list(selection.get("optional") or [])
        tags: list[str] = []
        provides = list(asset.get("provides") or [])
        if asset.get("trigger_policy") == "profile":
            profiles = asset.get("profiles") or {}
            choice = choices.get(selection["key"]) or {}
            if not profile_id and selection.get("mode") == "auto":
                candidate = choice.get("profile")
                if candidate in profiles:
                    profile_id, resolved_by = candidate, "llm"
                elif allow_unresolved_auto:
                    bindings.append({**selection, "type": asset.get("type"), "name": asset.get("name"),
                                     "file": asset.get("file"), "resolved_by": "pending",
                                     "injected_tags": [], "provides": []})
                    continue
                else:
                    default = (asset.get("selection") or {}).get("default_profile")
                    if default in profiles:
                        profile_id, resolved_by = default, "default"
                        warnings.append(f"{asset['name']} 未匹配到明确 Profile，已使用默认 {profiles[default]['name']}")
                    else:
                        raise HTTPException(400, f"LoRA {selection['key']} 需要明确选择 Profile")
            elif not profile_id:
                default = (asset.get("selection") or {}).get("default_profile")
                if default in profiles:
                    profile_id, resolved_by = default, "default"
                elif len(profiles) == 1:
                    profile_id, resolved_by = next(iter(profiles)), "single"
                else:
                    raise HTTPException(400, f"LoRA {selection['key']} 需要明确选择 Profile")
            if profile_id not in profiles:
                raise HTTPException(400, f"LoRA {selection['key']} 不存在 Profile: {profile_id}")
            profile = profiles[profile_id]
            provides = list(profile.get("provides") or [])
            tags.extend(profile.get("required_tags") or [])
            tags.extend(profile.get("default_tags") or [])
            # Profile 是否自动只影响“选哪个 Profile”；已经锁定 Profile 后，LLM 仍可
            # 根据用户意图挑选该 Profile 白名单里的 optional concept ID。
            optional_ids.extend(choices.get(selection["key"], {}).get("optional") or [])
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
            optional_ids = valid_optional
        else:
            if profile_id:
                raise HTTPException(400, f"LoRA {selection['key']} 不支持 Profile")
            tags.extend(asset.get("required_tags") or [])
            optional_ids = []
            resolved_by = "explicit"
        tags = list(dict.fromkeys(t.strip() for t in tags if isinstance(t, str) and t.strip()))
        bindings.append({
            "key": selection["key"], "type": asset.get("type", "unknown"),
            "name": asset.get("name", selection["key"]), "file": asset.get("file"),
            "profile": profile_id, "optional": optional_ids, "resolved_by": resolved_by,
            "injected_tags": tags, "provides": list(dict.fromkeys(provides)),
            "strength_model": asset.get("strength_model", 1.0),
            "strength_clip": asset.get("strength_clip", 1.0),
        })
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
        contract[selection["key"]] = {
            "profile": (binding.get("profile") or "<choose one allowed profile ID>"
                        if asset.get("trigger_policy") == "profile" else None),
            "optional": [],
        }
        lines.append(f"- LoRA {selection['key']}: {asset['name']} ({asset.get('type', 'unknown')})")
        if asset.get("trigger_policy") == "none":
            lines.append("  Active through weights; no trigger words are needed.")
        elif binding.get("profile"):
            profile = asset["profiles"][binding["profile"]]
            lines.append(f"  Locked profile: {binding['profile']} / {profile['name']}")
            lines.append(f"  Already provides: {', '.join(profile.get('provides') or ['the selected concept'])}")
            optional = profile.get("optional_tags") or {}
            if optional:
                choices = "; ".join(
                    f"{oid}={', '.join(opt.get('provides') or [opt.get('name', oid)])}"
                    for oid, opt in optional.items())
                lines.append(f"  Optional IDs (only if explicitly requested): {choices}")
        elif asset.get("trigger_policy") == "profile":
            lines.append("  Select exactly one profile ID from:")
            for pid, profile in asset.get("profiles", {}).items():
                lines.append(f"    {pid}: {profile['name']}; provides {', '.join(profile.get('provides') or [])}")
        else:
            lines.append(f"  Already provides: {', '.join(asset.get('provides') or ['the selected concept'])}")
    lines.extend([
        "Do not copy, invent, or rewrite LoRA trigger strings, filenames, or weights in PROMPT.",
        "Do not invent a conflicting character identity, outfit, appearance, or style.",
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
        profile_ids = [selection["profile"]] if selection.get("profile") else list(profiles)
        for profile_id in profile_ids:
            profile = profiles.get(profile_id) or {}
            aliases.update(str(x).strip().lower() for x in profile.get("aliases", []) if str(x).strip())
            if profile.get("name"):
                aliases.add(str(profile["name"]).strip().lower())
    return aliases


def _lora_tag_key(tag: str) -> str:
    value = tag.lower().replace("\\", "").replace("_", " ")
    value = re.sub(r"[()\[\]{}]", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ,.")


def compile_lora_bindings(prompt_en: str, bindings: list[dict] | None) -> str:
    """把 registry exact tags 幂等合入最终 Prompt；不从 LLM 字符串反推 Profile。"""
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
    existing = [t for t in existing if _lora_tag_key(t) not in lora_keys]
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

# Phase 2.6 production protocol. Keep the legacy prompt above as a parser-compatible
# fallback reference, but make the final painter prompt a first-class output instead
# of asking the model to satisfy the old TAGS/NL split and painter expansion at once.
PAINTER_SYSTEM_PROMPT = (
    "You are a professional painter-style prompt planner for the base Anima anime image model. "
    "You receive known canonical tags and the remaining Chinese user idea. Preserve the known tags in the final prompt through the backend; "
    "do not repeat known character or attribute tags in your PROMPT line.\n\n"
    "Normally output EXACTLY these two lines, nothing else (no markdown, no explanation):\n"
    "IR: <one-line valid compact JSON object with exactly these 12 array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints>\n"
    "PROMPT: <one compact comma-separated positive prompt in lowercase English>\n"
    "If ACTIVE LORA CONTEXT is present, insert one LORA JSON line between IR and PROMPT. "
    "Use only the supplied LoRA key/profile/optional IDs. Echo a locked explicit profile unchanged; "
    "for auto mode select the best allowed profile. Never put trigger strings, filenames, or weights in LORA or PROMPT.\n\n"
    "Build the prompt in five layers, in this order:\n"
    "1. Lock the explicit subject, count, named character, core object, action and location.\n"
    "2. Add only coherent appearance, clothing, pose and body-language details that support the explicit idea.\n"
    "3. Add a concrete scene anchor and at most one or two conservative supporting props.\n"
    "4. Choose one readable camera/framing/composition; do not add competing camera directions.\n"
    "5. Add restrained lighting, mood, material and anime line/shading details.\n\n"
    "Hard rules:\n"
    "- Use Danbooru-like lowercase tags plus short drawable English clauses. Keep PROMPT to about 20 concrete comma-separated elements or fewer.\n"
    "- Preserve every explicit constraint. Never add an unrelated character, weapon, named IP, towel, accessory or new main action.\n"
    "- If a person is explicit or naturally implied, make the person readable in the composition. Do not let scenery, black shadow, silhouette or a tiny distant figure replace the person unless the user explicitly asks for that.\n"
    "- If the input names a girl or woman, do not invent a school uniform, see-through clothing or a different hairstyle. If a named character has known canonical tags, keep them unchanged. For an unknown named character, put the best candidate tag in IR.subject so the backend can verify it.\n"
    "- For mood-only input, choose one concrete setting and one clear focal anchor that actually communicates the mood. A bare empty street or vague atmosphere is not enough; if a person is added, do not make them tiny or hide them in black space.\n"
    "- For explicit NSFW input, keep the human body and readable pose present. Prefer a medium/full-body or three-quarter composition over a default close-up unless the user asks for a close-up. Improve quality with gaze, facial expression, body language, fabric/skin material and reveal pacing. Do not add age labels, extra people or a new sex act.\n"
    "- Active LoRA capabilities are already supplied by weights and the backend binding. Plan around them; do not invent a conflicting identity, outfit, appearance, or style, and do not repeat provided LoRA details in PROMPT.\n"
    "- Do not output quality/score tags, negative tags, text/watermark, realistic/photorealistic/3d/render terms, or a TAGS/NL section.\n"
)

# LLM 结构化输出的字段 (顺序即展示顺序). TAGS 行单独解析为最终 tag.
_STRUCTURED_FIELDS = ("scene", "composition", "mood", "lighting", "style")
_IR_FIELDS = (
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
)


def _prompt_ir_meta(mode: str, reroll: bool = False, prompt_ir: dict | None = None,
                    char_tags: list[str] | None = None,
                    attribute_tags: list[str] | None = None,
                    character_lookup: list[dict] | None = None) -> dict:
    """为 API 增加来源/补全元数据，不污染 12 字段 Prompt IR 结构。"""
    expansion = mode == "painter_expansion"
    return {
        "mode": mode,
        "source": {
            "user_intent": "remaining_input",
            "character_tags": "dictionary" if char_tags else None,
            "attribute_tags": "dictionary" if attribute_tags else None,
            "default_completion": "painter" if expansion else None,
        },
        "expansion_applied": expansion,
        "reroll": bool(reroll),
        "reroll_strategy": "new_painter_plan" if expansion and reroll else None,
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
    "4. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
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
    "4. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
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


async def siliconflow_translate(context: str, reroll: bool = False) -> tuple[str, dict | None, str, dict | None, list[dict], dict]:
    """走硅基流动 Qwen 翻译/扩写. context 是结构化上下文 (Known tags + Remaining).
    返回 (LLM 新增 tag, 结构化拆解 dict). tag 不含已知 tag (由 translate 拼接).
    breakdown 供前端预览展示 AI 理解, prompt_ir 保存 12 字段语义计划. 失败抛异常 (上层转 HTTPException).
    reroll=True: 提高温度 + 前置发散指令, 让模型给一版不同创意解读 (抽卡再抽, 见 D19)."""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")

    # thinking 默认关 (D2: 思考慢 30s+ 且易复读); 结构化字段已是强制表态机制, 不依赖 CoT.
    # 隐喻/场景仍弱时 config 翻 translate_enable_thinking: true 重测, 不动代码 (见 D18).
    thinking = bool(CFG.get("translate_enable_thinking", False))

    # reroll: 高温 + 发散指令. /no_think 仍是 user 首token (thinking 开则不前置), nudge 跟在后面.
    temperature = float(CFG.get("reroll_temperature", 0.9)) if reroll else 0.4
    nudge = ("Generate a DIFFERENT painter completion plan from the previous one. "
             "Vary the composition, lighting, mood or concrete setting only when coherent; "
             "keep every explicit subject, action, location and constraint, and preserve subject readability. "
             "Still follow the output format and the known-tags rule.\n\n") if reroll else ""
    user_content = ("/no_think " if not thinking else "") + nudge + context
    active_lora = "ACTIVE LORA CONTEXT" in context

    out = ""
    tags, breakdown, nl, prompt_ir = "", None, "", None
    lora_choices = {}
    for attempt in range(2):
        repair = ""
        if attempt:
            repair = (
                "\nFORMAT REPAIR: Return one compact valid IR JSON line with all 12 array fields, "
                + ("then the mandatory LORA JSON line using only the supplied IDs, " if active_lora else "")
                + "then PROMPT. Do not omit any required line.\n"
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
                "max_tokens": 650,
                # ★ 关键: enable_thinking 必须放顶层, 放 extra_body 里硅基流动不认 -> 思考没关掉. (见 D2)
                "enable_thinking": thinking,
            },
            timeout=40,
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

        # 解析结构化输出: IR + PROMPT. 旧协议仍由解析器兼容.
        tags, breakdown, nl, prompt_ir = _parse_structured_output(out)
        lora_choices = _parse_lora_choices(out)
        if (prompt_ir is not None and (not active_lora or lora_choices)) or attempt == 1:
            break

    # 兜底: 检测重复 tag (模型复读), 出现3次以上相同 tag 说明输出异常
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"翻译输出异常(重复tag: {dupes[0]}), 请重试")

    return tags, breakdown, nl, prompt_ir, _parse_character_hints(out), lora_choices


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

    quality prefix、safety、LoRA trigger 和 workflow 注入仍由 build_prompt 负责。
    """
    cleaned_tags = [tag.strip() for tag in other_tags if tag and tag.strip()]
    cleaned_tags = _strip_char_bare_names(cleaned_tags, char_tags)
    result = normalize_tag_order(char_tags, cleaned_tags)
    nl = (nl or "").strip() if profile == "relation_hybrid" else ""
    if result and nl:
        return result + ". " + nl
    return result or nl


async def translate(text: str, reroll: bool = False, image_b64: str | None = None,
                    lora_selections=None, include_meta: bool = False) -> tuple:
    """中文 -> danbooru tag. 三层: 角色匹配 -> 词典匹配 -> LLM 扩写(只处理未命中).
    返回 (prompt_en, breakdown, prompt_ir): breakdown 是既有 5 维展示结构,
    prompt_ir 是 12 字段语义计划; 快速路径或旧视觉协议时二者按实际情况为 None.
    include_meta=True 时追加第四项 prompt_ir_meta，供 API additive 返回，不影响旧内部调用。
    reroll=True: 只对 LLM 路径生效, 高温重出一版不同补全方案, 跳过缓存(探索性, 不污染正常缓存).
    image_b64: ③ 参考图 (data URI 或 base64). 有图走视觉 LLM 提氛围.
    lora_selections: Active LoRA Asset/Profile；存在时所有路径都使用同一 binding context."""
    backend = CFG.get("translate", "none")
    lora_selections = apply_lora_intent_hints(text, lora_selections)
    lora_context, normalized_loras, lora_revision = build_lora_context(lora_selections)
    has_lora = bool(normalized_loras)

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
    char_tags, remaining = match_characters(text)

    # Layer 1: 词典子串匹配 (最长优先, 像 char_dict 一样在文本里找, 不靠逗号精确匹配)
    # 修: 原精确匹配逗号段, NSFW 词嵌在短语里("裸足少女")不命中 -> 走 LLM -> Qwen3 安全过滤丢词 (见 D26)
    hits, remaining = match_dict_words(remaining)
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
            _prompt_ir_meta("vision_reference", reroll, prompt_ir, char_tags, hits),
            bindings, lora_warnings,
        )

    # 全命中 (无 misses): 不调 LLM
    if not misses and not has_lora:
        # 裸角色名快速路径: 只有角色没别的描述 -> 补 1girl, solo
        # (LLM 对裸角色名会疯狂编场景/武器, 实测 7.9s + 噪声 tag, 见 D13)
        if char_tags and not hits:
            result = compile_prompt(char_tags, ["1girl", "solo"], profile="tag_first")
            return finish(result, None, None,
                          _prompt_ir_meta("canonical", reroll, char_tags=char_tags))
        all_tags = char_tags + hits
        if all_tags:
            result = compile_prompt(char_tags, hits, profile="tag_first")
            return finish(result, None, None,
                          _prompt_ir_meta("dictionary", reroll,
                                          char_tags=char_tags, attribute_tags=hits))
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
                                      char_tags=char_tags, attribute_tags=hits),
                      bindings, lora_warnings)

    if backend == "siliconflow":
        # 构造上下文: 已知 tag 喂给 LLM, 只让它翻/扩 misses (不重复已知 tag)
        ctx_lines = []
        if char_tags:
            ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
        if hits:
            ctx_lines.append(f"Known attribute tags: {', '.join(hits)}")
        ctx_lines.append(f"Original user intent: {text or '(image-only)'}")
        ctx_lines.append(f"Remaining: {', '.join(misses) if misses else '(all ordinary terms already resolved; still plan around Active LoRA)'}")
        if lora_context:
            ctx_lines.append(lora_context)
            ctx_lines.append(f"Registry revision: {lora_revision}")
        context = "\n".join(ctx_lines)

        cache_key = context
        if not reroll and cache_key in _TRANSLATE_CACHE:
            cached = _TRANSLATE_CACHE[cache_key]
            cached_result, cached_breakdown, cached_ir = cached[:3]
            cached_bindings = cached[3] if len(cached) > 3 else []
            cached_warnings = cached[4] if len(cached) > 4 else []
            return finish(
                cached_result, cached_breakdown, cached_ir,
                _prompt_ir_meta("painter_expansion", reroll, cached_ir, char_tags, hits),
                cached_bindings, cached_warnings,
            )
        try:
            translated = await siliconflow_translate(context, reroll=reroll)
            new_tags, breakdown, nl, prompt_ir, character_hints = translated[:5]
            lora_choices = translated[5] if len(translated) > 5 else {}
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
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
        # 编译: 已知/自动确认角色 tag + LLM Prompt, 再按 Anima 规范序排。
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        painter_tags = _prepare_painter_tags(hits + new_list, prompt_ir, text, resolved_char_tags)
        result = compile_prompt(resolved_char_tags, painter_tags, nl, infer_render_profile(prompt_ir))
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras, lora_choices)
        result = compile_lora_bindings(result, bindings)
        # reroll 不写缓存: 探索性结果不应顶掉正常翻译的缓存原版 (见 D19)
        if not reroll:
            if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
                _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
            _TRANSLATE_CACHE[cache_key] = (result, breakdown, prompt_ir, bindings, lora_warnings)
        return finish(
            result, breakdown, prompt_ir,
            _prompt_ir_meta("painter_expansion", reroll, prompt_ir,
                            resolved_char_tags, hits, lookup_results),
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
                                      char_tags=char_tags, attribute_tags=hits),
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
        selections = [
            {"key": b.get("key"), "profile": b.get("profile"), "mode": "explicit",
             "optional": b.get("optional") or []}
            for b in lora_bindings
        ]
        effective_bindings, _, _ = resolve_lora_selections(
            selections, expected_revision=registry_revision)
    elif lora_keys:
        effective_bindings, _, registry_revision = resolve_lora_selections(lora_keys)
    if effective_bindings:
        if "lora_node" not in wcfg:
            raise HTTPException(400, f"工作流 {wf_name} 不支持 LoRA")
        lora_entries = []
        for binding in effective_bindings:
            # 按类型取强度: 角色/风格各自独立, 未传则用 config 默认
            t = binding["type"]
            sv = strength_char if t == "character" else (strength_style if t == "style" else None)
            sm = float(sv) if sv is not None else binding["strength_model"]
            sc = float(sv) if sv is not None else binding["strength_clip"]
            lora_entries.append({"name": binding["file"], "strength": sm,
                                 "clipStrength": sc, "active": True})
        set_input("lora_node", "loras", {"__value__": lora_entries})
        prompt_en = compile_lora_bindings(prompt_en, effective_bindings)

    # safety 标签: Anima 要求明确 safe/sensitive/nsfw/explicit. 检测 prompt_en 里的 NSFW 关键词.
    _NSFW_KW = {"nipples", "pussy", "penis", "sex", "nude", "naked", "cum", "anus", "areola",
                "breasts out", "panty pull", "explicit", "questionable"}
    safety = "explicit, " if any(kw in prompt_en.lower() for kw in _NSFW_KW) else "safe, "

    full_prompt = wcfg.get("quality_prefix", "") + safety + prompt_en
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
    # img2img 注入: 切 ImpactSwitch select=2 (VAEEncode latent) + 覆盖主 KSampler denoise
    if image_filename and "image_node" in wcfg:
        if "switch_node" in wcfg:
            set_input("switch_node", "select", 2)
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


def generation_timeout_seconds(width: int | None, height: int | None) -> int:
    """按像素面积放宽高分辨率任务超时，标准约 1MP 仍沿用配置值。"""
    base_timeout = max(1, int(CFG.get("timeout_seconds", 300)))
    if not width or not height:
        return base_timeout
    scale = max(1.0, (int(width) * int(height)) / (1024 * 1024))
    return min(900, max(base_timeout, round(base_timeout * scale)))


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

    deadline = time.time() + generation_timeout_seconds(width, height)
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
    asyncio.create_task(scan_loras())  # 后台扫 LoRA, 不阻塞启动


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
    return {"characters": [i for i in items if i["type"] == "character"],
            "styles": [i for i in items if i["type"] == "style"],
            "other": [i for i in items if i["type"] not in {"character", "style"}]}


@app.post("/api/loras/refresh")
async def refresh_loras(token: str = Depends(auth)):
    """强制重试 failed/incomplete inventory，并重新按 metadata/Civitai 分类。"""
    result = await scan_loras(force=True)
    return {"ok": True, **result}


def _extract_lora_selections(body: dict):
    if "lora_selections" in body:
        return body.get("lora_selections") or []
    loras = body.get("loras")
    if loras is not None:
        return loras
    single = body.get("lora")
    return [single] if single else []


def _bindings_as_selections(bindings: list[dict] | None) -> list[dict]:
    return [
        {"key": b.get("key"), "profile": b.get("profile"), "mode": "explicit",
         "optional": b.get("optional") or []}
        for b in (bindings or [])
    ]


@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只翻译不排队: 中文 -> 英文 tag (角色->词典->LLM 三层 + 结构化扩写, LRU 缓存). 不计入 image 限额.
    返回 {prompt_en, breakdown, prompt_ir, prompt_ir_meta}: breakdown 保持 5 维前端展示形状,
    prompt_ir 是 12 字段语义计划; prompt_ir_meta additive 标注来源、补全模式和 reroll 方案.
    body.reroll=true: LLM 高温重出一版不同画师补全方案 (抽卡再抽, 跳过缓存, 见 D19).
    body.image: 可选, 参考图 base64 (data URI). 有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23)."""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    image = (body.get("image") or "").strip()
    if not prompt and not image:
        raise HTTPException(400, "提示词和参考图不能同时为空")
    if len(prompt) > 500:
        raise HTTPException(400, "提示词过长(>500)")
    if len(image) > 5_000_000:
        raise HTTPException(400, "参考图过大(>5MB)")
    if prompt:
        check_banned(prompt)
    reroll = bool(body.get("reroll"))
    prompt_en, breakdown, prompt_ir, prompt_ir_meta = await translate(
        prompt, reroll=reroll, image_b64=(image or None),
        lora_selections=_extract_lora_selections(body), include_meta=True
    )
    check_banned(prompt_en)
    return {
        "prompt_en": prompt_en,
        "breakdown": breakdown,
        "prompt_ir": prompt_ir,
        "prompt_ir_meta": prompt_ir_meta,
        "lora_bindings": prompt_ir_meta.get("lora_bindings", []),
        "lora_warnings": prompt_ir_meta.get("lora_warnings", []),
        "registry_revision": prompt_ir_meta.get("registry_revision"),
    }


async def _enqueue(token: str, wf_name: str, prompt_en: str, prompt_raw: str,
                   size, lora_selections, strength_char, strength_style,
                   image_filename: str | None = None, denoise: float | None = None,
                   detailer: dict | None = None,
                   lora_bindings: list[dict] | None = None,
                   registry_revision: str | None = None) -> str:
    """校验并入队一次出图 (USAGE+1 / JOBS / QUEUE.put). create_job 与 /api/dialog/turn 共用. 返回 job_id.
    prompt_en/prompt_raw 的 banned 检查由调用方负责 (两处逻辑不同)."""
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
        binding_requests = [
            {"key": b.get("key"), "profile": b.get("profile"), "mode": "explicit",
             "optional": b.get("optional") or []}
            for b in lora_bindings if isinstance(b, dict)
        ]
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
    if len(prompt_en) > 1200:
        raise HTTPException(400, "编译后的提示词过长(>1200)")
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
    if not prompt_en or len(prompt_en) > 800:
        raise HTTPException(400, "提示词为空或过长(>800)")
    if prompt_raw != prompt_en and len(prompt_raw) > 500:
        raise HTTPException(400, "原始提示词过长(>500)")
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
                            registry_revision=registry_revision)
    job = JOBS[job_id]
    return {"id": job_id, "prompt_en": job["prompt_en"],
            "lora_bindings": job.get("lora_bindings", []),
            "lora_warnings": job.get("lora_warnings", []),
            "registry_revision": job.get("registry_revision")}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, token: str = Depends(auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    queued_ids = list(QUEUE._queue)  # MVP 简单读取
    resp = {k: job[k] for k in (
        "id", "status", "prompt_raw", "prompt_en", "workflow",
        "lora_bindings", "lora_warnings", "registry_revision") if k in job}
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
    if len(delta) > 300:
        raise HTTPException(400, "改动描述过长(>300)")
    wf_name = body.get("workflow", "")
    image_filename = None
    denoise = None
    requested_lora_selections = _extract_lora_selections(body)

    if action == "start":
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > 500:
            raise HTTPException(400, "提示词为空或过长(>500)")
        check_banned(prompt)
        try:
            prompt_en, _, _, translate_meta = await translate(
                prompt, lora_selections=requested_lora_selections, include_meta=True)
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
            "lora_selections": _bindings_as_selections(src_job.get("lora_bindings")),
            "lora_bindings": src_job.get("lora_bindings", []),
            "lora_warnings": src_job.get("lora_warnings", []),
            "registry_revision": src_job.get("registry_revision"),
            "turns": [{"job_id": src_job_id, "action": "start-image", "delta": "",
                       "prompt_en": src_job.get("prompt_en", "")}],
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
                        include_meta=True)
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
                    lora_selections=session.get("lora_selections", []), include_meta=True)
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
                        include_meta=True)
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
                            registry_revision=session.get("registry_revision"))
    session["turns"].append({"job_id": job_id, "action": action, "delta": delta,
                             "prompt_en": JOBS[job_id]["prompt_en"]})
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
            "status": job.get("status", "?"), "image": job.get("image"), "error": job.get("error"),
        })
    return {"session_id": session_id, "raw": session["raw"], "current_en": session["current_en"],
            "lora_bindings": session.get("lora_bindings", []),
            "lora_warnings": session.get("lora_warnings", []),
            "registry_revision": session.get("registry_revision"), "turns": turns}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG.get("host", "127.0.0.1"), port=int(CFG.get("port", 8000)))
