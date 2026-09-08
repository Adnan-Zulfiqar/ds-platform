/**
 * Serve the Next.js standalone build for Playwright / local e2e.
 *
 * `next start` warns and misbehaves when `output: "standalone"` is set
 * (see TECHNICAL_DEBT M13). Docker copies static assets beside `server.js`;
 * this script does the same for the host, then binds PORT (default 3000).
 */

import { cpSync, existsSync, mkdirSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const distDir = process.env.NEXT_DIST_DIR ?? ".next";
const standalone = path.join(root, distDir, "standalone");
const serverJs = path.join(standalone, "server.js");
const staticSrc = path.join(root, distDir, "static");
const staticDest = path.join(standalone, distDir, "static");
const publicSrc = path.join(root, "public");
const publicDest = path.join(standalone, "public");

if (!existsSync(serverJs)) {
  console.error(`Missing ${distDir}/standalone/server.js — run \`npm run build\` first.`);
  process.exit(1);
}
if (!existsSync(staticSrc)) {
  console.error(`Missing ${distDir}/static — run \`npm run build\` first.`);
  process.exit(1);
}

mkdirSync(path.dirname(staticDest), { recursive: true });
cpSync(staticSrc, staticDest, { recursive: true });
if (existsSync(publicSrc)) {
  cpSync(publicSrc, publicDest, { recursive: true });
}

const child = spawn(process.execPath, ["server.js"], {
  cwd: standalone,
  stdio: "inherit",
  env: {
    ...process.env,
    PORT: process.env.PORT ?? "3000",
    HOSTNAME: process.env.HOSTNAME ?? "0.0.0.0",
  },
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
