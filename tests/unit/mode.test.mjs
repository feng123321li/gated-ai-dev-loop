import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { normalizeSignals } from '../../src/mode/signals.mjs';
import { classifyMode } from '../../src/mode/classify.mjs';
import { recheckMode } from '../../src/mode/recheck.mjs';

const fixture = JSON.parse(await readFile(new URL('../fixtures/modes/classifier-cases.json', import.meta.url), 'utf8'));
const base = (patch = {}) => ({ ...structuredClone(fixture.base), ...patch });

test('normalization returns the complete stable input shape and canonical unique paths', () => {
  const normalized = normalizeSignals(base({ modifiesFiles: ['.\\src\\a.mjs', 'src/a.mjs', 'src/b.mjs'], migrations: ['storage', 'config', 'storage'] }));
  assert.deepEqual(Object.keys(normalized), [
    'description', 'modifiesFiles', 'writesFiles', 'loadBearing', 'breaking', 'migrations',
    'dependencyChange', 'newDependency', 'externalContract', 'permissions', 'authentication',
    'stateMachine', 'transaction', 'concurrency', 'idempotency', 'unresolvedOptions',
    'thresholdDecision', 'impactKnown', 'requestedMode',
  ]);
  assert.deepEqual(normalized.modifiesFiles, ['src/a.mjs', 'src/b.mjs']);
  assert.deepEqual(normalized.migrations, ['config', 'storage']);
  assert.equal(normalized.requestedMode, null);
});

test('normalization treats listed files as writes and rejects malformed structured signals', () => {
  assert.equal(normalizeSignals(base({ writesFiles: false, modifiesFiles: ['src/a.mjs'] })).writesFiles, true);
  assert.throws(() => normalizeSignals(null), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ writesFiles: 'yes' })), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ requestedMode: 'none' })), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ modifiesFiles: ['../escape'] })), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ migrations: ['mystery'] })), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ migrations: { mystery: false } })), { code: 'MODE_SIGNALS_INVALID' });
  assert.throws(() => normalizeSignals(base({ modifiesFiles: ['src/a.mjs\n- ../outside'] })), { code: 'MODE_SIGNALS_INVALID' });
  for (const filePath of ['src/**', 'src/file?.mjs', 'src/[ab].mjs', 'src/{a,b}.mjs']) {
    assert.throws(() => normalizeSignals(base({ modifiesFiles: [filePath] })), { code: 'MODE_SIGNALS_INVALID' });
  }
});

for (const entry of fixture.hardCases) {
  test(`hard filter routes ${entry.name} to Full separately`, () => {
    const result = classifyMode(base({ [entry.field]: entry.value }));
    assert.equal(result.mode, 'full');
    assert.deepEqual(result.reasons, [entry.reason]);
    assert.equal(result.confidence, 'high');
  });
}

for (const category of ['unspecified', 'database', 'schema', 'data', 'config', 'storage', 'api-version', 'dependency']) {
  test(`${category} migration routes to Full`, () => {
    assert.deepEqual(classifyMode(base({ migrations: [category] })).reasons, ['MIGRATION']);
  });
}

for (const entry of [
  { name: 'boolean', value: true, normalized: ['unspecified'] },
  { name: 'string', value: 'database', normalized: ['database'] },
  { name: 'flag mapping', value: { schema: false, database: true }, normalized: ['database'] },
  { name: 'deduplicated array', value: ['unspecified', 'database', 'database'], normalized: ['database', 'unspecified'] },
]) {
  test(`${entry.name} migration input normalizes and routes to Full`, () => {
    const result = classifyMode(base({ migrations: entry.value }));
    assert.equal(result.mode, 'full');
    assert.deepEqual(result.reasons, ['MIGRATION']);
    assert.deepEqual(result.evaluatedInputs.migrations, entry.normalized);
  });
}

for (const filePath of fixture.loadBearingPaths) {
  test(`known load-bearing path overrides a false caller flag: ${filePath}`, () => {
    const result = classifyMode(base({ modifiesFiles: [filePath], loadBearing: false }));
    assert.equal(result.mode, 'full');
    assert.deepEqual(result.reasons, ['LOAD_BEARING_FILE']);
    assert.equal(result.evaluatedInputs.loadBearing, true);
  });
}

test('three unique safe files stay Light and a fourth forces Full', () => {
  const three = ['src/a.mjs', 'src/b.mjs', 'tests/a.test.mjs'];
  assert.deepEqual(classifyMode(base({ modifiesFiles: [...three, 'src/a.mjs'] })).reasons, ['LIGHT_ELIGIBLE']);
  assert.deepEqual(classifyMode(base({ modifiesFiles: [...three, 'src/c.mjs'] })).reasons, ['FILE_LIMIT_EXCEEDED']);
});

