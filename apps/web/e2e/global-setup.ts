import { DEGRADED_ENABLED, EXTERNAL_TARGET, PRIMARY_BASE_URL, URLS } from "./ports";

const LIVE = new Set((process.env.E2E_LIVE ?? "").split(",").map((s) => s.trim()));

/**
 * Record what the suite is actually talking to, before a single test runs.
 *
 * Playwright starts `webServer` entries before global setup, so by this point
 * both stacks are up. Printing their real modes is the cheapest guard against
 * the worst failure a test suite can have: reporting a pass for a tier it was
 * never running.
 */
export default async function globalSetup(): Promise<void> {
  const lines: string[] = [];

  const describe = async (label: string, base: string) => {
    const response = await fetch(`${base}/api/v1/health/ready`);
    const body = (await response.json()) as {
      status: string;
      checks: {
        maps: { mode: string };
        pvgis: { endpoint: string; trusted: boolean };
        fx: { mode: string };
        llm: { provider: string };
        database: { mode: string };
      };
    };
    const c = body.checks;

    // The quiet failure this whole tier is exposed to: if the replay stub did
    // not start, the API falls through to the real JRC endpoint and the
    // goldens very nearly still pass. Fail loudly instead — unless the run
    // asked for live PVGIS, which is the only time that is intended.
    const local = c.pvgis.endpoint.includes("127.0.0.1");
    if (!local && !LIVE.has("pvgis")) {
      throw new Error(
        `${label} is configured to call ${c.pvgis.endpoint}, which is not the local ` +
          `replay stub. Either the stub failed to start, or PVGIS_BASE_URL was not ` +
          `overridden. Re-run with E2E_LIVE=pvgis if a real call was intended.`,
      );
    }

    lines.push(
      `  ${label.padEnd(13)} ${base}  status=${body.status}  ` +
        `maps=${c.maps.mode} fx=${c.fx.mode} llm=${c.llm.provider}
` +
        `${" ".repeat(16)}pvgis=${c.pvgis.endpoint} trusted=${c.pvgis.trusted}`,
    );
  };

  await describe(EXTERNAL_TARGET ? "external" : "deterministic", PRIMARY_BASE_URL);
  if (DEGRADED_ENABLED) await describe("degraded", URLS.degradedWeb);
  void URLS;

  console.log(`\n[e2e] stacks under test:\n${lines.join("\n")}\n`);
}
