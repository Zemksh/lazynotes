const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

// Render the actual component into a lightweight native-element tree.
// Device layout/accessibility still need a phone acceptance check.
const source = readFileSync(require.resolve('../src/StudySection.tsx'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
} }).outputText;
const exportsObject = {};
const element = (type, props) => ({ type, props });
vm.runInNewContext(compiled, { exports: exportsObject, require: name => {
  if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element };
  if (name === 'react-native') return { Text: 'Text', View: 'View', ScrollView: 'ScrollView' };
  if (name === './theme') return { colors: {}, ui: {} };
  throw Error(`Unexpected dependency: ${name}`);
} });
function texts(node) {
  if (node == null || typeof node === 'boolean') return [];
  if (typeof node === 'string') return [node];
  if (Array.isArray(node)) return node.flatMap(texts);
  return texts(node.props.children);
}

const section = { heading: 'Transport', subheading: 'Diffusion', body: 'First explanation.\nSecond explanation.',
  bullets: ['No ATP required.', 'Moves down a gradient.'], source_segment_ids: ['INTERNAL_ID'],
  comparison: { columns: ['Mechanism', 'Energy'], rows: [['Diffusion', 'None'], ['Active', 'ATP']] },
  review_items: [{ reason: 'INTERNAL_WARNING' }], reliability: 'INTERNAL_DIAGNOSTIC',
};

test('renders topic, subtopic, paragraphs, bullets and comparison without internal metadata', () => {
  const rendered = texts(exportsObject.StudySection({ section }));
  for (const value of ['Transport', 'Diffusion', 'First explanation.', 'Second explanation.',
    '•', 'No ATP required.', 'Moves down a gradient.', 'Mechanism', 'Energy', 'None', 'Active', 'ATP']) {
    assert.ok(rendered.includes(value), value);
  }
  assert.doesNotMatch(rendered.join(' '), /INTERNAL_|timestamp|reliability|Play from/);
});

test('old saved notes render and edits preserve unedited structured blocks', () => {
  assert.deepEqual(Array.from(texts(exportsObject.StudySection({ section: { heading: 'Old', body: 'Saved paragraph' } }))),
    ['Old', 'Saved paragraph']);
  const rendered = texts(exportsObject.StudySection({ section, edit: { heading: 'Edited', body: 'My explanation' } }));
  assert.ok(rendered.includes('My explanation'));
  assert.ok(rendered.includes('No ATP required.'));
  assert.ok(rendered.includes('Energy'));
  assert.ok(!rendered.includes('First explanation.'));
});

test('repeated topic headings can be hidden without hiding subtopics or content', () => {
  const rendered = texts(exportsObject.StudySection({ section, showHeading: false }));
  assert.ok(!rendered.includes('Transport'));
  assert.ok(rendered.includes('Diffusion'));
  assert.ok(rendered.includes('No ATP required.'));
  const continued = texts(exportsObject.StudySection({ section: { ...section, subheading: '' }, showHeading: false }));
  assert.ok(continued.includes('Continued discussion'));
});

test('detail screen has no playback or reliability presentation path', () => {
  const detail = readFileSync(require.resolve('../app/class/[id].tsx'), 'utf8');
  assert.doesNotMatch(detail, /expo-audio|sourceLinks|review_items|output\.warnings|seekTo|output\.transcript/);
  assert.match(detail, /output\.notes\.title/);
});

test('nested bullets render indented details without losing qualifications or source privacy', () => {
  const tree = exportsObject.StudySection({ section: { heading: 'Transport', body: '', source_segment_ids: ['PRIVATE_ID'],
    bullets: ['ATP required', { text: 'Diffusion', children: ['Example: dye spreads.', 'Only under the stated conditions.', 'E = mc²'] }] } });
  const rendered = texts(tree);
  for (const value of ['ATP required', 'Diffusion', '◦', 'Example: dye spreads.', 'Only under the stated conditions.', 'E = mc²']) {
    assert.ok(rendered.includes(value));
  }
  assert.match(JSON.stringify(tree), /"marginLeft":24/);
  assert.doesNotMatch(JSON.stringify(tree), /PRIVATE_ID/);
});
