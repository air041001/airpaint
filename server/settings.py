"""AirPaint 路径、配置与稳定协议常量。"""
from pathlib import Path

import yaml
from fastapi import HTTPException


BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))

DICT_PATH = BASE / "dict.yaml"
CHAR_DICT_PATH = BASE / "char_dict.yaml"
KNOWLEDGE_CACHE_DIR = BASE / "knowledge_cache"
CHAR_AUTO_PATH = KNOWLEDGE_CACHE_DIR / "characters_auto.yaml"
CHAR_LOOKUP_PATH = KNOWLEDGE_CACHE_DIR / "characters_lookup.json"
LORA_REGISTRY_PATH = BASE / "lora_registry.yaml"
LORA_PREVIEWS = BASE / "lora_previews"
# 生产后端不扫描此目录；LoRA onboarding 工具仍用它定位待登记资产。
LORA_DIR = Path(CFG.get("comfy_dir", ".")) / "models" / "loras"

COMFY = CFG["comfy_url"].rstrip("/")
TOKENS = set(CFG.get("tokens", []))
DAILY_LIMIT = int(CFG.get("daily_limit", 30))
BANNED = [word.lower() for word in CFG.get("banned_words", [])]
WORKFLOWS = CFG.get("workflows", {})

MAX_USER_PROMPT_CHARS = 4_000
MAX_CONCEPT_CHARS = 4_000
MAX_PROMPT_EN_CHARS = 6_000
MAX_COMPILED_PROMPT_CHARS = 8_000
MAX_DIALOG_DELTA_CHARS = 2_000
COMPLETION_LEVELS = ("auto", "faithful", "free")
DEFAULT_COMPLETION_LEVEL = "auto"


def normalize_completion_level(value) -> str:
    """把 API/内部调用的补全程度归一化为稳定的三值协议。"""
    if value is None or value == "":
        return DEFAULT_COMPLETION_LEVEL
    if not isinstance(value, str):
        raise HTTPException(400, "completion_level 必须是 auto、faithful 或 free")
    level = value.strip().lower()
    if level not in COMPLETION_LEVELS:
        raise HTTPException(400, "completion_level 必须是 auto、faithful 或 free")
    return level


# 迁移期保留旧私有名称，避免接口语义和历史工具同时变化。
_normalize_completion_level = normalize_completion_level
