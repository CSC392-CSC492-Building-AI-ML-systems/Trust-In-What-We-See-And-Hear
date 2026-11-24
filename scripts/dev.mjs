// Run the whole stack natively: `npm run dev` (after `npm run setup`).
//
// Makes sure the local Supabase stack is up, migrated and has its demo data (skipped with
// `npm run dev -- --no-demo-data`), then runs the frontend, backend and mock voice pipeline
// together with prefixed output. Ctrl+C stops all three; Supabase keeps running
// (`npm run supabase stop` stops it).
//
// Ports come from the environment or the optional root .env: BACKEND_PORT (default 8000) and
// PIPELINE_PORT (default 8001). The frontend always uses 3000: backend CORS and the Supabase auth
// redirect URLs expect it.
//
// AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";
import {
  BACKEND_DIR,
  FRONTEND_DIR,
  LOCAL_PIPELINE_SECRET,
  ROOT,
  checkDocker,
  checkNode,
  checkPortsFree,
  fail,
  loadRootEnv,
  parseOptions,
  port,
  prepareSupabase,
  step,
  supabase,
} from "./lib.mjs";

checkNode();
const { demoData } = parseOptions();
loadRootEnv();
const backendPort = port("BACKEND_PORT", "8000");
const pipelinePort = port("PIPELINE_PORT", "8001");

for (const required of [
  path.join(FRONTEND_DIR, "node_modules"),
  path.join(FRONTEND_DIR, ".env.local"),
  path.join(BACKEND_DIR, ".venv"),
  path.join(BACKEND_DIR, ".env"),
]) {
  if (!existsSync(required)) fail(`${required} is missing. Run \`npm run setup\` first.`);
}
const devPorts = { frontend: "3000", BACKEND_PORT: backendPort, PIPELINE_PORT: pipelinePort };
await checkPortsFree(devPorts);
checkDocker();

step("Starting the local Supabase stack and applying new migrations");
prepareSupabase(supabase, { demoData });
const localApiUrl = localSupabaseUrl();
if (localApiUrl) {
  warnIfNotLocal(path.join(BACKEND_DIR, ".env"), "SUPABASE_URL");
  warnIfNotLocal(path.join(FRONTEND_DIR, ".env.local"), "NEXT_PUBLIC_SUPABASE_URL");
}
// Again, in case Supabase has just taken one of them.
await checkPortsFree(devPorts);

const { default: concurrently } = await import("concurrently");

step(
  `Frontend http://localhost:3000 · API http://localhost:${backendPort}/docs · ` +
    `Pipeline mock http://localhost:${pipelinePort} · Supabase Studio http://127.0.0.1:54323`,
);
const { result } = concurrently(
  [
    {
      name: "web",
      // An explicit port: plain `next dev` honours an inherited PORT and silently moves to another
      // port when 3000 is taken, which CORS and the auth redirects would reject.
      command: "npm run dev -- --port 3000",
      cwd: FRONTEND_DIR,
      env: {
        // Wins over .env.local, so the frontend always calls the backend started here.
        NEXT_PUBLIC_API_URL: `http://localhost:${backendPort}`,
        // As in the frontend Dockerfile. It also stops `next dev` rewriting the tracked AGENTS.md
        // with its agent feedback block on every start.
        NEXT_TELEMETRY_DISABLED: "1",
      },
      prefixColor: "cyan",
    },
    {
      name: "api",
      command: `uv run fastapi dev --port ${backendPort}`,
      cwd: BACKEND_DIR,
      // Win over backend/.env, so calls always go to the mock started here.
      env: {
        PIPELINE_WS_URL: `ws://localhost:${pipelinePort}`,
        PIPELINE_BACKEND_SECRET: LOCAL_PIPELINE_SECRET,
      },
      prefixColor: "green",
    },
    {
      name: "pipeline",
      command:
        `uv run uvicorn --app-dir ../voice-pipeline mock_server:app --port ${pipelinePort} ` +
        "--reload --reload-dir ../voice-pipeline",
      cwd: BACKEND_DIR,
      prefixColor: "magenta",
    },
  ],
  { killOthersOn: ["failure", "success"], restartTries: 0 },
);

try {
  await result;
} catch {
  // One of the processes exited with an error (already shown above), or Ctrl+C was pressed.
  process.exitCode = 1;
}

/** The local stack's API URL, or null if `supabase status` cannot say (then nothing is checked). */
function localSupabaseUrl() {
  const output = supabase(["status", "--output", "json"], { capture: true, allowFailure: true });
  try {
    return JSON.parse(output).API_URL || null;
  } catch {
    return null;
  }
}

/**
 * Warn when an env file points at a Supabase other than the local stack, e.g. one written for the
 * hosted project before `npm run setup` existed (setup keeps existing env files). Pointing at the
 * hosted project on purpose is allowed, so this only warns. It reads only this file: a value set in
 * the shell, or a frontend .env.development.local, would win over it.
 */
function warnIfNotLocal(file, key) {
  const value = parseEnv(readFileSync(file, "utf8"))[key] ?? "";
  if (sameServer(value, localApiUrl)) return;
  console.warn(
    `\n⚠ ${path.relative(ROOT, file)} has ${key}=${value || "(empty)"}, not the local stack ` +
      `(${localApiUrl}). Unless you meant to use another Supabase project, delete the file and run ` +
      "`npm run setup` again. The backend and frontend env files must point at the same project, " +
      "or every API call fails with 401.",
  );
}

function sameServer(a, b) {
  const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]);
  const key = (text) => {
    const url = new URL(text);
    const host = loopback.has(url.hostname) ? "loopback" : url.hostname;
    return `${url.protocol}//${host}:${url.port}`;
  };
  try {
    return key(a) === key(b);
  } catch {
    return false; // not a URL, e.g. empty
  }
}
