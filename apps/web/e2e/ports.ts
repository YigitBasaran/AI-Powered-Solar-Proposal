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
} as const;

export const URLS = {
  api: `http://127.0.0.1:${PORTS.api}`,
  web: `http://127.0.0.1:${PORTS.web}`,
  degradedApi: `http://127.0.0.1:${PORTS.degradedApi}`,
  degradedWeb: `http://127.0.0.1:${PORTS.degradedWeb}`,
} as const;

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

/** Where the browser goes for the default (deterministic / live) projects. */
export const PRIMARY_BASE_URL = EXTERNAL_TARGET ?? URLS.web;
