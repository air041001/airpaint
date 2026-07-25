# -*- coding: utf-8 -*-
import json
from pathlib import Path
d = json.loads(Path("E:/comfy-web/server/workflows/AnimaStandardV7.json").read_text(encoding="utf-8"))

def fmt(v):
    if isinstance(v, list):
        return f"-> node {v[0]}[{v[1]}]"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."

print("=== ALL NODES ===")
for nid in sorted(d.keys(), key=lambda x: int(x)):
    n = d[nid]
    ins = n.get("inputs", {})
    items = ", ".join(f"{k}={fmt(v)}" for k, v in ins.items())
    print(f"[{nid}] {n.get('class_type')}  ::  {items}")

print("\n=== who references WidgetToString output? ===")
# find WidgetToString id
wts = [nid for nid, n in d.items() if n.get("class_type") == "WidgetToString"]
print("WidgetToString ids:", wts)
for w in wts:
    for nid, n in d.items():
        for k, v in n.get("inputs", {}).items():
            if isinstance(v, list) and v and v[0] == w:
                print(f"  node [{nid}] {n.get('class_type')}.{k} <- WidgetToString[{w}]")

print("\n=== Image Saver / SaveImage / output nodes ===")
for nid, n in d.items():
    ct = n.get("class_type", "")
    if "Saver" in ct or "Save" in ct or ct in ("PreviewImage",):
        print(f"[{nid}] {ct}  ::  " + ", ".join(f"{k}={fmt(v)}" for k, v in n.get('inputs',{}).items()))

print("\n=== VAEDecode consumers ===")
vaes = [nid for nid, n in d.items() if n.get("class_type") == "VAEDecode"]
for vd in vaes:
    print(f"VAEDecode[{vd}]")
    for nid, n in d.items():
        for k, v in n.get("inputs", {}).items():
            if isinstance(v, list) and v and v[0] == vd:
                print(f"  -> node [{nid}] {n.get('class_type')}.{k}")
