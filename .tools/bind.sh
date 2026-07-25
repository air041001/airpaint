#!/usr/bin/env bash
# 前后端绑定脚本 —— 每次 cloudflared 重启后隧道地址会变, 跑这个一键同步.
# 做三件事:
#   1. 把新隧道地址写进前端 web/index.html (API 常量)
#   2. 把新隧道地址写进后端 server/config.yaml 的 CORS allow_origins
#   3. commit + push 前端到 GitHub Pages (air041001/air)
# 用法:
#   ./bind.sh https://xxxx-yyyy-zzzz.trycloudflare.com
#   ./bind.sh            # 不带参数则交互式输入
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"          # .tools 的上级 = comfy-web
WEB="$ROOT/web/index.html"
CFG="$ROOT/server/config.yaml"
export PATH="$SCRIPT_DIR/gh/bin:$PATH"   # 让 git 能用到便携版 gh (若需)

# --- 取隧道地址 ---
URL="${1:-}"
if [ -z "$URL" ]; then
  read -rp "输入 cloudflared 隧道地址 (https://xxx.trycloudflare.com): " URL
fi
URL="${URL%/}"   # 去掉结尾斜杠
if [[ ! "$URL" =~ ^https://[a-z0-9-]+\.trycloudflare\.com$ ]]; then
  echo "✗ 地址格式不对: $URL"
  echo "  应形如 https://suitable-acts-lewis-else.trycloudflare.com"
  exit 1
fi
echo "→ 目标隧道: $URL"

# --- 1 & 2: 替换两个文件里所有旧隧道地址 ---
# 匹配任意 https://*.trycloudflare.com, 整段换成新地址.
PAT='https://[a-z0-9-]+\.trycloudflare\.com'
for f in "$WEB" "$CFG"; do
  [ -f "$f" ] || { echo "✗ 找不到文件: $f"; exit 1; }
  if grep -Eq "$PAT" "$f"; then
    sed -i -E "s#$PAT#$URL#g" "$f"
    echo "✓ 已更新 ${f#$ROOT/}"
  else
    echo "⚠ ${f#$ROOT/} 里没找到旧隧道地址, 跳过 (可能需手动检查)"
  fi
done

# --- 3: push 前端 ---
cd "$ROOT/web"
if git diff --quiet -- index.html; then
  echo "· 前端 index.html 无变化, 不需 push"
else
  git add index.html
  git commit -q -m "chore: update tunnel url" || true
  echo "→ 正在 push 到 GitHub Pages ..."
  # 首次 push 若无 upstream, 自动绑定当前分支
  BR="$(git branch --show-current)"
  if git push -q 2>/tmp/bind_push.err || git push -q --set-upstream origin "$BR" 2>/tmp/bind_push.err; then
    echo "✓ 前端已推送, 约 1 分钟后生效: https://air041001.github.io/air/"
  else
    echo "✗ push 失败 (常见原因: 没开代理, GFW 挡了 github):"
    sed 's/^/    /' /tmp/bind_push.err
    echo "    改动已 commit 在本地, 开代理后重跑 'git push', 或去网页手动传 index.html"
  fi
fi

echo
echo "★ 别忘了: 后端 config.yaml 的 CORS 变了, 需重启后端才生效."
echo "  (如果后端正在跑, 停掉再启动 server/main.py)"
