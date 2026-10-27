import test from 'node:test';
import assert from 'node:assert/strict';
import { renderRunsHTML } from '../../src/iterm_dash/web/rows.js';

test('escapes text', () => {
  const h = renderRunsHTML([['<script>', null, null, 0]]);
  assert.ok(h.includes('&lt;script&gt;'));
  assert.ok(!h.includes('<script'));
});
test('escapes quotes and ampersand', () => {
  const h = renderRunsHTML([[`&"'`, null, null, 0]]);
  assert.ok(h.includes('&amp;&quot;&#39;'));
});
test('invalid colour never reaches style', () => {
  const h = renderRunsHTML([['x', 'red;}body{x', null, 0]]);
  assert.ok(!h.includes('style'));
});
test('NUL characters render as spaces, never vanish', () => {
  assert.equal(renderRunsHTML([['a\u0000b', null, null, 0]]), 'a b');
});
test('wide flag', () => {
  assert.ok(renderRunsHTML([['界', null, null, 32]]).includes('class="w"'));
});
test('colours and flags', () => {
  const h = renderRunsHTML([['a', '#ff0000', '#000000', 1 | 4]]);
  assert.ok(h.includes('style="color:#ff0000;background:#000000"'));
  assert.ok(h.includes('class="b u"'));
});
