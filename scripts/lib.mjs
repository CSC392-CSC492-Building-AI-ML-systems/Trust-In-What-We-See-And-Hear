// Shared helpers for scripts/setup.mjs and scripts/dev.mjs.
//
// Commands are spawned without a shell: npm and the Supabase CLI are run as Node scripts through
// process.execPath, because Windows cannot spawn npm.cmd/npx.cmd directly, and a shell would make
// quoting depend on the OS. Paths with spaces are passed as `cwd`, never interpolated.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const FRONTEND_DIR = path.join(ROOT, "frontend", "tiwwsah_webapp");
export const BACKEND_DIR = path.join(ROOT, "backend");
export const PIPELINE_DIR = path.join(ROOT, "voice-pipeline");

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

/** Load the optional, gitignored root .env (ports). Variables already set in the shell win. */
export function loadRootEnv() {
  const file = path.join(ROOT, ".env");
  if (existsSync(file)) process.loadEnvFile(file);
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

/** Run a command to completion, streaming its output. Exits the script if it fails. */
export function run(command, args, { cwd = ROOT, env, capture = false } = {}) {
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

export function checkDocker() {
  if (!has("docker", ["info"])) {
    fail(
      "Docker is not running (the local Supabase stack runs in Docker). " +
        "Start Docker Desktop, or the Docker daemon, and try again.",
    );
  }
}
