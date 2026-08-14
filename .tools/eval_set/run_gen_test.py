# -*- coding: utf-8 -*-
"""第二层生图验收: 选 2 条代表性 case 实际生图, 让用户判断提示词出图是否符合意图。
002 天宫心(角色锁定+场景) + 018 两剑士对峙(多角色+复杂动作)。"""
import urllib.request, json, time

BASE = "http://127.0.0.1:8000"
TOKEN = "friend-123"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def translate(text):
    body = json.dumps({"prompt": text}).encode()
    req = urllib.request.Request(f"{BASE}/api/translate", data=body, headers=H)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def submit(prompt_en, prompt_raw, size):
    body = json.dumps({"workflow": "anima", "prompt_en": prompt_en,
                       "prompt": prompt_raw, "size": size}).encode()
    req = urllib.request.Request(f"{BASE}/api/jobs", data=body, headers=H)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["id"]


def poll(jid):
    for _ in range(90):  # 最多 180s
        req = urllib.request.Request(f"{BASE}/api/jobs/{jid}",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        if d["status"] in ("done", "failed"):
            return d
        time.sleep(2)
    return {"status": "timeout"}


cases = [
    ("002", "天宫心在神社前微笑", "832x1216"),
    ("018", "两个剑士在雨中对峙一人持刀前倾蓄力一人后撤半蹲举盾格挡闪电照亮他们的脸", "1216x832"),
]

for cid, text, size in cases:
    print(f"\n=== {cid}: {text[:35]}... (size={size}) ===")
    tr = translate(text)
    pe = tr["prompt_en"]
    print(f"prompt_en: {pe}")
    jid = submit(pe, text, size)
    print(f"job_id: {jid}, 轮询中...")
    res = poll(jid)
    print(f"status: {res['status']}")
    if res["status"] == "done":
        print(f"image_url: {BASE}{res['image']}")
        print(f"image_path: E:/comfy-web/server/images/{res['image'].rsplit('/',1)[-1]}")
    else:
        print(f"error: {res.get('error', 'unknown')}")
