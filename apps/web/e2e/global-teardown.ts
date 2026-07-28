import { readdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";

import type { FullConfig } from "@playwright/test";

/**
 * Delete the temporary databases.
 *
 * Playwright tears its `webServer` processes down *after* global teardown, so
 * the API may still hold the SQLite file open when this runs — on Windows that
 * is an `EBUSY`/`EPERM`, not a bug. Freshness is guaranteed at the other end:
 * `serve-api.mjs` deletes the database before uvicorn starts, so a leftover
 * file can never leak into the next run. This is best-effort housekeeping, and
 * says so rather than failing a green suite.
 *
 * The WAL sidecars matter: deleting `x.db` but leaving `x.db-wal` can restore
 * committed rows the moment SQLite reopens the file — so they go together or
 * not at all.
 *
 * The path comes from `config.rootDir` rather than `__dirname`/`import.meta`,
 * either of which breaks depending on whether Playwright loads this file as
 * CommonJS or ESM.
 */
export default async function globalTeardown(config: FullConfig): Promise<void> {
  const tmpDir = resolve(config.rootDir, "..", ".e2e-tmp");

  let names: string[];
  try {
    names = readdirSync(tmpDir).filter((name) => /\.db(-wal|-shm|-journal)?$/.test(name));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }

  const remaining: string[] = [];
  for (const name of names) {
    let removed = false;
    // The server usually exits within a few hundred milliseconds of the run
    // finishing; a short bounded retry covers that without stalling the report.
    for (let attempt = 0; attempt < 5 && !removed; attempt += 1) {
      try {
        rmSync(resolve(tmpDir, name), { force: true });
        removed = true;
      } catch {
        await new Promise((r) => setTimeout(r, 200));
      }
    }
    if (!removed) remaining.push(name);
  }

  if (remaining.length > 0) {
    console.log(
      `[e2e] ${remaining.length} temporary database file(s) were still locked by the ` +
        `server and remain in .e2e-tmp; the next run recreates them from empty.`,
    );
  } else if (names.length > 0) {
    console.log(`[e2e] removed ${names.length} temporary database file(s)`);
  }
}
