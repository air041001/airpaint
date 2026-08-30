"""进程内运行状态与共享 HTTP 客户端。"""
import asyncio
import uuid

import httpx

from server.settings import BASE, LORA_PREVIEWS


IMAGES = BASE / "images"
IMAGES.mkdir(exist_ok=True)
LORA_PREVIEWS.mkdir(exist_ok=True)

JOBS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
QUEUE: asyncio.Queue[str] = asyncio.Queue()
USAGE: dict[str, list] = {}
CLIENT = httpx.AsyncClient(timeout=60)
CLIENT_ID = uuid.uuid4().hex
