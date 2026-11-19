// Shared helpers for scripts/setup.mjs, scripts/dev.mjs and scripts/docker.mjs.
//
// Commands are spawned without a shell: npm and the Supabase CLI are run as Node scripts through
// process.execPath, because Windows cannot spawn npm.cmd/npx.cmd directly, and a shell would make
// quoting depend on the OS. Paths with spaces are passed as `cwd`, never interpolated.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const FRONTEND_DIR = path.join(ROOT, "frontend", "tiwwsah_webapp");
export const BACKEND_DIR = path.join(ROOT, "backend");
export const PIPELINE_DIR = path.join(ROOT, "voice-pipeline");

// What the backend expects from the local mock pipeline. Matches compose.yaml. The mock never calls
// the backend, so this value protects nothing; the backend only needs one to accept calls.
export const LOCAL_PIPELINE_SECRET = "local-dev-pipeline-secret";

const MIN_NODE = [22, 13];

export function fail(message) {
  console.error(`\n✖ ${message}`);
  process.exit(1);
}

export function step(message) {
  console.log(`\n▶ ${message}`);
}

export function checkNode() {
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < MIN_NODE[0] || (major === MIN_NODE[0] && minor < MIN_NODE[1])) {
    fail(`Node ${MIN_NODE.join(".")} or newer is required (found ${process.versions.node}).`);
  }
}

/**
 * Read the ports from the optional, gitignored root .env. Variables already set in the shell win.
 * Nothing else is loaded, so stray settings there cannot reach the servers started from here.
 */
export function loadRootEnv() {
  const file = path.join(ROOT, ".env");
  if (!existsSync(file)) return;
  const values = parseEnv(readFileSync(file, "utf8"));
  for (const name of ["BACKEND_PORT", "PIPELINE_PORT"]) {
    if (values[name] !== undefined) process.env[name] ??= values[name];
  }
}

/** A port from the environment (or `fallback`), validated as 1-65535. */
export function port(name, fallback) {
  const value = process.env[name] || fallback;
  const number = Number(value);
  if (!/^\d+$/.test(value) || number < 1 || number > 65535) {
    fail(`${name} must be a port number from 1 to 65535 (got "${value}").`);
  }
  return value;
}

/**
 * Run a command to completion, streaming its output unless `capture` is set. Exits the script if it
 * fails, or returns null with `allowFailure`.
 */
export function run(command, args, { cwd = ROOT, env, capture = false, allowFailure = false } = {}) {
  const result = spawnSync(command, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: capture ? ["ignore", "pipe", "inherit"] : "inherit",
    encoding: "utf8",
  });
  if (result.error?.code === "ENOENT") {
    fail(`\`${path.basename(command)}\` was not found. Install it and make sure it is on PATH.`);
  }
  if (result.error) fail(`${command} failed: ${result.error.message}`);
  if (result.status !== 0) {
    // The Supabase CLI can report errors on stdout, which would otherwise be lost.
    if (capture && result.stdout) process.stderr.write(result.stdout);
    if (allowFailure) return null;
    fail(`\`${[path.basename(command), ...args].join(" ")}\` exited with code ${result.status}.`);
  }
  return result.stdout;
}

/** Whether a program can be started, e.g. `has("uv", ["--version"])`. */
export function has(command, args) {
  const result = spawnSync(command, args, { stdio: "ignore" });
  return !result.error && result.status === 0;
}

function npmCli() {
  // Set by npm for `npm run ...`; points at npm's own JS entry point.
  const cli = process.env.npm_execpath;
  if (!cli || !cli.endsWith(".js")) {
    fail("Run this through npm, e.g. `npm run setup` or `npm run dev`.");
  }
  return cli;
}

export function npm(args, options) {
  return run(process.execPath, [npmCli(), ...args], options);
}

/** The pinned Supabase CLI from the root node_modules (installed by `npm run setup`). */
export function supabaseCli() {
  try {
    return createRequire(path.join(ROOT, "package.json")).resolve("supabase/dist/supabase.js");
  } catch {
    fail("The Supabase CLI is not installed. Run `npm run setup` first.");
  }
}

export function supabase(args, options) {
  return run(process.execPath, [supabaseCli(), ...args], options);
}

/**
 * Start the local stack, apply migrations it has not seen (e.g. after a pull) and restore the demo
 * data. `cli` runs the Supabase CLI. Migration or demo data problems only warn: a database that is
 * ahead of this branch after switching branches is normal and should not block starting.
 */
export function prepareSupabase(cli, { showStartOutput = false } = {}) {
  cli(["start"], { capture: !showStartOutput });
  if (cli(["migration", "up", "--include-all"], { capture: true, allowFailure: true }) === null) {
    console.warn(
      "\n⚠ Could not apply the database migrations (see above). If you switched branches, " +
        "`supabase db reset` rebuilds the local database from this branch.",
    );
  }
  const demoData = ["db", "query", "--file", path.join("supabase", "local-dev-data.sql")];
  if (cli(demoData, { capture: true, allowFailure: true }) === null) {
    console.warn("\n⚠ Could not load the local demo data (see above).");
  }
}

/** The running local stack's URL and keys, from `supabase status`. */
export function supabaseStatus(cli) {
  const status = JSON.parse(cli(["status", "--output", "json"], { capture: true }));
  for (const key of ["API_URL", "PUBLISHABLE_KEY", "SECRET_KEY"]) {
    if (!status[key]) fail(`\`supabase status\` did not report ${key}.`);
  }
  return status;
}

export function checkDocker() {
  if (!has("docker", ["info"])) {
    fail(
      "Docker is not running (the local Supabase stack runs in Docker). " +
        "Start Docker Desktop, or the Docker daemon, and try again.",
    );
  }
}
