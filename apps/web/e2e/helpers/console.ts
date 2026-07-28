import type { Page } from "@playwright/test";

/**
 * Capture browser-side failures.
 *
 * An uncaught exception or a failed request usually means the page is broken
 * in a way the assertions happen not to look at. Collecting them turns a
 * silently-degraded page into a visible failure.
 */

export type ConsoleCapture = {
  errors: string[];
  warnings: string[];
  pageErrors: string[];
  failedRequests: string[];
  /** Everything worth failing a test over, allowlist applied. */
  significant(): string[];
};

/**
 * Noise that is not the application's fault, or is deliberate.
 *
 * Kept deliberately short and specific — a broad allowlist would let a real
 * regression through, which defeats the purpose of capturing at all.
 */
const ALLOWLIST: RegExp[] = [
  // React DevTools nag in development builds.
  /Download the React DevTools/i,
  // Next.js dev-only HMR chatter.
  /\[Fast Refresh\]/i,
  // Chromium emits this for the favicon when none is configured.
  /favicon\.ico/i,
];

export function captureConsole(page: Page): ConsoleCapture {
  const capture: ConsoleCapture = {
    errors: [],
    warnings: [],
    pageErrors: [],
    failedRequests: [],
    significant() {
      return [...capture.pageErrors, ...capture.errors, ...capture.failedRequests].filter(
        (line) => !ALLOWLIST.some((pattern) => pattern.test(line)),
      );
    },
  };

  page.on("console", (message) => {
    const text = `console.${message.type()}: ${message.text()}`;
    if (message.type() === "error") capture.errors.push(text);
    else if (message.type() === "warning") capture.warnings.push(text);
  });

  page.on("pageerror", (error) => {
    capture.pageErrors.push(`pageerror: ${error.message}`);
  });

  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "unknown";
    // An aborted request is normal when a page navigates away mid-flight.
    if (failure.includes("ERR_ABORTED") || failure.includes("net::ERR_ABORTED")) return;
    capture.failedRequests.push(`requestfailed: ${request.method()} ${request.url()} — ${failure}`);
  });

  return capture;
}
