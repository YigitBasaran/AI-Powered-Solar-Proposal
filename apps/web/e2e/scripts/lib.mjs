import { createConnection } from "node:net";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Shared helpers for the E2E stack launchers. */

export const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const REPO_ROOT = resolve(WEB_ROOT, "..", "..");
export const TMP_DIR = resolve(WEB_ROOT, ".e2e-tmp");

/**
 * True when something is already listening.
 *
 * A stranger's server on our port is a hard error, not something to attach to:
 * a degraded stack silently answered by a healthy one would produce green
 * tests that prove nothing.
 */
export function portInUse(port, host = "127.0.0.1") {
  return new Promise((resolvePromise) => {
    const socket = createConnection({ port, host });
    const done = (result) => {
      socket.destroy();
      resolvePromise(result);
    };
    socket.setTimeout(1000);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

export async function requireFreePort(port, label) {
  if (await portInUse(port)) {
    console.error(
      `\n[e2e] Port ${port} is already in use, so the ${label} server cannot start.\n` +
        `      The suite must own its servers — attaching to an unknown process would\n` +
        `      make the results meaningless. Stop whatever is on ${port} and re-run.\n`,
    );
    process.exit(1);
  }
}

/** Poll a URL until it answers, or give up with a clear message. */
export async function waitForUrl(url, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (response.status < 500) return;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) {
      console.error(`\n[e2e] Timed out after ${timeoutMs}ms waiting for ${label} at ${url}\n`);
      process.exit(1);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

export function ensureTmpDir() {
  mkdirSync(TMP_DIR, { recursive: true });
  return TMP_DIR;
}

/**
 * SQLAlchemy needs a POSIX-style path even on Windows:
 * `sqlite+aiosqlite:///C:/path/to/e2e.db`.
 */
export function sqliteUrl(absolutePath) {
  return `sqlite+aiosqlite:///${absolutePath.replace(/\\/g, "/")}`;
}

/** Remove a SQLite database and the WAL sidecars it leaves behind. */
export function removeDatabase(absolutePath) {
  for (const suffix of ["", "-wal", "-shm", "-journal"]) {
    rmSync(`${absolutePath}${suffix}`, { force: true });
  }
}

/** Forward a child's exit to our own, so Playwright sees the real failure. */
export function pipeExit(child) {
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 0);
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => {
      child.kill(signal);
    });
  }
}
