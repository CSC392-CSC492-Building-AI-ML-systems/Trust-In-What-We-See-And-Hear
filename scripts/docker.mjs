// Run the whole stack in Docker: `npm run docker`. Needs nothing installed besides Docker and Node.
//
// Starts the local Supabase stack with the CLI version pinned in package.json, applies new
// migrations and restores the demo data, then runs
//   docker compose up --build --watch --force-recreate
// with the stack's own keys.
//
// --force-recreate: Compose Watch copies edits into the running containers, so a container reused
// from an earlier session can still hold edits that were since undone on the host.

import { readFileSync } from "node:fs";
import path from "node:path";
import {
  ROOT,
  checkDocker,
  checkNode,
  fail,
  loadRootEnv,
  npm,
  parseOptions,
  port,
  prepareSupabase,
  run,
  step,
  supabaseStatus,
} from "./lib.mjs";

const MIN_COMPOSE = [2, 25];

checkNode();
const { demoData } = parseOptions();
loadRootEnv();
port("BACKEND_PORT", "8000");
port("PIPELINE_PORT", "8001");
checkDocker();
checkCompose();

const { devDependencies } = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8"));
const supabase = (args, options) =>
  npm(["exec", "--yes", "--", `supabase@${devDependencies.supabase}`, ...args], options);

step("Starting the local Supabase stack and applying new migrations (the first run downloads its Docker images)");
prepareSupabase(supabase, { demoData });
const status = supabaseStatus(supabase);

step("Starting the frontend, backend and pipeline mock (Ctrl+C stops them)");
run("docker", ["compose", "up", "--build", "--watch", "--force-recreate"], {
  env: {
    SUPABASE_PUBLISHABLE_KEY: status.PUBLISHABLE_KEY,
    SUPABASE_SECRET_KEY: status.SECRET_KEY,
  },
});

function checkCompose() {
  const required = `Docker Compose ${MIN_COMPOSE.join(".")} or newer is required`;
  const version = run("docker", ["compose", "version", "--short"], {
    capture: true,
    allowFailure: true,
  });
  if (version === null) fail(`${required}.`);
  const [major, minor] = version.trim().replace(/^v/, "").split(".").map(Number);
  if (major < MIN_COMPOSE[0] || (major === MIN_COMPOSE[0] && minor < MIN_COMPOSE[1])) {
    fail(`${required} (found ${version.trim()}).`);
  }
}
