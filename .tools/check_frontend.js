const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const htmlPath = path.join(root, "web", "index.html");
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
if (!/<button id="dlg-img2img"[^>]*>基于此图重绘<\/button>/.test(html)
    || !html.includes("不保证只改指定内容")
    || !html.includes("高强度可能连带改变人物、发型与构图")
    || !html.includes("$('dlg-img2img').onclick = () => setDlgMode('tweak')")) {
  throw new Error("Img2Img copy or legacy tweak button contract changed; review D59");
}
console.log("Img2Img capability copy and legacy tweak button contract checked");
