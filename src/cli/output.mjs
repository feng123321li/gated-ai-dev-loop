const SENSITIVE = /^(stdout|stderr|env|.*token.*|.*key.*|.*secret.*|.*password.*)$/i;
export function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, SENSITIVE.test(key) ? '[REDACTED]' : redact(child)]));
  return value;
}
export function renderJson(value) { return `${JSON.stringify(redact(value))}\n`; }
export function renderError(error) { return `ERROR ${error.code}: ${error.message}\n`; }
