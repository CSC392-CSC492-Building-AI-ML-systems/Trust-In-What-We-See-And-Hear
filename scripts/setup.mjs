// One-time setup for running everything natively: `npm run setup`.
//
// Installs dependencies, starts the local Supabase stack (applying migrations and loading the demo
// data) and creates backend/.env and frontend/tiwwsah_webapp/.env.local pointing at it. Safe to
// rerun: existing env files are left alone.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  BACKEND_DIR,
  FRONTEND_DIR,
  LOCAL_PIPELINE_SECRET,
  ROOT,
  checkDocker,
  checkNode,
  fail,
  has,
  loadRootEnv,
  npm,
  port,
  prepareSupabase,
  run,
  step,
  supabase,
  supabaseStatus,
} from "./lib.mjs";

checkNode();
loadRootEnv();
const backendPort = port("BACKEND_PORT", "8000");
const pipelinePort = port("PIPELINE_PORT", "8001");

if (!has("uv", ["--version"])) {
  fail("uv is required for the backend: https://docs.astral.sh/uv/getting-started/installation/");
}
checkDocker();

step("Installing dev tools (Supabase CLI, concurrently)");
npm(["ci"], { cwd: ROOT });

step("Installing frontend dependencies");
npm(["ci"], { cwd: FRONTEND_DIR });

step("Installing backend dependencies (uv installs Python 3.14 if needed)");
run("uv", ["sync"], { cwd: BACKEND_DIR });

step("Starting the local Supabase stack (the first run downloads its Docker images)");
prepareSupabase(supabase, { showStartOutput: true });
const status = supabaseStatus(supabase);

step("Creating env files");
writeEnvFromExample(path.join(BACKEND_DIR, ".env"), {
  SUPABASE_URL: status.API_URL,
  SUPABASE_PUBLISHABLE_KEY: status.PUBLISHABLE_KEY,
  SUPABASE_SECRET_KEY: status.SECRET_KEY,
  PIPELINE_WS_URL: `ws://localhost:${pipelinePort}`,
  PIPELINE_BACKEND_SECRET: LOCAL_PIPELINE_SECRET,
});
writeEnvFromExample(path.join(FRONTEND_DIR, ".env.local"), {
  NEXT_PUBLIC_SUPABASE_URL: status.API_URL,
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: status.PUBLISHABLE_KEY,
  NEXT_PUBLIC_API_URL: `http://localhost:${backendPort}`,
});

const ffmpeg = process.env.FFMPEG_PATH || "ffmpeg";
if (!has(ffmpeg, ["-version"])) {
  console.warn(
    `\n⚠ ffmpeg was not found (${ffmpeg}). Everything runs, but voice uploads fail with 503 until ` +
      "ffmpeg is installed and on PATH (or FFMPEG_PATH is set in backend/.env).",
  );
}

console.log("\n✔ Setup complete. Start everything with: npm run dev");

/** Copy `<file>.example`-style template to `file` with `values` filled in, unless `file` exists. */
function writeEnvFromExample(file, values) {
  const relative = path.relative(ROOT, file);
  if (existsSync(file)) {
    console.log(`  kept existing ${relative} (delete it and rerun to regenerate)`);
    return;
  }
  const example = path.join(path.dirname(file), ".env.example");
  let text = readFileSync(example, "utf8");
  for (const [key, value] of Object.entries(values)) {
    const line = new RegExp(`^${key}=.*$`, "m");
    if (!line.test(text)) fail(`${path.relative(ROOT, example)} has no ${key}= line.`);
    text = text.replace(line, `${key}=${value}`);
  }
  writeFileSync(file, text);
  console.log(`  created ${relative}`);
}
