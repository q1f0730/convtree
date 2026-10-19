#!/usr/bin/env node
// 前端纯逻辑验收：从 demo/ink.html 抽出 <script id="uilogic"> 块（无 DOM 依赖），在 node vm 里跑。
// 用法（项目根目录）：node tools/test_ui_logic.js    退出码 0 = 全过
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'ink.html'), 'utf8');
const m = html.match(/<script id="uilogic">([\s\S]*?)<\/script>/);
let fail = 0, n = 0;
function check(ok, name) { n++; if (ok) console.log('OK   ' + n + ' ' + name); else { fail++; console.log('FAIL ' + n + ' ' + name) } }
if (!m) { check(false, 'ink.html 含 <script id="uilogic"> 纯逻辑块'); console.log('\n共 ' + n + ' 例，失败 ' + fail); process.exit(2) }
const pick = ['kbdLabel', 'placeMenu', 'layoutTree'].map(k => k + ':typeof ' + k + "==='function'?" + k + ':null').join(',');
const L = vm.runInNewContext(m[1] + ';({' + pick + '})', {});
function section(fn, title) { if (!L[fn]) { check(false, title + '：缺 ' + fn + '()'); return false } return true }

// ── 平台快捷键文案：非 Mac 一律 Ctrl ──
if (section('kbdLabel', '快捷键文案')) {
check(L.kbdLabel('Win32') === 'Ctrl K', 'Windows 显示 Ctrl K');
check(L.kbdLabel('Linux x86_64') === 'Ctrl K', 'Linux 显示 Ctrl K');
check(L.kbdLabel('MacIntel') === '⌘ K', 'Mac 显示 ⌘ K');
}

// ── 划词菜单定位：默认在选区上方，不盖住选区；贴边翻到下方；左右夹在视口内 ──
if (section('placeMenu', '划词菜单定位')) {
const VP = { w: 1440, h: 900 }, MENU = { w: 300, h: 44 };
const overlaps = (a, b) => a.x < b.right && a.x + MENU.w > b.left && a.y < b.bottom && a.y + MENU.h > b.top;
{ const r = { left: 400, top: 300, right: 600, bottom: 320 }; const p = L.placeMenu(r, r, MENU, VP);
  check(p.y === 248 && p.x === 350 && p.side === 'above', '单行选区：菜单居中放上方 8px 处');
  check(!overlaps(p, r), '单行选区：菜单不盖选区'); }
{ const r = { left: 400, top: 30, right: 600, bottom: 50 }; const p = L.placeMenu(r, r, MENU, VP);
  check(p.side === 'below' && p.y === 58, '贴顶：翻到选区下方');
  check(!overlaps(p, r), '贴顶：翻下后仍不盖选区'); }
{ const a = { left: 300, top: 400, right: 900, bottom: 420 }, b = { left: 300, top: 460, right: 520, bottom: 480 };
  const p = L.placeMenu(a, b, MENU, VP);
  check(p.side === 'above' && p.y === 348 && !overlaps(p, a) && !overlaps(p, b), '多行选区：放首行上方，不盖任一行'); }
{ const a = { left: 300, top: 20, right: 900, bottom: 40 }, b = { left: 300, top: 300, right: 520, bottom: 320 };
  const p = L.placeMenu(a, b, MENU, VP);
  check(p.side === 'below' && p.y === 328 && !overlaps(p, a) && !overlaps(p, b), '多行选区贴顶：放末行下方'); }
{ const r = { left: 1400, top: 300, right: 1430, bottom: 320 }; const p = L.placeMenu(r, r, MENU, VP);
  check(p.x === 1132, '贴右：夹回视口内（留 8px）'); }
{ const r = { left: 2, top: 300, right: 20, bottom: 320 }; const p = L.placeMenu(r, r, MENU, VP);
  check(p.x === 8, '贴左：夹回视口内（留 8px）'); }
}

// ── 树布局：矩形卡片 + 由真实布局算出的连线（实线、正交、首尾相接）──
if (section('layoutTree', '树布局')) {
const nodes = [{ id: 'R', parent_id: null }, { id: 'A', parent_id: 'R' }, { id: 'B', parent_id: 'R' },
  { id: 'C', parent_id: 'R' }, { id: 'A1', parent_id: 'A' }];
const T = L.layoutTree(nodes, { nodeW: 100, nodeH: 60, gap: 20, hgap: 40, margin: 10 });
const bx = T.boxes;
check(Object.keys(bx).length === 5, '五个节点都有卡片框');
check(bx.A1.x === 10 && bx.A.x === 10 && bx.B.x === 130 && bx.C.x === 250, '兄弟并排、间距=gap（手算值）');
check(bx.R.x === 130, '父卡居中于首末子之上（手算值 130）');
check(bx.R.y > bx.A.y && bx.A.y > bx.A1.y, '根在下、子孙往上长');
const ids = Object.keys(bx);
let noOverlap = true;
for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
  const p = bx[ids[i]], q = bx[ids[j]];
  if (p.x < q.x + q.w && p.x + p.w > q.x && p.y < q.y + q.h && p.y + p.h > q.y) noOverlap = false }
check(noOverlap, '卡片互不重叠');
check(T.segs.length > 0 && T.segs.every(s => s[0] === s[2] || s[1] === s[3]), '连线全为水平/竖直线段');
const onSeg = (x, y, s) => (s[0] === s[2] && x === s[0] && y >= Math.min(s[1], s[3]) && y <= Math.max(s[1], s[3])) ||
  (s[1] === s[3] && y === s[1] && x >= Math.min(s[0], s[2]) && x <= Math.max(s[0], s[2]));
const touches = (x, y) => T.segs.some(s => onSeg(x, y, s));
const cx = b => b.x + b.w / 2;
check(['A', 'B', 'C', 'A1'].every(id => touches(cx(bx[id]), bx[id].y + bx[id].h)), '每张子卡底边中点有连线接上');
check(touches(cx(bx.R), bx.R.y) && touches(cx(bx.A), bx.A.y), '父卡顶边中点有连线接上');
// 连通：从根顶点出发，沿线段（端点落在另一段上即相连）能走到所有子卡锚点
{ const segs = T.segs.slice(); const seen = new Set(); const q = [];
  segs.forEach((s, i) => { if (onSeg(cx(bx.R), bx.R.y, s)) { seen.add(i); q.push(i) } });
  while (q.length) { const s = segs[q.shift()];
    segs.forEach((t, j) => { if (seen.has(j)) return;
      if (onSeg(t[0], t[1], s) || onSeg(t[2], t[3], s) || onSeg(s[0], s[1], t) || onSeg(s[2], s[3], t)) { seen.add(j); q.push(j) } }) }
  const reach = (x, y) => [...seen].some(i => onSeg(x, y, segs[i]));
  check(['A', 'B', 'C'].every(id => reach(cx(bx[id]), bx[id].y + bx[id].h)), 'R→A/B/C 连线连通无断口'); }
check(T.width >= bx.C.x + bx.C.w && T.height >= bx.R.y + bx.R.h, '画布尺寸包住所有卡片');
}

console.log('\n共 ' + n + ' 例，失败 ' + fail);
process.exit(fail ? 2 : 0);
