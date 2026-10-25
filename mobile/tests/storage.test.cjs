const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

// Exercise production storage logic with native I/O replaced by controllable
// promises. These tests cannot reproduce Expo Go's native permission boundary.
const source = readFileSync(require.resolve('../src/storage.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(copy) {
  const events = [];
  class Directory {
    constructor(...parts) { this.uri = parts.map(p => p.uri ?? p).join('/'); this.exists = true; }
    create() {}
    delete() { events.push('cleanup'); }
  }
  class File {
    constructor(...parts) { this.uri = parts.map(p => p.uri ?? p).join('/'); this.exists = true; this.size = 10; }
    copy(destination) { events.push(this.uri); return copy(this.uri, destination.uri); }
    delete() { events.push('cleanup'); }
  }
  const modules = {
    'expo-file-system': { Directory, File, Paths: { cache: 'file:///app/cache', document: 'file:///app/documents' } },
    'expo-file-system/legacy': { copyAsync: ({ from, to }) => copy(from, to) },
    'expo-crypto': { randomUUID: () => 'generated-id', getRandomBytesAsync: async () => new Uint8Array(32) },
    'expo-sqlite': { openDatabaseAsync: async () => ({
      execAsync: async () => {}, runAsync: async () => { events.push('insert'); },
    }) },
  };
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => {
    assert.ok(modules[name], `Unexpected import ${name}`);
    return modules[name];
  } });
  return { storage: exports, events };
}

const draft = { title: 'Class', topics: 'Topics', syllabusText: '', audioUri: 'file:///app/cache/audio.mp3',
  audioName: 'audio.mp3', audioType: 'audio/mpeg', pdfUri: 'file:///app/cache/syllabus.pdf' };

test('class is inserted only after both native copies finish', async () => {
  const pending = [];
  const { storage, events } = setup(() => new Promise(resolve => pending.push(resolve)));
  const saved = storage.createClass(draft, 'http://localhost:8000');
  assert.deepEqual(events, [draft.audioUri]);
  pending.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(events, [draft.audioUri, draft.pdfUri]);
  pending.shift()();
  const result = await saved;
  assert.equal(events.at(-1), 'insert');
  assert.match(result.draft.audioUri, /^file:\/\/\/app\/documents\//);
});

test('rejected audio or PDF copy prevents a saved job and cleans partial files', async () => {
  for (const failedUri of [draft.audioUri, draft.pdfUri]) {
    const failure = new Error('Native copy rejected');
    const { storage, events } = setup(async uri => { if (uri === failedUri) throw failure; });
    await assert.rejects(storage.createClass(draft, 'http://localhost:8000'), error => error === failure);
    assert.ok(!events.includes('insert'));
    assert.equal(events.at(-1), 'cleanup');
  }
});

test('imports the original document URI into the experience cache', async () => {
  const original = 'content://com.android.providers.media.documents/document/audio%3A42';
  const calls = [];
  const { storage } = setup(async (from, to) => calls.push({ from, to }));
  const uri = await storage.importPickedFile(original, 'lecture.mp3');
  assert.equal(calls[0].from, original);
  assert.equal(calls[0].to, uri);
  assert.match(uri, /^file:\/\/\/app\/cache\/picked-files\/.*\.mp3$/);
});

test('failed import propagates the error and cleans the partial destination', async () => {
  const failure = new Error('Provider denied read');
  const { storage, events } = setup(async () => { throw failure; });
  await assert.rejects(storage.importPickedFile('content://provider/audio', 'audio.mp3'), error => error === failure);
  assert.deepEqual(events, ['cleanup']);
});
