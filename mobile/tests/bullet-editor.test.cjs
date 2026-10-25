const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(require.resolve('../src/BulletEditor.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const api = {};
const element = (type, props) => ({ type, props });
vm.runInNewContext(compiled, { exports: api, require: name => {
  if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element };
  if (name === 'react-native') return { TextInput: 'TextInput', View: 'View' };
  if (name === './components') return { Button: 'Button' };
  if (name === './theme') return { ui: {} };
  throw Error(`Unexpected import ${name}`);
} });
function nodes(node) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap(nodes);
  return [node, ...nodes(node.props.children)];
}
test('editing a nested detail preserves multiline content and other bullets without mutation', () => {
  const value = ['Definition', { text: 'Example', children: ['E = mc²\nConditions apply.', 'Another detail'] }];
  let updated;
  const tree = api.BulletEditor({ value, disabled: false, onChange: next => { updated = next; } });
  const input = nodes(tree).find(n => n.props.accessibilityLabel === 'Bullet 2, detail 1');
  assert.equal(input.props.value, 'E = mc²\nConditions apply.');
  input.props.onChangeText('New equation\nQualification preserved.');
  assert.equal(updated[1].children[0], 'New equation\nQualification preserved.');
  assert.equal(updated[1].children[1], 'Another detail');
  assert.equal(value[1].children[0], 'E = mc²\nConditions apply.');
});
test('plain bullets can gain children and empty edits cannot be saved', () => {
  let updated;
  const tree = api.BulletEditor({ value: ['Definition'], disabled: false, onChange: next => { updated = next; } });
  nodes(tree).find(n => n.props.label === 'Add detail').props.onPress();
  assert.equal(updated[0].text, 'Definition');
  assert.equal(api.validBullets(updated), false);
  updated[0].children[0] = 'A useful explanation.';
  assert.equal(api.validBullets(updated), true);
  const edit = api.BulletEditor({ value: updated, disabled: false, onChange: next => { updated = next; } });
  nodes(edit).find(n => n.props.label === 'Remove detail').props.onPress();
  assert.equal(updated[0], 'Definition');
});
