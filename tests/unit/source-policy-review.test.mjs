import test from 'node:test';
import assert from 'node:assert/strict';
import * as fsPromises from 'node:fs/promises';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { normalizeBaselineInputPath, readBaselineSources } from '../../src/baseline/sources.mjs';

const forbiddenPaths = [
  'config/application-prod.yml',
  'config\\APPLICATION_PRE.properties',
  'nested/application.prod.yaml',
  'nested\\application_preprod.toml',
  'config/prod.env',
  'config/prod.yml',
  'config/prod-config.yml',
  'config/app-prod.yml',
  'config/staging.toml',
  'deploy/pre/settings.xml',
  'docs/application-production.json',
  'docs/application-preproduction.json',
  '.aws/credentials',
  '.aws-prod/config',
  '.aws-sdk/reference.md',
  '.aws./config',
  'home\\.AWS\\config',
  '.gnupg/private-keys-v1.d/key',
  '.git /config',
  'credentials/service.json',
  'config\\CREDENTIALS\\service.json',
  'config/credentials',
  'config/aws-credentials.json',
  'config/credentials-prod.json',
  'config/service-credential.json',
  'config/secrets.yml',
  'config/Secrets.production.json',
  'config/client-secret.json',
  'config/client-secrets.json',
  'config/service-secrets.yml',
  'config/passwords.yml',
  'config/service-token.json',
  'config/prod.yml.bak',
  'config/application-prod.yaml.old',
  'config/app-prod.cnf',
  'secrets-backup/reference.md',
  'keys/id_rsa',
  'keys/id_rsa.',
  'keys/id_rsa.bak',
  'keys\\ID_ED25519',
  'keys/id_ed25519_sk',
  'keys/id_ecdsa.pub',
  'private-keys/reference.md',
  'keys/service.private-key',
  'keys/service.pem',
  'keys/putty.ppk',
  '.env',
  'config\\.ENV.local',
  '.envrc',
  '.ssh/config',
  '.git/config',
  '.npmrc',
  '.pypirc',
  '.netrc',
  '.docker/config.json',
  '.kube/config',
  '.azure/accessTokens.json',
  'config/api-keys.json',
  'config/service-account.json',
  'config/keystore.jks',
  'credentials.csv',
  'config/secrets.txt',
];

for (const candidate of forbiddenPaths) {
  test(`source policy rejects ${candidate}`, () => {
    assert.throws(
      () => normalizeBaselineInputPath(candidate),
      { code: 'BASELINE_PATH_INVALID' },
    );
  });
}

const allowedPaths = [
  'docs/application-product.yml',
  'docs/application-preview.yml',
  'docs/application-profile.yml',
  'docs/product-roadmap.md',
  'docs/preparation-notes.md',
  'docs/preflight.md',
  'docs/reproduction-steps.md',
  'requirements/production-readiness.md',
  'requirements/preproduction-plan.md',
  'requirements/staging-rollout.md',
  'deploy/prod/reference.md',
  'deploy/pre/reference.md',
  'deploy/production/reference.md',
  'docs/release-preproduction-notes.md',
  'docs/credentialed-access.md',
  '.environment/reference.md',
  'src/secretariat.mjs',
  'src/secretion.mjs',
  'src/tokenizer.mjs',
  'src/passwordless.mjs',
  'docs/tokenizer-design.json',
  'docs/passwordless-login.yml',
  'keys/id_rsa-parser.mjs',
  'keys/public-key-guide.md',
  'requirements/credentials-rotation.md',
  'requirements/password-policy.md',
  'requirements/token-authentication.md',
  'requirements/client-secret-redaction.md',
  'requirements/private-key-handling.md',
  'requirements/secrets-management.md',
  'requirements/password-policy.yaml',
  'requirements/token-authentication.json',
  'requirements/credentials-rotation.yml',
  'requirements/client-secret-redaction.toml',
  'requirements/secrets-management.txt',
];

for (const candidate of allowedPaths) {
  test(`source policy allows ${candidate}`, () => {
    assert.equal(normalizeBaselineInputPath(candidate), candidate.replaceAll('\\', '/'));
  });
}

test('forbidden baseline inputs are rejected before their file handle is opened', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-source-policy-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const relative = '.npmrc';
  const sensitive = path.join(root, '.npmrc');
  await writeFile(sensitive, '# must not be read\n');

  let sensitiveOpenCalls = 0;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'open') {
        return async (value, ...args) => {
          if (path.resolve(String(value)) === sensitive) {
            sensitiveOpenCalls += 1;
            const error = new Error('forbidden file was opened');
            error.code = 'FORBIDDEN_FILE_OPENED';
            throw error;
          }
          return target.open(value, ...args);
        };
      }
      return Reflect.get(target, property, receiver);
    },
  });

  const error = await readBaselineSources({ root, baseline: relative, fs }).then(
    () => undefined,
    (caught) => caught,
  );
  assert.equal(error?.code, 'BASELINE_PATH_INVALID');
  assert.equal(sensitiveOpenCalls, 0);
});

test('a rejected supporting source prevents every baseline source from being opened', async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), 'gated-loop-source-list-policy-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'requirements'), { recursive: true });
  await mkdir(path.join(root, '.aws-prod'), { recursive: true });
  await writeFile(path.join(root, 'requirements', 'baseline.md'), '# safe baseline\n');
  await writeFile(path.join(root, '.aws-prod', 'config'), 'must not be read\n');

  let openCalls = 0;
  const fs = new Proxy(fsPromises, {
    get(target, property, receiver) {
      if (property === 'open') return async (...args) => {
        openCalls += 1;
        return target.open(...args);
      };
      return Reflect.get(target, property, receiver);
    },
  });

  await assert.rejects(
    () => readBaselineSources({
      root,
      baseline: 'requirements/baseline.md',
      sources: ['.aws-prod/config'],
      fs,
    }),
    { code: 'BASELINE_PATH_INVALID' },
  );
  assert.equal(openCalls, 0);
});
