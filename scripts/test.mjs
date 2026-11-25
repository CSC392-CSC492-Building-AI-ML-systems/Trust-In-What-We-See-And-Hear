// Run the backend's and the voice pipeline's tests: `npm test`.
//
// Both run in the backend's environment (uv creates or updates it as needed) and need no Docker,
// Supabase or GPU. The second suite runs even if the first fails, so one run shows every failure.
//
// AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

import { BACKEND_DIR, checkNode, fail, has, run, step } from "./lib.mjs";

checkNode();
if (!has("uv", ["--version"])) {
  fail("uv is required for the tests: https://docs.astral.sh/uv/getting-started/installation/");
}

const suites = [
  ["Backend", ["run", "pytest"]],
  ["Voice pipeline", ["run", "pytest", "../voice-pipeline/tests"]],
];
const failed = [];
for (const [name, args] of suites) {
  step(`${name} tests`);
  if (run("uv", args, { cwd: BACKEND_DIR, allowFailure: true }) === null) failed.push(name);
}

if (failed.length) fail(`Tests failed: ${failed.join(", ")}.`);
console.log("\n✔ All tests passed.");
