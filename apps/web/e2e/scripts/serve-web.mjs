import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";

import { WEB_ROOT, pipeExit, requireFreePort, waitForUrl } from "./lib.mjs";

/**
 * Launch one Next.js server for the E2E suite.
 *
 * The suite runs against the **production** bundle, not `next dev`. That is
 * what a reviewer would deploy, it is the artefact the dependency-containment
 * check inspects, and dev-mode Fast Refresh would add console noise the console
 * guard would then have to excuse.
 *
 * Each stack needs its own build. Next evaluates `rewrites()` during
 * `next build` and freezes the result into `routes-manifest.json`, so a build
 * is permanently bound to one API origin — the degraded stack therefore builds
 * into its own `distDir` rather than sharing one output.
 *
 * Playwright starts webServer entries concurrently, so the second server waits
 * on the *first server's URL* before building. Two full Next builds racing for
 * the same cores would only make both slower.
 *
 * Usage:
 *   node serve-web.mjs --port 3100 --api http://127.0.0.1:8100 --dist .next
 *   node serve-web.mjs --port 3101 --api http://127.0.0.1:8101 \
 *                      --dist .next-degraded --after http://127.0.0.1:3100
 */

const { values } = parseArgs({
  options: {
    port: { type: "string" },
    api: { type: "string" },
    dist: { type: "string", default: ".next" },
    after: { type: "string" },
  },
});

const port = Number(values.port);
if (!Number.isInteger(port)) throw new Error("--port is required");
if (!values.api) throw new Error("--api is required");

await requireFreePort(port, "web server");

if (values.after) {
  console.log(`[e2e] waiting for ${values.after} before building…`);
  await waitForUrl(values.after, 420_000, "the primary web server");
}

const nextBin = resolve(WEB_ROOT, "node_modules", "next", "dist", "bin", "next");
const env = { ...process.env, API_BASE_URL: values.api, NEXT_DIST_DIR: values.dist };

// `E2E_SKIP_BUILD=1` is for tight iteration on the specs themselves. It is
// never used in a verification run: a stale bundle would make the results a
// statement about code that is no longer there.
const built = existsSync(resolve(WEB_ROOT, values.dist, "BUILD_ID"));
if (process.env.E2E_SKIP_BUILD === "1" && built) {
  console.log(`[e2e] E2E_SKIP_BUILD=1 — reusing the existing ${values.dist} build`);
} else {
  console.log(`[e2e] building ${values.dist} against ${values.api}…`);
  const build = spawnSync(process.execPath, [nextBin, "build"], {
    cwd: WEB_ROOT,
    stdio: "inherit",
    env,
  });
  if (build.status !== 0) process.exit(build.status ?? 1);
}

const child = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
  cwd: WEB_ROOT,
  stdio: "inherit",
  env: { ...env, PORT: String(port) },
});

console.log(`[e2e] web on :${port} (${values.dist}) proxying /api/v1 -> ${values.api}`);
pipeExit(child);
