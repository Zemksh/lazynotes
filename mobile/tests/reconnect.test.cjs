const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(require.resolve('../src/reconnect.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function setup(status) {
  const exportsObject = {}, patches = [];
  vm.runInNewContext(compiled, { exports: exportsObject, require: name => {
    if (name === './api') return { API_URL: 'http://new-server:8000', status };
    if (name === './storage') return { patchClass: async (id, patch) => patches.push({ id, patch }) };
    throw Error(name);
  } });
  return { reconnect: exportsObject.reconnectClass, patches };
}
const item = { id: 'local', remoteId: 'remote', token: 'existing-token', apiUrl: 'http://old-server:8000',
  result: { notes: { title: 'Saved' } }, edits: { '0': { body: 'My edit' } }, draft: { audioUri: 'saved-file' } };

test('verifies the same job and changes only connection fields for downloaded notes', async () => {
  const before = JSON.stringify(item);
  const { reconnect, patches } = setup(async candidate => {
    assert.equal(candidate.apiUrl, 'http://new-server:8000');
    assert.equal(candidate.remoteId, item.remoteId);
    assert.equal(candidate.token, item.token);
    return { id: 'remote', state: 'ready' };
  });
  await reconnect(item);
  assert.equal(patches.length, 1);
  assert.deepEqual(Object.keys(patches[0].patch).sort(), ['apiUrl', 'syncError']);
  assert.equal(JSON.stringify(item), before);
});

test('unreachable, expired, or wrong-server responses never change stored data', async () => {
  for (const status of [async () => { throw Error('unreachable'); },
    async () => { throw Error('410 expired'); }, async () => ({ id: 'different-job' })]) {
    const { reconnect, patches } = setup(status);
    await assert.rejects(reconnect(item));
    assert.equal(patches.length, 0);
  }
});

test('a pending class can resume polling with the verified server state', async () => {
  const { reconnect, patches } = setup(async () => ({ id: 'remote', state: 'preparing', error: null, expires_at: 123 }));
  await reconnect({ ...item, result: undefined, stage: 'expired' });
  assert.equal(patches[0].patch.stage, 'preparing');
  assert.equal(patches[0].patch.expiresAt, 123);
  assert.ok(!('draft' in patches[0].patch));
});

test('missing and deleted server jobs cannot be reconnected', async () => {
  const { reconnect, patches } = setup(async () => { throw Error('should not call'); });
  await assert.rejects(reconnect({ ...item, remoteId: undefined }), /No existing/);
  await assert.rejects(reconnect({ ...item, serverDeleted: true }), /No existing/);
  assert.equal(patches.length, 0);
});

test('the UI requires confirmation before sending the class token to a changed host', () => {
  const source = readFileSync(require.resolve('../src/ReconnectServer.tsx'), 'utf8');
  assert.match(source, /Alert.alert\('Reconnect this class\?'/);
  assert.match(source, /same trusted backend/);
  assert.match(source, /text: 'Cancel'/);
  assert.match(source, /text: 'Reconnect', onPress: \(\) => \{ void connect\(\); \}/);
});
