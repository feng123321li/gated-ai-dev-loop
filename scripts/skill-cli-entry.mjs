import { runHierarchicalCli } from '../src/cli/main.mjs';

process.exitCode = await runHierarchicalCli(process.argv.slice(2));
