# -*- coding: utf-8 -*-
"""AirPaint 后端启动入口与迁移期兼容导出。

生产实现按职责位于 settings/runtime/knowledge/lora/prompt_engine/
workflow_engine/api；保留本模块导出，避免现有维护脚本一次性失效。
"""
from pathlib import Path
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


async def siliconflow_translate(*args, **kwargs):
    """兼容旧实验脚本对 main 中 Prompt 覆盖项的临时 monkeypatch。"""
    prompt_module.PAINTER_SYSTEM_PROMPT = globals()["PAINTER_SYSTEM_PROMPT"]
    prompt_module._composer_character_lora_appearance_issue = globals()[
        "_composer_character_lora_appearance_issue"
    ]
    return await prompt_module.siliconflow_translate(*args, **kwargs)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings_module.CFG.get("host", "127.0.0.1"),
        port=int(settings_module.CFG.get("port", 8000)),
    )
