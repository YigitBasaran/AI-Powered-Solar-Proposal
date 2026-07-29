/**
 * Ports the E2E suite owns.
 *
 * Deliberately clear of 3000/8000 so a developer's running dev server is
 * neither disturbed nor accidentally tested against — a suite that silently
 * attaches to whatever is listening proves nothing about the code in the tree.
 */
export const PORTS = {
  api: Number(process.env.E2E_API_PORT ?? 8100),
  web: Number(process.env.E2E_WEB_PORT ?? 3100),
  degradedApi: Number(process.env.E2E_DEGRADED_API_PORT ?? 8101),
  degradedWeb: Number(process.env.E2E_DEGRADED_WEB_PORT ?? 3101),
  // One replay stub serves every stack. Safe because a fault is a URL path
  // rather than server state, so the stacks cannot interfere with each other.
  pvgisStub: Number(process.env.E2E_PVGIS_STUB_PORT ?? 8102),
  // API-only, no web server: its whole job is the PVGIS-unavailable path.
  pvgisDownApi: Number(process.env.E2E_PVGIS_DOWN_API_PORT ?? 8103),
} as const;

export const URLS = {
  api: `http://127.0.0.1:${PORTS.api}`,
  web: `http://127.0.0.1:${PORTS.web}`,
  degradedApi: `http://127.0.0.1:${PORTS.degradedApi}`,
  degradedWeb: `http://127.0.0.1:${PORTS.degradedWeb}`,
  pvgisStub: `http://127.0.0.1:${PORTS.pvgisStub}`,
  pvgisDownApi: `http://127.0.0.1:${PORTS.pvgisDownApi}`,
} as const;

/**
 * Where a stack should send PVGIS requests.
 *
 * `E2E_LIVE=pvgis` omits the override entirely so the application's own
 * default - the canonical JRC endpoint - applies. That is cleaner than
 * flipping a mode, and it means tier C genuinely cannot be served by the stub.
 */
export const PVGIS_STUB_BASE = `${URLS.pvgisStub}/api/v5_3`;
export const PVGIS_DOWN_BASE = `${URLS.pvgisStub}/__fault/unavailable/api/v5_3`;

/**
 * Point the suite at a stack it does not own — the Docker Compose containers,
 * or a deployed environment.
 *
 * `E2E_TARGET_URL=http://127.0.0.1:3000 npx playwright test --grep "@p0"`
 *
 * When set, no `webServer` is started at all, so the suite cannot silently
 * half-attach to something. The degraded tier is unavailable in this mode: it
 * *is* a specific stack configuration, not a way of driving one.
 */
export const EXTERNAL_TARGET = process.env.E2E_TARGET_URL?.replace(/\/$/, "") ?? null;

/** The degraded tier costs a second Next build; allow opting out while iterating. */
export const DEGRADED_ENABLED = process.env.E2E_DEGRADED !== "0" && EXTERNAL_TARGET === null;

/**
 * The PVGIS-unavailable stack. API only, so it is cheap - but it is still a
 * stack the suite owns, and it is meaningless against an external target.
 */
export const PVGIS_DOWN_ENABLED = EXTERNAL_TARGET === null;

/** Where the browser goes for the default (deterministic / live) projects. */
export const PRIMARY_BASE_URL = EXTERNAL_TARGET ?? URLS.web;
