import test from 'node:test';
import assert from 'node:assert/strict';
import { nextNeedsYou, moveFocus, shouldHandleKey, keepFocus } from '../../src/iterm_dash/web/nav.js';

const cells = [{id:'a',tone:'working',changedAt:1},{id:'b',tone:'done',changedAt:5},{id:'c',tone:'waiting',changedAt:9},{id:'d',tone:'done',changedAt:2},{id:'e',tone:'error',changedAt:7}];
test('nextNeedsYou cycles waiting → error → done (oldest first) and wraps', () => {
  assert.equal(nextNeedsYou(cells, null), 'c');
  assert.equal(nextNeedsYou(cells, 'c'), 'e');
  assert.equal(nextNeedsYou(cells, 'e'), 'd');
  assert.equal(nextNeedsYou(cells, 'd'), 'b');
  assert.equal(nextNeedsYou(cells, 'b'), 'c');
  assert.equal(nextNeedsYou(cells, 'a'), 'c');
  assert.equal(nextNeedsYou([{id:'a',tone:'working',changedAt:1}], null), null);
});
test('moveFocus moves in the grid and clamps at edges', () => {
  assert.equal(moveFocus(0, 'ArrowRight', 3, 5), 1);
  assert.equal(moveFocus(0, 'ArrowLeft', 3, 5), 0);
  assert.equal(moveFocus(1, 'ArrowDown', 3, 5), 4);
  assert.equal(moveFocus(2, 'ArrowDown', 3, 5), 2);
  assert.equal(moveFocus(4, 'ArrowUp', 3, 5), 1);
});
test('shouldHandleKey ignores modifier chords', () => {
  assert.equal(shouldHandleKey({key:'1', metaKey:true, ctrlKey:false, altKey:false}), false);
  assert.equal(shouldHandleKey({key:'t', metaKey:false, ctrlKey:true, altKey:false}), false);
  assert.equal(shouldHandleKey({key:'n', metaKey:false, ctrlKey:false, altKey:true}), false);
  assert.equal(shouldHandleKey({key:'H', metaKey:false, ctrlKey:false, altKey:false}), true);
});
test('keepFocus focuses cell 1 once the grid has cells and nothing is focused', () => {
  assert.equal(keepFocus([], ['a', 'b'], null), 'a');
  assert.equal(keepFocus(['a'], ['a', 'b'], null), 'a');
  assert.equal(keepFocus([], [], null), null);
});
test('keepFocus keeps the focused cell, or moves to the cell that took its place', () => {
  assert.equal(keepFocus(['a', 'b', 'c'], ['c', 'a', 'b'], 'b'), 'b');
  assert.equal(keepFocus(['a', 'b', 'c'], ['a', 'c'], 'b'), 'c');
  assert.equal(keepFocus(['a', 'b', 'c'], ['a', 'b'], 'c'), 'b');
  assert.equal(keepFocus(['a'], [], 'a'), null);
  assert.equal(keepFocus([], ['x', 'y'], 'gone'), 'x');
});
