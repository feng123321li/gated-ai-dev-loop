import { runHierarchicalCli } from '../src/cli/hierarchical.mjs';

process.exitCode = await runHierarchicalCli(process.argv.slice(2));