test('questions are None only from an explicit no-write signal; typo and reorder writes are Light', () => {
  const securityWords = 'Should we change authentication and transaction behavior?';
  const question = classifyMode(base({ description: securityWords, modifiesFiles: [], writesFiles: false, impactKnown: true }));
  assert.deepEqual(question, { mode: 'none', reasons: ['NO_FILE_WRITES'], confidence: 'high', evaluatedInputs: question.evaluatedInputs });
  assert.equal(classifyMode(base({ description: 'Fix a typo', modifiesFiles: ['docs/readme.md'] })).mode, 'light');
  assert.equal(classifyMode(base({ description: 'Reorder documentation', modifiesFiles: ['docs/readme.md'] })).mode, 'light');
});

test('hard Full signals take precedence over a contradictory no-write claim', () => {
  const result = classifyMode(base({ modifiesFiles: [], writesFiles: false, breaking: true }));
  assert.equal(result.mode, 'full');
  assert.deepEqual(result.reasons, ['BREAKING_CHANGE']);
});

test('one unresolved option is safe but two require Full', () => {
  assert.equal(classifyMode(base({ unresolvedOptions: 1 })).mode, 'light');
  assert.deepEqual(classifyMode(base({ unresolvedOptions: 2 })).reasons, ['UNRESOLVED_OPTIONS']);
});

for (const filePath of fixture.ordinaryPaths) {
  test(`ordinary token lookalike is not mistaken for a load-bearing contract: ${filePath}`, () => {
    const result = classifyMode(base({ modifiesFiles: [filePath] }));
    assert.equal(result.mode, 'light');
    assert.equal(result.evaluatedInputs.loadBearing, false);
  });
}

test('free text never supplies or suppresses security signals', () => {
  const a = classifyMode(base({ description: 'authentication migration threshold breaking external API' }));
  const b = classifyMode(base({ description: 'tiny harmless typo' }));
  assert.equal(a.mode, b.mode);
  assert.deepEqual(a.reasons, b.reasons);
  assert.notEqual(a.evaluatedInputs.description, b.evaluatedInputs.description);
});

test('requested Full always wins and includes the stable user-forced reason', () => {
  const result = classifyMode(base({ modifiesFiles: [], writesFiles: false, requestedMode: 'full' }));
  assert.equal(result.mode, 'full');
  assert.deepEqual(result.reasons, ['USER_FORCED_FULL']);
  assert.equal(result.confidence, 'high');
});

test('requested Light cannot bypass hard filters and reports every sorted unique reason', () => {
  assert.throws(
    () => classifyMode(base({ requestedMode: 'light', breaking: true, loadBearing: true, modifiesFiles: ['SKILL.md', 'SKILL.md'] })),
    (error) => {
      assert.equal(error.code, 'MODE_ESCALATION_REQUIRED');
      assert.deepEqual(error.details, { requiredMode: 'full', reasons: ['BREAKING_CHANGE', 'LOAD_BEARING_FILE'] });
      return true;
    },
  );
});

test('requested Light cannot use a no-write claim to suppress a hard signal', () => {
  assert.throws(
    () => classifyMode(base({ modifiesFiles: [], writesFiles: false, requestedMode: 'light', permissions: true })),
    { code: 'MODE_ESCALATION_REQUIRED' },
  );
});

test('reason codes are unique and sorted independently of input order', () => {
  const result = classifyMode(base({ breaking: true, authentication: true, migrations: ['storage', 'config'], newDependency: true }));
  assert.deepEqual(result.reasons, ['AUTHENTICATION', 'BREAKING_CHANGE', 'MIGRATION', 'NEW_DEPENDENCY']);
});

test('unknown impact is conservatively Full with medium confidence', () => {
  const result = classifyMode(base({ impactKnown: false }));
  assert.deepEqual(result.reasons, ['IMPACT_UNKNOWN']);
  assert.equal(result.mode, 'full');
  assert.equal(result.confidence, 'medium');
});

test('missing impact knowledge is conservative for writes', () => {
  const input = base();
  delete input.impactKnown;
  assert.deepEqual(classifyMode(input).reasons, ['IMPACT_UNKNOWN']);
});

test('a declared write with no known paths fails closed to Full', () => {
  const result = classifyMode(base({ modifiesFiles: [], writesFiles: true }));
  assert.equal(result.mode, 'full');
  assert.deepEqual(result.reasons, ['WRITE_PATHS_UNKNOWN']);
  assert.equal(result.confidence, 'medium');
});

