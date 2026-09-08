"""Process-local caches, scheduler primitives, and shared service clients."""
import asyncio
import uuid

import httpx

from server.persistence import AirPaintStore, OwnerIdentity
from server.settings import (
    BASE,
    DATABASE_PATH,
    IDENTITY_KEY_PATH,
    LORA_PREVIEWS,
    SOURCE_IMAGES,
)


IMAGES = BASE / "images"
IMAGES.mkdir(exist_ok=True)
LORA_PREVIEWS.mkdir(exist_ok=True)
SOURCE_IMAGES.mkdir(parents=True, exist_ok=True)

STORE = AirPaintStore(DATABASE_PATH)
IDENTITY = OwnerIdentity(IDENTITY_KEY_PATH)

# Hot caches only.  SQLite is authoritative and every public read can hydrate
# these again after a restart.
JOBS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
QUEUE: asyncio.Queue[str] = asyncio.Queue()
# Kept as a compatibility view for old maintenance tests; quota decisions no
# longer read this dictionary.
USAGE: dict[str, list] = {}
CLIENT = httpx.AsyncClient(timeout=60)
CLIENT_ID = uuid.uuid4().hex
WORKER_TASK: asyncio.Task | None = None
