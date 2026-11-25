import test from 'node:test';
import assert from 'node:assert/strict';
import { courseLink } from './catalog.mjs';
test('valid course link preserves LMS origin and deep path', () => {
  assert.equal(courseLink('PY101', 'https://learn.example.test', '/learning/py101/'), 'https://learn.example.test/learning/py101/');
});
test('rejects external URL overrides, invalid course codes and insecure nonlocal origins', () => {
  for (const path of ['//evil.example/path','/\\evil.example','https://evil.example','/\t/evil.example']) {
    assert.equal(courseLink('PY101', 'https://learn.example.test', path), null);
  }
  assert.equal(courseLink('bogus', 'https://learn.example.test', '/ok'), null);
  assert.equal(courseLink('PY101', 'http://learn.example.test', '/ok'), null);
});
