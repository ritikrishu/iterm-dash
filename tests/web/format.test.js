import test from 'node:test';
import assert from 'node:assert/strict';
import { formatAgo } from '../../src/iterm_dash/web/format.js';

test('formatAgo', () => {
  for (const [s, out] of [[0, '0s'], [59, '59s'], [60, '1m'], [3599, '59m'], [3600, '1h'], [86400, '1d']]) {
    assert.equal(formatAgo(s), out);
  }
});
