const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const htmlPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root, "web", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((source) => source.trim());

scripts.forEach((source, index) => {
  try {
    new Function(source);
  } catch (error) {
    throw new Error(`inline script ${index + 1}: ${error.message}`);
  }
});

console.log(`${scripts.length} inline scripts parsed: ${path.relative(root, htmlPath)}`);

// Img2Img is a full-image redraw, not a guarantee of isolated edits (D59).
for (const claim of ["只写改动即可，其余内容从原图理解", "留空则保留原图内容", "直接在这张图上改、保留构图"]) {
  if (html.includes(claim)) throw new Error(`unsupported Img2Img claim: ${claim}`);
}
if (!/<button\b[^>]*\bid="dlg-img2img"[^>]*>基于此图重绘<\/button>/.test(html)
    || !html.includes("不保证只改指定内容")
    || !html.includes("高强度可能连带改变人物、发型与构图")
    || !html.includes("$('dlg-img2img').onclick = () => setDlgMode('tweak')")) {
  throw new Error("Img2Img copy or legacy tweak button contract changed; review D59");
}
console.log("Img2Img capability copy and legacy tweak button contract checked");

// Final-seal reliability contract: server history, cookie auth, stable client
// request IDs, and honest recoverable states must remain visible in the UI.
for (const marker of [
  "/api/history",
  "credentials = 'include'",
  "client_request_id",
  "waiting_for_comfy",
  "reconcile_pending",
  "result_ready",
  "重新核对",
  "const latest = historyItems.find(job => job.status === 'done' && job.image)",
  "#image-fit,#image-crop,#reference-scope",
  "用中文构思，保留 Prompt 与成像参数控制",
]) {
  if (!html.includes(marker)) throw new Error(`missing reliability UI contract: ${marker}`);
}
if (/localStorage\.setItem\(HIST_KEY/.test(html)) {
  throw new Error("generation history must be server-authoritative, not written to localStorage");
}
console.log("Persistent history, idempotency, cookie auth, and recovery states checked");
