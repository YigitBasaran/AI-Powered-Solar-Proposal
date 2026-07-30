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
  // Named, not inherited: several guards are only permitted in a test
  // environment and check this rather than trusting a flag. `ALLOW_REPLAY_
  // PROPOSALS` below is refused at start-up without it.
  APP_ENV: "test",
  LOG_LEVEL: "WARNING",
  DATABASE_URL: sqliteUrl(dbPath),
  API_BASE_URL: `http://127.0.0.1:${port}`,
  // Imagery has no fixture mode either: the API always makes a real HTTP
  // request. The stub answers it, and the calibration profile written beside
  // the stub is bound to that synthetic raster, so the verification guard runs
  // for real in E2E rather than being switched off.
  GOOGLE_STATIC_MAPS_BASE_URL: `http://127.0.0.1:${process.env.E2E_PVGIS_STUB_PORT ?? 8102}/maps/api/staticmap`,
  GOOGLE_MAPS_API_KEY: "",
  ROOF_CALIBRATION_PATH: stubCalibrationPath(),
  GOOGLE_MAPS_API_KEY: "",
};

/** Where the stub writes the calibration profile that matches its raster. */
function stubCalibrationPath() {
  return resolve(REPO_ROOT, "apps", "web", ".e2e-tmp", "stub-roof-calibration.json");
}

const stubPort = process.env.E2E_PVGIS_STUB_PORT ?? 8102;
const stubBase = `http://127.0.0.1:${stubPort}/api/v5_3`;

/**
 * Tier A. Maps and FX are committed fixtures; PVGIS is a **real HTTP call**
 * answered by the local replay stub, because the application has no fixture
 * mode any more. Same numbers, same transport as production.
 */
const deterministic = {
  ...common,
  WEB_BASE_URL: `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? 3100}`,
  PVGIS_BASE_URL: stubBase,
  // Short, because the stub is local: a slow response here means the stub did
  // not start, and that should fail fast rather than look like a slow PVGIS.
  PVGIS_TIMEOUT_SECONDS: "3",
  // The stub is not the canonical PVGIS origin, so its output is labelled
  // `replay` and is not proposal-grade. Named explicitly, and only ever on a
  // stack whose APP_ENV is `test`.
  ALLOW_REPLAY_PROPOSALS: "true",
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
if (live.has("pvgis")) {
  // Drop the override entirely so the application's own default - the
  // canonical JRC endpoint - applies. Tier C then genuinely cannot be served
  // by the stub, which is what makes its skip guard meaningful.
  delete deterministic.PVGIS_BASE_URL;
  delete deterministic.PVGIS_TIMEOUT_SECONDS;
  delete deterministic.ALLOW_REPLAY_PROPOSALS;
}
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
  // PVGIS points at the healthy stub, not at `.invalid`. Without production
  // figures there is no analysis at all, and then the FX and maps fallbacks -
  // which is what this tier exists to prove - have nothing to be asserted
  // against. The PVGIS-unavailable path gets its own stack below.
  PVGIS_BASE_URL: stubBase,
  PVGIS_TIMEOUT_SECONDS: "3",
  ALLOW_REPLAY_PROPOSALS: "true",
  FX_MODE: "live",
  FX_BASE_URL: "http://fx.invalid/v2",
  FX_TIMEOUT_SECONDS: "2",
  FX_FALLBACK_ENABLED: "true",
  LLM_PROVIDER: "ollama",
  OLLAMA_BASE_URL: "http://ollama.invalid",
  OLLAMA_TIMEOUT_SECONDS: "2",
  LLM_FALLBACK_ENABLED: "true",
};

/**
 * A stack whose PVGIS genuinely never answers.
 *
 * API only - no web server, so it costs about a second and no Next build. Its
 * whole job is to prove that an unavailable PVGIS fails the analysis honestly
 * and blocks finalisation, which is the behaviour that replaced the fixture
 * fallback.
 */
const pvgisDown = {
  ...common,
  WEB_BASE_URL: `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? 3100}`,
  PVGIS_BASE_URL: `http://127.0.0.1:${stubPort}/__fault/unavailable/api/v5_3`,
  PVGIS_TIMEOUT_SECONDS: "2",
  PVGIS_MAX_ATTEMPTS: "2",
  PVGIS_RETRY_BUDGET_SECONDS: "3",
  FX_MODE: "fixture",
  LLM_PROVIDER: "rules",
};

const ENVIRONMENTS = { deterministic, degraded, "pvgis-down": pvgisDown };
const chosen = ENVIRONMENTS[mode];
if (!chosen) throw new Error(`unknown --mode ${mode}; expected one of ${Object.keys(ENVIRONMENTS)}`);

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)],
  { cwd: apiRoot, stdio: "inherit", env: { ...process.env, ...chosen } },
);

console.log(`[e2e] ${mode} API on :${port}, database ${dbPath}`);
pipeExit(child);
