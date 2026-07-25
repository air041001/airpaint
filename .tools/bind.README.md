# bind.sh 使用说明

前后端绑定脚本。**每次重启 cloudflared,隧道地址都会变**(免费版特性),导致前端连不上后端、后端 CORS 拦截。跑一次 `bind.sh` 就能把新地址同步到前后端并部署。

---

## 什么时候用

只要 cloudflared 隧道地址变了(重启电脑、重启 cloudflared、隧道断线重连),就跑一次。地址没变则不用管。

新地址在启动 cloudflared 的那个窗口里,形如:

```
https://suitable-acts-lewis-else.trycloudflare.com
```

---

## 怎么用

在 Git Bash 里:

```bash
cd /e/comfy-web/.tools
./bind.sh https://你的新隧道地址.trycloudflare.com
```

或者不带地址,让它问你:

```bash
cd /e/comfy-web/.tools
./bind.sh
# 提示后粘贴地址回车
```

---

## 它做了什么

| 步骤 | 动作 | 影响文件 |
|------|------|----------|
| 1 | 替换前端里的后端 API 地址 | `web/index.html`(第 63 行的 `API` 常量) |
| 2 | 替换后端 CORS 白名单里的隧道地址 | `server/config.yaml`(`allow_origins`) |
| 3 | commit + push 前端到 GitHub Pages | 仓库 `air041001/air` |

原理:用正则匹配 `https://*.trycloudflare.com` 整段替换,所以旧地址是什么都无所谓。

---

## ⚠️ 跑完还要手动做一步

**重启后端。** config.yaml 的 CORS 改了,但正在运行的后端还在用旧配置。停掉正在跑的 `server/main.py` 再重新启动,新隧道地址的跨域请求才会被放行。

顺序建议:
1. (确认 cloudflared 已经用新地址跑起来了)
2. 跑 `bind.sh`
3. 重启后端 `server/main.py`
4. 等约 1 分钟 GitHub Pages 生效,访问 https://air041001.github.io/air/

---

## 常见问题

**push 失败,提示 `Connection was reset` / GFW 挡了 github**
开代理后重跑:
```bash
cd /e/comfy-web/web && git push
```
改动已经 commit 在本地,不会丢。也可以去 GitHub 网页手动上传 `index.html`。

**push 失败,提示 `no upstream branch`**
脚本已内置自动 `--set-upstream`,一般不会再遇到。真遇到就:
```bash
cd /e/comfy-web/web && git push --set-upstream origin main
```

**提示 `地址格式不对`**
必须是完整的 `https://xxx.trycloudflare.com`,不要带路径、不要 http、结尾斜杠可带可不带。

**提示 `没找到旧隧道地址, 跳过`**
说明那个文件里当前没有 trycloudflare 地址(可能被手动改过)。去对应文件手动检查一下 API 地址 / allow_origins。

---

## 前端访客侧的应急办法

如果你懒得 push、或 Pages 还没生效,访客也能在浏览器自己临时指定后端地址。在页面按 F12 打开控制台,粘贴:

```js
localStorage.setItem('api_base', 'https://你的新隧道地址.trycloudflare.com'); location.reload();
```

前端代码里 `localStorage` 的 `api_base` 优先级高于硬编码地址。
