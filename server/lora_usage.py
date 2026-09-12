"""LoRA 用法资料的纯函数规范层（无 I/O、无 runtime 依赖）。

P2A 的「正文 hash」「不可变版本ID」「记录核验」「Registry 引用解析」只允许有一处实现：
``server.persistence``（写入/读取）与 ``server.lora``（关联解析）都调用本模块，
既避免两模块互相 import 形成循环依赖，也避免两套算法漂移。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


USAGE_SOURCE_KINDS = ("author", "community", "user", "inferred")
USAGE_VERIFIED_STATUSES = ("unverified", "source_confirmed", "image_verified")

HASH_MISMATCH = "hash_mismatch"


def body_hash(body: Any) -> str:
    """正文的 sha256（UTF-8 字节）；正文是版本身份的一部分，不由调用方提供。"""
    return hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()


def normalize_record(record: dict) -> dict:
    """规范化外部输入，并由服务端从正文生成 ``body_hash``。

    传入的 ``body_hash`` 只被当作一致性声明：与正文不符则拒绝（不写入、不静默修正），
    省略或留空则由服务端生成。
    """
    if not isinstance(record, dict):
        raise ValueError("lora usage 记录必须是对象")
    asset_key = str(record.get("asset_key") or "").strip()
    if not asset_key:
        raise ValueError("lora usage 需要 asset_key")
    body = str(record.get("body") or "")
    expected = body_hash(body)
    declared = str(record.get("body_hash") or "").strip()
    if declared and declared != expected:
        raise ValueError("body_hash 与正文不符；请省略该字段，由服务端从正文生成")
    return {
        "asset_key": asset_key,
        "profile_id": str(record.get("profile_id") or "").strip(),
        "body": body,
        "body_hash": expected,
        "source_kind": str(record.get("source_kind") or "inferred"),
        "source_url": str(record.get("source_url") or ""),
        "background": record.get("background") or {},
        "candidate": record.get("candidate") or {},
        "advisory": record.get("advisory") or {},
        "verified": str(record.get("verified") or "unverified"),
    }


def _versioned_payload(record: dict) -> dict:
    return {
        "asset_key": str(record.get("asset_key") or "").strip(),
        "profile_id": str(record.get("profile_id") or "").strip(),
        "body": str(record.get("body") or ""),
        "body_hash": str(record.get("body_hash") or ""),
        "source_kind": str(record.get("source_kind") or "inferred"),
        "source_url": str(record.get("source_url") or ""),
        "background": record.get("background") or {},
        "candidate": record.get("candidate") or {},
        "advisory": record.get("advisory") or {},
        "verified": str(record.get("verified") or "unverified"),
    }


def version_id(record: dict) -> str:
    """版本ID = 规范化**完整**记录的 sha256（含正文与正文 hash）。

    覆盖参与应用与来源解释的全部字段，因此更新正文或任一结构化内容都会得到新版本号。
    """
    canonical = json.dumps(_versioned_payload(record), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_record(record: dict | None) -> str | None:
    """核验一条已读取的记录：正文 hash 与完整版本ID 是否自洽。

    返回 ``None`` 表示完整；否则返回损坏原因（``hash_mismatch``）。
    被改写正文、只改正文 hash、或伪造版本号的记录都不会通过。
    """
    if not isinstance(record, dict):
        return HASH_MISMATCH
    body = str(record.get("body") or "")
    if str(record.get("body_hash") or "") != body_hash(body):
        return HASH_MISMATCH
    if str(record.get("usage_id") or "") != version_id(record):
        return HASH_MISMATCH
    return None


def usage_refs(asset: dict, *, label: str = "usage") -> list[str]:
    """解析 Registry asset 的 ``usage.ref`` / ``usage.refs`` 引用。

    类型错误的字段直接报错，而不是被静默忽略成「该 Asset 没有资料」（no_ref）；
    ``ref`` 与 ``refs`` 同时存在时合并去重。无 ``usage`` 字段的旧 Asset 返回空列表。
    """
    usage = asset.get("usage") if isinstance(asset, dict) else None
    if usage is None:
        return []
    if not isinstance(usage, dict):
        raise ValueError(f"{label} 必须是对象")
    refs: list[str] = []
    single = usage.get("ref")
    if single is not None:
        if not isinstance(single, str) or not single.strip():
            raise ValueError(f"{label}.ref 必须是非空字符串")
        refs.append(single.strip())
    many = usage.get("refs")
    if many is not None:
        if not isinstance(many, list) or not many:
            raise ValueError(f"{label}.refs 必须是非空字符串数组")
        for item in many:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{label}.refs 必须是非空字符串数组")
            refs.append(item.strip())
    if not refs:
        raise ValueError(f"{label} 必须提供 ref 或 refs")
    return list(dict.fromkeys(refs))
