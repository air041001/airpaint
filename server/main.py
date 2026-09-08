# -*- coding: utf-8 -*-
"""AirPaint 后端启动入口与迁移期兼容导出。

生产实现按职责位于 settings/runtime/knowledge/lora/prompt_engine/
workflow_engine/api；保留本模块导出，避免现有维护脚本一次性失效。
"""
from pathlib import Path
import asyncio
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import api as api_module
from server import knowledge as knowledge_module
from server import lora as lora_module
from server import prompt_engine as prompt_module
from server import runtime as runtime_module
from server import settings as settings_module
from server import workflow_engine as workflow_module


_IMPLEMENTATION_MODULES = (
    settings_module,
    runtime_module,
    knowledge_module,
    lora_module,
    prompt_module,
    workflow_module,
    api_module,
)

for _module in _IMPLEMENTATION_MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            globals()[_name] = _value

app = api_module.app


async def _serve_until_stopped() -> None:
    """Run uvicorn and honor the local stop marker used by the Windows script."""
    import uvicorn

    stop_marker = settings_module.STATE_DIR / "run" / "stop.request"
    stop_marker.parent.mkdir(parents=True, exist_ok=True)
    stop_marker.unlink(missing_ok=True)
    config = uvicorn.Config(
        app,
        host=settings_module.CFG.get("host", "127.0.0.1"),
        port=int(settings_module.CFG.get("port", 8000)),
    )
    server = uvicorn.Server(config)

    async def watch_stop_marker() -> None:
        while not server.should_exit:
            if stop_marker.exists():
                stop_marker.unlink(missing_ok=True)
                server.should_exit = True
                return
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(watch_stop_marker(), name="airpaint-stop-monitor")
    try:
        await server.serve()
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        stop_marker.unlink(missing_ok=True)


async def siliconflow_translate(*args, **kwargs):
    """兼容旧实验脚本对 main 中 Prompt 覆盖项的临时 monkeypatch。"""
    prompt_module.PAINTER_SYSTEM_PROMPT = globals()["PAINTER_SYSTEM_PROMPT"]
    prompt_module._composer_character_lora_appearance_issue = globals()[
        "_composer_character_lora_appearance_issue"
    ]
    return await prompt_module.siliconflow_translate(*args, **kwargs)


if __name__ == "__main__":
    asyncio.run(_serve_until_stopped())
