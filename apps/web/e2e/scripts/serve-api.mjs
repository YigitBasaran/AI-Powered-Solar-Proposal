import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { parseArgs } from "node:util";

import { REPO_ROOT, ensureTmpDir, pipeExit, removeDatabase, requireFreePort, sqliteUrl } from "./lib.mjs";

/**
 * Launch one FastAPI stack for the E2E suite.
 *
 * Playwright's `webServer` owns the process, but three things have to happen
 * before uvicorn starts and none of them belong in a shell one-liner:
 *
 *   1. refuse to start if the port is taken (never attach to a stranger),
 *   2. give this stack its own SQLite file, deleted first so every run starts
 *      from an empty database,
 *   3. set the mode environment explicitly rather than inheriting `.env`.
 *
 * Usage: node serve-api.mjs --port 8100 --db e2e-primary.db --mode deterministic
 */

const { values } = parseArgs({
  options: {
    port: { type: "string" },
    db: { type: "string" },
    mode: { type: "string", default: "deterministic" },
  },
});

const port = Number(values.port);
const mode = values.mode;
if (!Number.isInteger(port)) throw new Error("--port is required");
if (!values.db) throw new Error("--db is required");

await requireFreePort(port, `${mode} API`);

const tmp = ensureTmpDir();
const dbPath = resolve(tmp, values.db);
removeDatabase(dbPath);

const apiRoot = resolve(REPO_ROOT, "apps", "api");
const python =
  process.env.E2E_PYTHON ??
  resolve(apiRoot, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

/** Settings shared by every E2E stack. */
const common = {
  APP_ENV: "test",
  LOG_LEVEL: "WARNING",
  DATABASE_URL: sqliteUrl(dbPath),
  API_BASE_URL: `http://127.0.0.1:${port}`,
  MAPS_MODE: "fixture",
  GOOGLE_MAPS_API_KEY: "",
};

/**
 * Tier A. Every external dependency is a committed fixture, so the same
 * inputs always give the same numbers.
 */
const deterministic = {
  ...common,
  WEB_BASE_URL: `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? 3100}`,
  PVGIS_MODE: "fixture",
  FX_MODE: "fixture",
  LLM_PROVIDER: "rules",
};

/**
 * `@live` overrides, opt-in and one at a time.
 *
 * The deterministic stack stays deterministic by default. Setting
 * `E2E_LIVE=pvgis,fx,llm` turns individual dependencies live so the tier C
 * specs have something real to talk to; anything not named stays on fixtures,
 * so a live run is never accidentally live in more ways than it says.
 */
const live = new Set((process.env.E2E_LIVE ?? "").split(",").map((s) => s.trim()));
if (live.has("pvgis")) deterministic.PVGIS_MODE = "live";
if (live.has("fx")) deterministic.FX_MODE = "live";
if (live.has("llm")) {
  deterministic.LLM_PROVIDER = "ollama";
  deterministic.OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434";
  deterministic.OLLAMA_MODEL = process.env.OLLAMA_MODEL ?? "qwen3.5:2b";
  // A 2B model on CPU is slow; a live test should wait for it rather than
  // record a timeout as a failure of the integration.
  deterministic.OLLAMA_TIMEOUT_SECONDS = process.env.OLLAMA_TIMEOUT_SECONDS ?? "120";
}

/**
 * Tier B. PVGIS and FX are called by the *backend*, so a browser route
 * interception cannot reach them — the only honest way to test the fallbacks
 * is a stack genuinely configured to fail.
 *
 * The hosts use the reserved `.invalid` TLD, which by definition never
 * resolves. That is a *connection* failure in ~200 ms, on every OS. An
 * unbound local port looked like the obvious choice but is not portable:
 * Windows drops the SYN instead of refusing it, so each attempt burned the
 * whole connect timeout and one analysis took 87 seconds.
 */
const degraded = {
  ...common,
  WEB_BASE_URL: `http://127.0.0.1:${process.env.E2E_DEGRADED_WEB_PORT ?? 3101}`,
  PVGIS_MODE: "live",
  PVGIS_BASE_URL: "http://pvgis.invalid/api/v5_3",
  PVGIS_TIMEOUT_SECONDS: "2",
  PVGIS_FALLBACK_ENABLED: "true",
  FX_MODE: "live",
  FX_BASE_URL: "http://fx.invalid/v2",
  FX_TIMEOUT_SECONDS: "2",
  FX_FALLBACK_ENABLED: "true",
  LLM_PROVIDER: "ollama",
  OLLAMA_BASE_URL: "http://ollama.invalid",
  OLLAMA_TIMEOUT_SECONDS: "2",
  LLM_FALLBACK_ENABLED: "true",
};

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)],
  {
    cwd: apiRoot,
    stdio: "inherit",
    env: { ...process.env, ...(mode === "degraded" ? degraded : deterministic) },
  },
);

console.log(`[e2e] ${mode} API on :${port}, database ${dbPath}`);
pipeExit(child);