test('recheck keeps Full strict even when the actual diff is small', () => {
  const result = recheckMode({ initialMode: 'full', changedPaths: ['src/a.mjs'], detectedSignals: { impactKnown: true } });
  assert.equal(result.allowed, true);
  assert.equal(result.requiredMode, 'full');
});

test('recheck permits a Light diff of up to three safe files', () => {
  const result = recheckMode({ initialMode: 'light', changedPaths: ['src/a.mjs', 'src/b.mjs', 'tests/a.test.mjs'], detectedSignals: { impactKnown: true } });
  assert.deepEqual(result, { allowed: true, requiredMode: 'light', reasons: ['LIGHT_ELIGIBLE'] });
});

test('recheck escalates Light before review when actual paths or signals require Full', () => {
  let reviews = 0;
  const result = recheckMode({
    initialMode: 'light',
    changedPaths: ['src/a.mjs', 'src/b.mjs', 'src/c.mjs', 'src/d.mjs'],
    detectedSignals: { impactKnown: true, transaction: true },
    review: () => { reviews++; },
  });
  assert.deepEqual(result, {
    allowed: false,
    requiredMode: 'full',
    code: 'MODE_ESCALATION_REQUIRED',
    reasons: ['FILE_LIMIT_EXCEEDED', 'TRANSACTION'],
  });
  assert.equal(reviews, 0);
});

test('recheck detects load-bearing paths even when diff metadata says otherwise', () => {
  const result = recheckMode({ initialMode: 'light', changedPaths: ['nested/SKILL.md'], detectedSignals: { impactKnown: true, loadBearing: false } });
  assert.deepEqual(result.reasons, ['LOAD_BEARING_FILE']);
  assert.equal(result.allowed, false);
});

test('recheck requires explicit non-null actual changed paths instead of treating missing metadata as an empty diff', () => {
  for (const options of [
    { initialMode: 'light', detectedSignals: { impactKnown: true } },
    { initialMode: 'light', changedPaths: null, detectedSignals: { impactKnown: true } },
    { initialMode: 'light', actualChangedPaths: null, detectedSignals: { impactKnown: true } },
    { initialMode: 'light', actualChangedPaths: 'src/a.mjs', detectedSignals: { impactKnown: true } },
    { initialMode: 'light', changedPaths: [], actualChangedPaths: [], detectedSignals: { impactKnown: true } },
  ]) {
    assert.throws(() => recheckMode(options), { code: 'MODE_RECHECK_INVALID' });
  }
});

test('recheck accepts the actualChangedPaths alias when it is an explicit array', () => {
  assert.deepEqual(
    recheckMode({ initialMode: 'light', actualChangedPaths: [], detectedSignals: { impactKnown: true } }),
    { allowed: true, requiredMode: 'light', reasons: ['NO_FILE_WRITES'] },
  );
});

test('initial None recheck stays None only for an explicit empty diff and escalates every write', () => {
  assert.deepEqual(
    recheckMode({ initialMode: 'none', changedPaths: [], detectedSignals: { impactKnown: true } }),
    { allowed: true, requiredMode: 'none', reasons: ['NO_FILE_WRITES'] },
  );
  assert.deepEqual(
    recheckMode({ initialMode: 'none', changedPaths: ['src/a.mjs'], detectedSignals: { impactKnown: true } }),
    { allowed: false, requiredMode: 'light', code: 'MODE_ESCALATION_REQUIRED', reasons: ['LIGHT_ELIGIBLE'] },
  );
  assert.deepEqual(
    recheckMode({ initialMode: 'none', changedPaths: ['api/spec.yaml'], detectedSignals: { impactKnown: true } }),
    { allowed: false, requiredMode: 'full', code: 'MODE_ESCALATION_REQUIRED', reasons: ['LOAD_BEARING_FILE'] },
  );
  assert.deepEqual(
    recheckMode({ initialMode: 'none', changedPaths: [], detectedSignals: { impactKnown: true, migrations: 'database' } }),
    { allowed: false, requiredMode: 'full', code: 'MODE_ESCALATION_REQUIRED', reasons: ['MIGRATION'] },
  );
});

test('recheck two-argument form does not let diff metadata replace the trusted initial mode', () => {
  const result = recheckMode('light', {
    initialMode: 'full',
    changedPaths: ['nested/SKILL.md'],
    detectedSignals: { impactKnown: true },
  });
  assert.deepEqual(result, {
    allowed: false,
    requiredMode: 'full',
    code: 'MODE_ESCALATION_REQUIRED',
    reasons: ['LOAD_BEARING_FILE'],
  });
});

test('recheck rejects unsupported initial modes', () => {
  assert.throws(() => recheckMode({ initialMode: 'wat', changedPaths: [] }), { code: 'MODE_RECHECK_INVALID' });
});
