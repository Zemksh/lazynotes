const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function load(file, overrides = {}) {
  const compiled = ts.transpileModule(readFileSync(require.resolve(file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const exportsObject = {};
  const element = (type, props) => ({ type, props });
  vm.runInNewContext(compiled, { exports: exportsObject, require: name => {
    if (overrides[name]) return overrides[name];
    if (name === 'react') return {};
    if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element };
    if (name === 'react-native') return Object.fromEntries(['Text', 'View', 'Pressable', 'ActivityIndicator'].map(s => [s, s]));
    if (name === './theme') return { colors: {}, ui: {} };
    if (name === './api' || name === './storage') return {};
    if (name === './components') return {
      ErrorMessage: ({ message }) => element('Text', { children: message }),
      Button: props => element('Button', props),
    };
    throw Error(`Unexpected dependency ${name}`);
  } });
  return exportsObject;
}
function texts(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'number' || typeof node === 'string') return String(node);
  if (Array.isArray(node)) return node.map(texts).join(' ');
  if (typeof node.type === 'function') return texts(node.type(node.props));
  return texts(node.props.children);
}
const { InsightsContent } = load('../src/LectureInsights.tsx');
const data = {
  version: 2, mock: false,
  transcription: { method: 'rule_based', status: 'partial', total_passages: 8, analyzed_passages: 6,
    flagged_passages: 1, affected_sections: 2,
    passages: [{ text: 'The actual flagged passage.', reason: 'Speech heuristic.', headings: ['Energy'] }] },
  concepts: { method: 'tfidf_agglomerative', status: 'available', ungrouped_sections: 0,
    groups: [{ terms: ['diffusion', 'gradient'], section_indices: [0, 2], headings: ['Transport', 'Molecules'] },
      { terms: ['stars'], section_indices: [1], headings: ['Astronomy'] }] },
};

test('shows real counts, passages, discovered terms, and honest method labels', () => {
  const text = texts(InsightsContent({ data })).replace(/\s+/g, ' ');
  for (const part of ['Rule-based',
    'The actual flagged passage.', 'Energy', '1 flagged passage', '2 affected note sections',
    '6 of 8 passages analyzed', 'Partial analysis',
    'diffusion', 'gradient', 'Transport', 'Topic',
    '1 related group']) assert.ok(text.includes(part), part);
  assert.doesNotMatch(text, /not a trained|TF-IDF|predefined topic|STANDALONE SECTION|does not prove|before your local edits/);
  assert.doesNotMatch(text, /\d+%|accuracy:|confidence:/i);
});

test('missing, empty and mock diagnostics are never presented as reliable', () => {
  for (const status of ['unavailable', 'mock']) {
    const text = texts(InsightsContent({ data: { ...data, mock: status === 'mock',
      transcription: { ...data.transcription, status },
      concepts: { ...data.concepts, status: 'insufficient_text', groups: [] } } }));
    assert.match(text, /unavailable/i);
    assert.match(text, /No related groups found/);
    assert.doesNotMatch(text, /flagged passages|The actual flagged passage/);
    if (status === 'mock') assert.match(text, /not analysis of your recording/);
  }
  assert.match(texts(InsightsContent({ data: { ...data,
    transcription: { ...data.transcription, status: 'available', flagged_passages: 0, passages: [] } } })),
    /No passages flagged/);
});

test('tab controls expose selected state and switch to insights', () => {
  const { InsightTabs } = load('../src/InsightTabs.tsx');
  let selected = 'notes';
  let tree = InsightTabs({ value: selected, onChange: value => { selected = value; } });
  assert.equal(tree.props.accessibilityRole, 'tablist');
  assert.equal(tree.props.children[0].props.accessibilityState.selected, true);
  tree.props.children[1].props.onPress();
  tree = InsightTabs({ value: selected, onChange() {} });
  assert.equal(tree.props.children[1].props.accessibilityState.selected, true);
  assert.equal(tree.props.children[0].props.accessibilityState.selected, false);
});

function panelHarness(item, api) {
  let cursor = 0;
  const states = [], effects = [], patches = [];
  let refreshed = 0;
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!(i in states)) states[i] = initial;
      return [states[i], value => { states[i] = typeof value === 'function' ? value(states[i]) : value; }];
    },
    useEffect(effect) { effects.push(effect); },
  };
  const { LectureInsightsPanel } = load('../src/LectureInsights.tsx', {
    react, './api': api,
    './storage': { patchClass: async (id, patch) => { patches.push({ id, patch }); } },
  });
  return {
    render() { cursor = 0; return LectureInsightsPanel({ item, onSaved: async () => { refreshed++; } }); },
    mount() { return effects.shift()(); }, patches, refreshed: () => refreshed,
  };
}

test('cached insights render offline without network calls', () => {
  const harness = panelHarness({ id: 'local', insights: data }, {
    lectureInsights: () => { throw Error('Must not fetch cached insights'); },
  });
  assert.match(texts(harness.render()), /The actual flagged passage/);
  harness.mount();
});

test('loading saved backend insights persists them locally and refreshes the class', async () => {
  let calls = 0;
  const harness = panelHarness({ id: 'local', remoteId: 'remote' }, {
    lectureInsights: async () => { calls++; return data; }, ApiError: class extends Error {},
  });
  harness.render(); harness.mount();
  assert.match(texts(harness.render()), /Finding connections/);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(texts(harness.render()), /The actual flagged passage/);
  assert.equal(calls, 1);
  assert.equal(harness.patches[0].patch.insights, data);
  assert.equal(harness.refreshed(), 1);
});

test('expired server copy gives an actionable error without affecting notes', async () => {
  class ApiError extends Error { constructor(status) { super(); this.status = status; } }
  const harness = panelHarness({ id: 'local', remoteId: 'remote' }, {
    ApiError, lectureInsights: async () => { throw new ApiError(410); },
  });
  harness.render(); harness.mount();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(texts(harness.render()), /server copy has expired/);
  assert.equal(harness.patches.length, 0);
});

test('older cached insights refresh to the new clustering version', async () => {
  let calls = 0;
  const harness = panelHarness({ id: 'local', remoteId: 'remote', insights: { ...data, version: 1 } }, {
    ApiError: class extends Error {}, lectureInsights: async () => { calls++; return data; },
  });
  harness.render(); harness.mount();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.equal(harness.patches[0].patch.insights.version, 2);
});

test('study notes and insights have separate rendering paths', () => {
  const detail = readFileSync(require.resolve('../app/class/[id].tsx'), 'utf8');
  assert.match(detail, /tab === 'insights' \? <LectureInsightsPanel/);
  assert.doesNotMatch(readFileSync(require.resolve('../src/StudySection.tsx'), 'utf8'), /transcription|flagged_passages|ConceptGroup/);
});
