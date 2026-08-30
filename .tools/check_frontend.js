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
