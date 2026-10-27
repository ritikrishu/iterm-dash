import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseLayout } from '../../src/iterm_dash/web/layout.js';

const base = { cols: 214, rows: 57, charWidthPx: 0.6, lineHeight: 1.2, minFontPx: 7, maxFontPx: 13, headerPx: 48, gapPx: 10 };
const cases = [
  ['ultrawide, 5 sessions', 3440, 1400, 5, [3, 2, 8.879, 57]],
  ['27in QHD, 5 sessions', 2560, 1400, 5, [2, 3, 7, 49]],
  ['laptop, 6 sessions', 1512, 900, 6, [2, 3, 5.849, 34]],
  ['ultrawide, 4 sessions', 3440, 1400, 4, [2, 2, 9.459, 57]],
  ['one session', 1512, 900, 1, [1, 1, 11.776, 57]],
  ['none', 1512, 900, 0, [0, 0, 0, 0]],
];
for (const [name, width, height, count, [columns, cellRows, fontPx, visibleRows]] of cases) {
  test(`chooseLayout: ${name}`, () => {
    const r = chooseLayout({ ...base, width, height, count });
    assert.equal(r.columns, columns);
    assert.equal(r.cellRows, cellRows);
    assert.ok(Math.abs(r.fontPx - fontPx) < 0.01, `fontPx ${r.fontPx} vs ${fontPx}`);
    assert.equal(r.visibleRows, visibleRows);
  });
}
