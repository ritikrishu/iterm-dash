import test from 'node:test';
import assert from 'node:assert/strict';
import { createClickRouter } from '../../src/iterm_dash/web/clicks.js';

function setup() {
  const calls = [];
  let timers = [];
  let next = 1;
  const router = createClickRouter({
    onSingle: (id) => calls.push(['single', id]),
    onDouble: (id) => calls.push(['double', id]),
    setTimer: (fn) => { const t = next++; timers.push({ t, fn }); return t; },
    clearTimer: (t) => { timers = timers.filter((x) => x.t !== t); },
  });
  const fire = () => { const ts = timers; timers = []; ts.forEach((x) => x.fn()); };
  return { calls, router, fire };
}

test('one click gives single after the window, never double', () => {
  const { calls, router, fire } = setup();
  router('a');
  assert.deepEqual(calls, []);
  fire();
  assert.deepEqual(calls, [['single', 'a']]);
});
test('two clicks within the window give one double and no single', () => {
  const { calls, router, fire } = setup();
  router('a'); router('a');
  fire();
  assert.deepEqual(calls, [['double', 'a']]);
});
test('clicks on a then b give single a then single b', () => {
  const { calls, router, fire } = setup();
  router('a'); router('b');
  assert.deepEqual(calls, [['single', 'a']]);
  fire();
  assert.deepEqual(calls, [['single', 'a'], ['single', 'b']]);
});
