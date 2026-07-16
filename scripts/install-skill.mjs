#!/usr/bin/env node
import { cp, lstat, mkdir, readdir, rename, rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const SKILL_NAME = 'hierarchical-delivery-governance';
const VALID_TARGETS = new Set(['codex', 'claude', 'both']);
const VALID_SCOPES = new Set(['user', 'project']);

function valueAfter(argv, index, option) {
  const value = argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${option} 缺少参数值`);
  return value;
}

export function parseArgs(argv) {
  const options = { target: 'both', scope: 'user', dryRun: false, force: false };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const option = argv[index];
    if (seen.has(option)) throw new Error(`参数重复: ${option}`);
    seen.add(option);
    if (option === '--dry-run') options.dryRun = true;
    else if (option === '--force') options.force = true;
    else if (option === '--help') options.help = true;
    else if (option === '--target') options.target = valueAfter(argv, index++, option);
    else if (option === '--scope') options.scope = valueAfter(argv, index++, option);
    else if (option === '--project-root') options.projectRoot = valueAfter(argv, index++, option);
    else throw new Error(`未知参数: ${option}`);
  }
  if (!VALID_TARGETS.has(options.target)) throw new Error('--target 必须是 codex、claude 或 both');
  if (!VALID_SCOPES.has(options.scope)) throw new Error('--scope 必须是 user 或 project');
  if (options.scope === 'user' && options.projectRoot) throw new Error('--project-root 只能与 --scope project 一起使用');
  return options;
}

export function resolveTargets(options, runtime = {}) {
  const home = path.resolve(runtime.home ?? homedir());
  const env = runtime.env ?? process.env;
  const projectRoot = path.resolve(options.projectRoot ?? runtime.cwd ?? process.cwd());
  const selected = options.target === 'both' ? ['codex', 'claude'] : [options.target];
  return selected.map((host) => {
    let root;
    if (options.scope === 'project') {
      root = host === 'codex'
        ? path.join(projectRoot, '.agents', 'skills')
        : path.join(projectRoot, '.claude', 'skills');
    } else if (host === 'codex') {
      root = path.join(path.resolve(env.CODEX_HOME || path.join(home, '.codex')), 'skills');
    } else {
      root = path.join(home, '.claude', 'skills');
    }
    return { host, root, destination: path.join(root, SKILL_NAME) };
  });
}

async function maybeLstat(candidate) {
  try { return await lstat(candidate); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

async function assertNoSymlinkComponents(candidate) {
  const absolute = path.resolve(candidate);
  const parsed = path.parse(absolute);
  let cursor = parsed.root;
  for (const part of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const stat = await maybeLstat(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`拒绝符号链接路径: ${cursor}`);
  }
}

async function assertPlainSource(directory) {
  const rootStat = await maybeLstat(directory);
  if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) throw new Error(`Skill 源目录无效: ${directory}`);
  const pending = [directory];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const child = path.join(current, entry.name);
      const stat = await lstat(child);
      if (stat.isSymbolicLink()) throw new Error(`Skill 源目录包含符号链接: ${child}`);
      if (stat.isDirectory()) pending.push(child);
      else if (!stat.isFile()) throw new Error(`Skill 源目录包含非常规文件: ${child}`);
    }
  }
}

function assertDestination(root, destination) {
  const relative = path.relative(path.resolve(root), path.resolve(destination));
  if (relative !== SKILL_NAME || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`安装目标越界: ${destination}`);
  }
}

async function installOne(sourceDir, target, options) {
  assertDestination(target.root, target.destination);
  await assertNoSymlinkComponents(target.root);
  await assertNoSymlinkComponents(target.destination);
  const existing = await maybeLstat(target.destination);
  if (existing && (!existing.isDirectory() || existing.isSymbolicLink())) {
    throw new Error(`安装目标不是普通目录: ${target.destination}`);
  }
  if (existing && !options.force) throw new Error(`安装目标已存在，请使用 --force: ${target.destination}`);
  if (options.dryRun) return { ...target, action: existing ? 'replace' : 'create', dryRun: true };

  await mkdir(target.root, { recursive: true });
  await assertNoSymlinkComponents(target.root);
  const staging = path.join(target.root, `.${SKILL_NAME}.tmp-${randomUUID()}`);
  const backup = path.join(target.root, `.${SKILL_NAME}.backup-${randomUUID()}`);
  let movedExisting = false;
  try {
    await cp(sourceDir, staging, { recursive: true, force: false, errorOnExist: true });
    if (existing) {
      await rename(target.destination, backup);
      movedExisting = true;
    }
    try {
      await rename(staging, target.destination);
    } catch (error) {
      if (movedExisting) await rename(backup, target.destination);
      throw error;
    }
    if (movedExisting) await rm(backup, { recursive: true, force: true });
    return { ...target, action: existing ? 'replaced' : 'created', dryRun: false };
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
}

export async function installSkill(options, runtime = {}) {
  const sourceDir = path.resolve(runtime.sourceDir ?? fileURLToPath(new URL('../skills/hierarchical-delivery-governance', import.meta.url)));
  await assertPlainSource(sourceDir);
  const targets = resolveTargets(options, runtime);
  const results = [];
  for (const target of targets) results.push(await installOne(sourceDir, target, options));
  return { skill: SKILL_NAME, scope: options.scope, results };
}

const help = `安装分层交付治理 Skill\n\n用法:\n  node scripts/install-skill.mjs [选项]\n\n选项:\n  --target codex|claude|both   安装目标，默认 both\n  --scope user|project         安装范围，默认 user\n  --project-root <path>        项目级安装根目录\n  --dry-run                    只显示计划，不写入\n  --force                      安全替换已有安装\n  --help                       显示帮助\n`;

async function main() {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) { process.stdout.write(help); return; }
    const result = await installSkill(options);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`安装失败: ${error.message}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
