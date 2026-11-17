// Run the whole stack in Docker: `npm run docker`. Needs nothing installed besides Docker and Node.
//
// Equivalent to:
//   npx --yes supabase@<version pinned in package.json> start
//   docker compose up --build --watch --force-recreate
//
// --force-recreate: Compose Watch copies edits into the running containers, so a container reused
// from an earlier session can still hold edits that were since undone on the host.

import { readFileSync } from "node:fs";
import path from "node:path";
import { ROOT, checkDocker, checkNode, fail, has, loadRootEnv, npm, port, run, step } from "./lib.mjs";

checkNode();
loadRootEnv();
port("BACKEND_PORT", "8000");
port("PIPELINE_PORT", "8001");
checkDocker();
if (!has("docker", ["compose", "version"])) fail("Docker Compose is required (2.25 or newer).");

const { devDependencies } = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8"));

step("Starting the local Supabase stack (the first run downloads its Docker images)");
npm(["exec", "--yes", "--", `supabase@${devDependencies.supabase}`, "start"], { capture: true });

step("Starting the frontend, backend and pipeline mock (Ctrl+C stops them)");
run("docker", ["compose", "up", "--build", "--watch", "--force-recreate"]);
