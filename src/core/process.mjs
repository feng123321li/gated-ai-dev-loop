import { spawn as nodeSpawn } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { GatedLoopError } from './errors.mjs';

const LIMIT = 64 * 1024;
function collector() {
  const chunks = []; let size = 0;
  return {
    add(chunk) {
      const bytes = Buffer.from(chunk); const remaining = LIMIT - size;
      if (remaining > 0) { chunks.push(bytes.subarray(0, remaining)); size += Math.min(bytes.length, remaining); }
    },
    text() {
      // write(), without end(), deliberately omits an incomplete trailing codepoint.
      const decoded = new StringDecoder('utf8').write(Buffer.concat(chunks));
      if (Buffer.byteLength(decoded) <= LIMIT) return decoded;
      let result = ''; let bytes = 0;
      for (const codepoint of decoded) {
        const length = Buffer.byteLength(codepoint);
        if (bytes + length > LIMIT) break;
        result += codepoint; bytes += length;
      }
      return result;
    },
  };
}

export function runProcess(file, args, { spawn = nodeSpawn, timeoutMs = 0, signal, cwd, env } = {}) {
  if (signal?.aborted) return Promise.reject(new GatedLoopError('PROCESS_ABORTED', `Process aborted: ${file}`));
  return new Promise((resolve, reject) => {
    let settled = false; let timer; let child; let abortRequested = false; let killed = false;
    const out = collector(); const err = collector();
    const finish = (fn, value) => { if (settled) return; settled = true; clearTimeout(timer); signal?.removeEventListener('abort', abort); fn(value); };
    const kill = () => { if (child && !killed) { killed = true; child.kill(); } };
    const abort = () => {
      abortRequested = true;
      kill();
      finish(reject, new GatedLoopError('PROCESS_ABORTED', `Process aborted: ${file}`));
    };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      child = spawn(file, args, { shell: false, cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      finish(reject, new GatedLoopError('PROCESS_SPAWN_FAILED', `Unable to start process: ${file}`, { details: { cause: error.message } }));
      return;
    }
    if (abortRequested || signal?.aborted) { abort(); return; }
    child.stdout?.on('data', (chunk) => out.add(chunk)); child.stderr?.on('data', (chunk) => err.add(chunk));
    if (timeoutMs > 0) timer = setTimeout(() => { kill(); finish(reject, new GatedLoopError('PROCESS_TIMEOUT', `Process timed out: ${file}`, { details: { timeoutMs, stdout: out.text(), stderr: err.text() } })); }, timeoutMs);
    child.on('error', (error) => finish(reject, new GatedLoopError('PROCESS_SPAWN_FAILED', `Unable to start process: ${file}`, { details: { cause: error.message } })));
    child.on('close', (exitCode, exitSignal) => exitCode === 0
      ? finish(resolve, { exitCode, signal: exitSignal })
      : finish(reject, new GatedLoopError('PROCESS_FAILED', `Process failed: ${file}`, { details: { exitCode, signal: exitSignal, stdout: out.text(), stderr: err.text() } })));
  });
}
