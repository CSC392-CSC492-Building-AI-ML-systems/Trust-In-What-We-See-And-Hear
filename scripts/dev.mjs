// Run the whole stack natively: `npm run dev` (after `npm run setup`).
//
// Makes sure the local Supabase stack is up, migrated and has its demo data (skipped with
// `npm run dev -- --no-demo-data`), then runs the frontend,
// backend and mock voice pipeline together with prefixed output. Ctrl+C stops all three; Supabase
// keeps running (`npm run supabase stop` stops it).
//
// Ports come from the environment or the optional root .env: BACKEND_PORT (default 8000) and
// PIPELINE_PORT (default 8001). The frontend always uses 3000: backend CORS and the Supabase auth
// redirect URLs expect it.

import { existsSync } from "node:fs";
import path from "node:path";
import {
  BACKEND_DIR,
  FRONTEND_DIR,
  LOCAL_PIPELINE_SECRET,
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
