import fs from 'node:fs';
import { JSDOM } from 'jsdom';

const docPath = process.argv[2] || '资料/technical_doc.md';
const md = fs.readFileSync(docPath, 'utf8');
const lines = md.split('\n');

// 抽取所有 ```mermaid 代码块
const blocks = [];
for (let i = 0; i < lines.length; i++) {
  if (lines[i].trim() === '```mermaid') {
    const start = i + 1;
    let j = start;
    while (j < lines.length && lines[j].trim() !== '```') j++;
    blocks.push({ line: start + 1, src: lines.slice(start, j).join('\n') });
    i = j;
  }
}

const dom = new JSDOM('<!DOCTYPE html><html><body><div id="g"></div></body></html>', {
  pretendToBeVisual: true,
  url: 'http://localhost/',
});
global.window = dom.window;
global.document = dom.window.document;
try { Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true }); } catch (e) {}
global.DOMPurify = undefined;
global.Element = dom.window.Element;
global.SVGElement = dom.window.SVGElement;
global.Node = dom.window.Node;
global.HTMLElement = dom.window.HTMLElement;
global.MutationObserver = dom.window.MutationObserver;
global.getComputedStyle = dom.window.getComputedStyle;
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);

const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

let ok = 0, fail = 0;
console.log('mermaid 包:', JSON.parse(fs.readFileSync('./node_modules/mermaid/package.json', 'utf8')).version);
console.log('共抽取到', blocks.length, '个 mermaid 块\n');
for (const b of blocks) {
  try {
    await mermaid.parse(b.src);
    console.log(`  ✅ OK   文档 L${b.line}  (${b.src.split('\n').length} 行)`);
    ok++;
  } catch (e) {
    console.log(`  ❌ FAIL 文档 L${b.line}  ${String(e.message || e).slice(0, 300)}`);
    fail++;
  }
}
console.log(`\n结果：${ok} 通过 / ${fail} 失败（共 ${blocks.length}）`);
process.exit(fail ? 1 : 0);
