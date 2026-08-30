# -*- coding: utf-8 -*-
"""打印 ComfyUI API workflow 的节点与连接。

默认检查当前生产工作流 AnimaFull.json，也可传入任意 JSON 路径：
    python .tools/inspect_wf.py
    python .tools/inspect_wf.py path/to/workflow.json
"""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = ROOT / "server" / "workflows" / "AnimaFull.json"


def fmt(value):
    if isinstance(value, list):
        return f"-> node {value[0]}[{value[1]}]"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a ComfyUI API workflow")
    parser.add_argument("workflow", nargs="?", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args()
    workflow_path = args.workflow.resolve()
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

    print(f"=== {workflow_path} ===")
    print("=== ALL NODES ===")
    for node_id in sorted(workflow, key=lambda value: int(value)):
        node = workflow[node_id]
        inputs = node.get("inputs", {})
        items = ", ".join(f"{key}={fmt(value)}" for key, value in inputs.items())
        print(f"[{node_id}] {node.get('class_type')}  ::  {items}")

    print("\n=== WidgetToString consumers ===")
    widget_nodes = [
        node_id for node_id, node in workflow.items()
        if node.get("class_type") == "WidgetToString"
    ]
    print("WidgetToString ids:", widget_nodes)
    for widget_id in widget_nodes:
        for node_id, node in workflow.items():
            for key, value in node.get("inputs", {}).items():
                if isinstance(value, list) and value and value[0] == widget_id:
                    print(
                        f"  node [{node_id}] {node.get('class_type')}.{key} "
                        f"<- WidgetToString[{widget_id}]"
                    )

    print("\n=== Image output nodes ===")
    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")
        if "Saver" in class_type or "Save" in class_type or class_type == "PreviewImage":
            inputs = ", ".join(
                f"{key}={fmt(value)}" for key, value in node.get("inputs", {}).items()
            )
            print(f"[{node_id}] {class_type}  ::  {inputs}")

    print("\n=== VAEDecode consumers ===")
    vae_nodes = [
        node_id for node_id, node in workflow.items()
        if node.get("class_type") == "VAEDecode"
    ]
    for vae_id in vae_nodes:
        print(f"VAEDecode[{vae_id}]")
        for node_id, node in workflow.items():
            for key, value in node.get("inputs", {}).items():
                if isinstance(value, list) and value and value[0] == vae_id:
                    print(f"  -> node [{node_id}] {node.get('class_type')}.{key}")


if __name__ == "__main__":
    main()
