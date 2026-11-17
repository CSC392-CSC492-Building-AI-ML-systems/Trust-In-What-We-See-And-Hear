// Run the whole stack natively: `npm run dev` (after `npm run setup`).
//
// Makes sure the local Supabase stack is up, then runs the frontend, backend and mock voice
// pipeline together with prefixed output. Ctrl+C stops all three; Supabase keeps running
// (`npm run supabase stop` stops it).
//
// Ports come from the environment or the optional root .env: BACKEND_PORT (default 8000) and
// PIPELINE_PORT (default 8001). The frontend always uses 3000: backend CORS and the Supabase auth
// redirect URLs expect it.

import { existsSync } from "node:fs";
import path from "node:path";
import { BACKEND_DIR, FRONTEND_DIR, checkDocker, checkNode, fail, loadRootEnv, step, supabase } from "./lib.mjs";

checkNode();
loadRootEnv();
const backendPort = port("BACKEND_PORT", "8000");
const pipelinePort = port("PIPELINE_PORT", "8001");

for (const required of [path.join(FRONTEND_DIR, "node_modules"), path.join(BACKEND_DIR, ".venv")]) {
  if (!existsSync(required)) fail("Dependencies are missing. Run `npm run setup` first.");
}
checkDocker();

step("Starting the local Supabase stack (no-op if it is already running)");
supabase(["start"], { capture: true });

const { default: concurrently } = await import("concurrently");

step(
  `Frontend http://localhost:3000 · API http://localhost:${backendPort}/docs · ` +
    `Pipeline mock http://localhost:${pipelinePort} · Supabase Studio http://127.0.0.1:54323`,
);
const { result } = concurrently(
  [
    {
      name: "web",
      command: "npm run dev",
      cwd: FRONTEND_DIR,
      // Wins over .env.local, so the frontend always calls the backend started here.
      env: { NEXT_PUBLIC_API_URL: `http://localhost:${backendPort}` },
      prefixColor: "cyan",
    },
    {
      name: "api",
      command: `uv run fastapi dev --port ${backendPort}`,
      cwd: BACKEND_DIR,
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

function port(name, fallback) {
  const value = process.env[name] || fallback;
  if (!/^\d{1,5}$/.test(value) || Number(value) > 65535) fail(`${name} must be a port number.`);
  return value;
}
