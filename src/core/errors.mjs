export class GatedLoopError extends Error {
  constructor(code, message, { exitCode = 1, details = {} } = {}) {
    super(message);
    this.name = 'GatedLoopError';
    this.code = code;
    this.exitCode = exitCode;
    this.details = details;
  }
}
