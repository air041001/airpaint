# -*- coding: utf-8 -*-
import httpx, time, json, sys, os, yaml
from pathlib import Path
BASE = "http://127.0.0.1:8000"
# token 从本地 config.yaml 读 (该文件已 gitignore, 不进仓库); 也可用环境变量 AIRPAINT_TOKEN 覆盖
_CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "server" / "config.yaml").read_text(encoding="utf-8"))
TOKEN = os.environ.get("AIRPAINT_TOKEN") or (_CFG.get("tokens") or [None])[0]
if not TOKEN:
    print("未找到 token: 请在 server/config.yaml 配 tokens, 或设环境变量 AIRPAINT_TOKEN"); sys.exit(1)
H = {"Authorization": f"Bearer {TOKEN}"}

r = httpx.post(f"{BASE}/api/jobs",
               headers={**H, "Content-Type": "application/json"},
               json={"workflow": "anima",
                     "prompt": "白发蓝眼睛的猫耳少女, 微笑, 站在樱花树下",
                     "size": "832x1216"},
               timeout=30)
print("create:", r.status_code, r.text, flush=True)
if r.status_code != 200:
    sys.exit(1)
job = r.json()
jid = job["id"]
print("prompt_en:", job.get("prompt_en"), flush=True)

for i in range(60):
    time.sleep(4)
    s = httpx.get(f"{BASE}/api/jobs/{jid}", headers=H, timeout=10).json()
    st = s.get("status")
    extra = f" pos={s.get('position')}" if st == "queued" else ""
    print(f"[{i}] {st}{extra}", flush=True)
    if st in ("done", "failed"):
        print("RESULT:", json.dumps(s, ensure_ascii=False), flush=True)
        break
