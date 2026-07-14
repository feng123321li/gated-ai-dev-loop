export const DEVELOPMENT_HANDOFF_FILE = 'development-handoff.md';
export const LEGACY_DEVELOPMENT_HANDOFF_FILE = 'handoff-to-claude.md';

export function handoffFileFromNames(names) {
  const matches = [DEVELOPMENT_HANDOFF_FILE, LEGACY_DEVELOPMENT_HANDOFF_FILE]
    .filter((name) => names.includes(name));
  return matches.length === 1 ? matches[0] : null;
}
