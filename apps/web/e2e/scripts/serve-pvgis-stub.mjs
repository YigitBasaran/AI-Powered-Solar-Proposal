import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { parseArgs } from "node:util";

import { REPO_ROOT, pipeExit, requireFreePort } from "./lib.mjs";

/**
 * Launch the PVGIS replay stub.
 *
 * The application has no fixture mode: it always makes a real HTTP request to
 * whatever `PVGIS_BASE_URL` names. Pointing that at this process is what keeps
 * the suite offline and deterministic while still exercising the transport,
 * the retry ladder and the parser exactly as production does.
 *
 * One process serves every stack. That is safe because the stub holds no
 * fault state - a fault is a URL path, so tier A, the degraded stack and the
 * pvgis-down stack all point here and each gets its own behaviour.
 *
 * The implementation lives in `apps/api/tests/support/pvgis_stub.py`, under
 * `tests/`, so it is unambiguously test infrastructure and never ships.
 *
 * Usage: node serve-pvgis-stub.mjs --port 8102
 */

const { values } = parseArgs({ options: { port: { type: "string" } } });

const port = Number(values.port);
if (!Number.isInteger(port)) throw new Error("--port is required");

await requireFreePort(port, "PVGIS replay stub");

const apiRoot = resolve(REPO_ROOT, "apps", "api");

// The stub writes a roof calibration bound to the raster it will serve. The
// committed profile is bound to Google's imagery, which is deliberately not in
// this repository, so an offline stack needs its own - and gets to exercise the
// verification guard for real rather than switching it off.
const calibrationPath = resolve(REPO_ROOT, "apps", "web", ".e2e-tmp", "stub-roof-calibration.json");
const python =
  process.env.E2E_PYTHON ??
  resolve(apiRoot, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

const child = spawn(python, ["-m", "tests.support.pvgis_stub", "--port", String(port), "--write-calibration", calibrationPath], {
  cwd: apiRoot,
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: apiRoot },
});

console.log(`[e2e] PVGIS replay stub on :${port}`);
pipeExit(child);
