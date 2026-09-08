"""AirPaint 路径、配置与稳定协议常量。"""
from pathlib import Path

import yaml
from fastapi import HTTPException


BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.yaml"
if not CONFIG_PATH.is_file():
    raise RuntimeError(
        f"缺少配置文件 {CONFIG_PATH}；请复制 server/config.example.yaml 并填写本机值"
    )
try:
    CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
except yaml.YAMLError as exc:
    raise RuntimeError(f"配置文件 YAML 无法解析: {CONFIG_PATH}: {exc}") from exc
if not isinstance(CFG, dict):
    raise RuntimeError(f"配置文件必须是 YAML 对象: {CONFIG_PATH}")

DICT_PATH = BASE / "dict.yaml"
CHAR_DICT_PATH = BASE / "char_dict.yaml"
KNOWLEDGE_CACHE_DIR = BASE / "knowledge_cache"
CHAR_AUTO_PATH = KNOWLEDGE_CACHE_DIR / "characters_auto.yaml"
CHAR_LOOKUP_PATH = KNOWLEDGE_CACHE_DIR / "characters_lookup.json"
LORA_REGISTRY_PATH = BASE / "lora_registry.yaml"
LORA_PREVIEWS = BASE / "lora_previews"
STATE_DIR = BASE / "state"
DATABASE_PATH = STATE_DIR / "airpaint.db"
IDENTITY_KEY_PATH = STATE_DIR / "identity.key"
SOURCE_IMAGES = STATE_DIR / "source_images"
# 生产后端不扫描此目录；LoRA onboarding 工具仍用它定位待登记资产。
LORA_DIR = Path(CFG.get("comfy_dir", ".")) / "models" / "loras"

COMFY = str(CFG.get("comfy_url") or "").rstrip("/")
TOKENS = set(CFG.get("tokens", []))
try:
    DAILY_LIMIT = int(CFG.get("daily_limit", 90))
except (TypeError, ValueError) as exc:
    raise RuntimeError("daily_limit 必须是正整数") from exc
if DAILY_LIMIT < 1:
    raise RuntimeError("daily_limit 必须是正整数")
BANNED = [word.lower() for word in CFG.get("banned_words", [])]
WORKFLOWS = CFG.get("workflows", {})


def validate_local_configuration() -> None:
    missing: list[str] = []
    if not CFG.get("comfy_url"):
        missing.append("comfy_url")
    if not isinstance(WORKFLOWS, dict) or not WORKFLOWS:
        missing.append("workflows")
    else:
        for name, workflow in WORKFLOWS.items():
            if not isinstance(workflow, dict) or not workflow.get("file"):
                missing.append(f"workflows.{name}.file")
                continue
            path = BASE / str(workflow["file"])
            if not path.is_file():
                missing.append(f"工作流文件 {path}")
    if not LORA_REGISTRY_PATH.is_file():
        missing.append(f"LoRA Registry {LORA_REGISTRY_PATH}")
    if missing:
        raise RuntimeError("AirPaint 启动资源缺失: " + "；".join(missing))


validate_local_configuration()

MAX_USER_PROMPT_CHARS = 4_000
MAX_CONCEPT_CHARS = 4_000
MAX_PROMPT_EN_CHARS = 6_000
MAX_COMPILED_PROMPT_CHARS = 8_000
MAX_DIALOG_DELTA_CHARS = 2_000
COMPLETION_LEVELS = ("auto", "faithful", "free")
DEFAULT_COMPLETION_LEVEL = "auto"
REFERENCE_SCOPES = ("composition_vibe", "composition", "full")
DEFAULT_REFERENCE_SCOPE = "composition_vibe"
IMAGE_FIT_MODES = ("preserve", "crop")
CROP_POSITIONS = ("center", "top", "bottom", "left", "right")
DEFAULT_IMAGE_FIT_MODE = "preserve"
DEFAULT_CROP_POSITION = "center"
MIN_DENOISE = 0.1
MAX_DENOISE = 0.9
DEFAULT_DENOISE = 0.35
MAX_REFERENCE_IMAGE_CHARS = 5_000_000
MAX_SOURCE_IMAGE_CHARS = 12_000_000


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


def normalize_reference_scope(value) -> str:
    """把参考图范围归一化为稳定的三值协议。"""
    if value is None or value == "":
        return DEFAULT_REFERENCE_SCOPE
    if not isinstance(value, str):
        raise HTTPException(400, "reference_scope 必须是 composition_vibe、composition 或 full")
    scope = value.strip().lower()
    if scope not in REFERENCE_SCOPES:
        raise HTTPException(400, "reference_scope 必须是 composition_vibe、composition 或 full")
    return scope


def normalize_image_fit(value, crop_position=None) -> tuple[str, str]:
    """校验 Img2Img 画幅适配方式；preserve 固定使用居中扩边。"""
    mode = DEFAULT_IMAGE_FIT_MODE if value in (None, "") else value
    if not isinstance(mode, str) or mode.strip().lower() not in IMAGE_FIT_MODES:
        raise HTTPException(400, "fit_mode 必须是 preserve 或 crop")
    mode = mode.strip().lower()
    position = DEFAULT_CROP_POSITION if crop_position in (None, "") else crop_position
    if not isinstance(position, str) or position.strip().lower() not in CROP_POSITIONS:
        raise HTTPException(400, "crop_position 必须是 center、top、bottom、left 或 right")
    position = position.strip().lower()
    return mode, position if mode == "crop" else DEFAULT_CROP_POSITION


def normalize_denoise(value, *, required: bool = False) -> float | None:
    """统一 Img2Img 重绘强度边界。"""
    if value in (None, ""):
        if required:
            return DEFAULT_DENOISE
        return None
    try:
        denoise = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "denoise 必须是数字")
    if not MIN_DENOISE <= denoise <= MAX_DENOISE:
        raise HTTPException(400, f"denoise 必须在 {MIN_DENOISE}~{MAX_DENOISE} 之间")
    return denoise


# 迁移期保留旧私有名称，避免接口语义和历史工具同时变化。
_normalize_completion_level = normalize_completion_level
