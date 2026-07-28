import { DEGRADED_ENABLED, EXTERNAL_TARGET, PRIMARY_BASE_URL, URLS } from "./ports";

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
        pvgis: { mode: string };
        fx: { mode: string };
        llm: { provider: string };
        database: { mode: string };
      };
    };
    const c = body.checks;
    lines.push(
      `  ${label.padEnd(13)} ${base}  status=${body.status}  ` +
        `maps=${c.maps.mode} pvgis=${c.pvgis.mode} fx=${c.fx.mode} llm=${c.llm.provider}`,
    );
  };

  await describe(EXTERNAL_TARGET ? "external" : "deterministic", PRIMARY_BASE_URL);
  if (DEGRADED_ENABLED) await describe("degraded", URLS.degradedWeb);
  void URLS;

  console.log(`\n[e2e] stacks under test:\n${lines.join("\n")}\n`);
}
