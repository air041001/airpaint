# -*- coding: utf-8 -*-
"""Phase 0 Evaluation Set baseline 跑批. 零依赖(ast+正则+urllib).
读 cases.yaml + char_dict.yaml, 调 SiliconFlow(当前 system prompt), 记录 prompt_en+breakdown 到 baseline.yaml.
用法: python run_baseline.py  (改 Prompt Engine 后重跑, 对比 baseline)"""
import ast, re, json, urllib.request, os, time
from collections import Counter

BASE = r"E:\comfy-web"

# 1. ast 提取 SILICONFLOW_SYSTEM_PROMPT (不 import main, 零依赖, 自动同步)
src = open(os.path.join(BASE, "server", "main.py"), encoding="utf-8").read()
tree = ast.parse(src)
SYSTEM_PROMPT = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "SILICONFLOW_SYSTEM_PROMPT":
                SYSTEM_PROMPT = ast.literal_eval(node.value)
assert SYSTEM_PROMPT, "未提取到 SILICONFLOW_SYSTEM_PROMPT"

# 2. 正则读 config api_key/model
cfg = open(os.path.join(BASE, "server", "config.yaml"), encoding="utf-8").read()
api_key = re.search(r'siliconflow_api_key:\s*"([^"]+)"', cfg).group(1)
model = re.search(r'siliconflow_model:\s*"([^"]+)"', cfg).group(1)

# 3. 读 char_dict.yaml (正则提取 中文名: tag)
cd = open(os.path.join(BASE, "server", "char_dict.yaml"), encoding="utf-8").read()
char_dict = {}
for m in re.finditer(r'^([^#\n:]+):\s*([\w()_]+)\s*$', cd, re.M):
    k, v = m.group(1).strip(), m.group(2).strip()
    if k and v:
        char_dict[k] = v

# 4. 读 cases.yaml (正则提取 id + input)
cases_text = open(os.path.join(BASE, ".tools", "eval_set", "cases.yaml"), encoding="utf-8").read()
cases = []
for m in re.finditer(r'- id: (\d+)\s*\n\s*category: \w+\s*\n\s*input: "(.+?)"', cases_text):
    cases.append((m.group(1), m.group(2)))

print(f"[setup] model={model}, {len(cases)} cases, char_dict={len(char_dict)} entries\n")


def match_chars(text):
    """复刻 match_characters: 子串匹配 char_dict, 返回 (tags, remaining)."""
    tags, remaining = [], text
    for name, tag in char_dict.items():
        if name in remaining:
            tags.append(tag)
            remaining = remaining.replace(name, "")
    return tags, remaining


def call_llm(context):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "/no_think " + context},
        ],
        "temperature": 0.4,
        "max_tokens": 400,
        "enable_thinking": False,
    }).encode()
    req = urllib.request.Request(
        "https://api.siliconflow.cn/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
    out = data["choices"][0]["message"]["content"].strip()
    if "</think>" in out:
        out = out.split("</think>", 1)[1].strip()
    return out


def parse(out):
    """复刻 _parse_structured_output."""
    tags, nl, bd = "", "", {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
        elif low.startswith("nl:"):
            nl = line.split(":", 1)[1].strip()
        else:
            for f in ("scene", "composition", "mood", "lighting", "style"):
                if low.startswith(f + ":"):
                    bd[f] = line.split(":", 1)[1].strip()
    return tags, bd, nl


results = []
for cid, inp in cases:
    print(f"[{cid}] {inp[:35]}...")
    char_tags, remaining = match_chars(inp)
    ctx_lines = []
    if char_tags:
        ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
    ctx_lines.append(f"Remaining: {remaining.strip()}")
    context = "\n".join(ctx_lines)
    try:
        out = call_llm(context)
        tags, bd, nl = parse(out)
        status = "OK"
    except Exception as e:
        tags, bd, nl, out, status = "", None, "", f"ERROR: {e}", "FAIL"
    results.append({"id": cid, "input": inp, "char_tags": char_tags,
                     "tags": tags, "nl": nl, "breakdown": bd, "raw": out, "status": status})
    time.sleep(0.5)

# === 验收统计 ===
print("\n" + "=" * 60)
print("验收统计")
print("=" * 60)
ok = sum(1 for r in results if r["status"] == "OK")
nl_empty = sum(1 for r in results if r["status"] == "OK" and not r["nl"])
print(f"成功: {ok}/{len(results)}, NL空: {nl_empty} (简单输入预期空), NL非空: {ok - nl_empty}")
# TAGS 重复
for r in results:
    if r["status"] != "OK":
        continue
    tlist = [t.strip() for t in r["tags"].split(",") if t.strip()]
    dups = [t for t, c in Counter(tlist).most_common(3) if c > 1 and t]
    if dups:
        print(f"  [{r['id']}] TAGS重复: {dups}")
# breakdown 完整性
incomplete = [r["id"] for r in results if r["status"] == "OK" and r["breakdown"] and len(r["breakdown"]) < 5]
if incomplete:
    print(f"  breakdown不完整(<5字段): {incomplete}")

# === 写 baseline.yaml ===
out_path = os.path.join(BASE, ".tools", "eval_set", "baseline.yaml")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(f"# Phase 0 baseline (当前 system prompt, {time.strftime('%Y-%m-%d')})\n")
    f.write(f"# model: {model}, cases: {len(results)}, ok: {ok}\n")
    for r in results:
        f.write(f"\n- id: {r['id']}\n")
        f.write(f"  input: \"{r['input']}\"\n")
        f.write(f"  char_tags: {r['char_tags']}\n")
        f.write(f"  status: {r['status']}\n")
        f.write(f"  TAGS: \"{r['tags']}\"\n")
        f.write(f"  NL: \"{r['nl']}\"\n")
        f.write(f"  breakdown: {json.dumps(r['breakdown'], ensure_ascii=False) if r['breakdown'] else 'null'}\n")
print(f"\nbaseline 写入 {out_path}")
