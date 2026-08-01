import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS, EXPECTED_ROOF } from "./fixtures/expected-values";

/**
 * Does the application come up, and does it tell the truth about itself?
 *
 * Everything downstream assumes a healthy stack in a known mode. If that
 * assumption is wrong, every other failure in the run is noise.
 */

test.describe("@p0 boot and provenance", () => {
  test("the application loads and reports a ready stack", async ({ solarFlow, api, stack }) => {
    const health = await api.health();
    expect(health.status).toBe(200);
    expect(health.body.status).toBe("ok");

    await solarFlow.open();

    // Every operating mode is surfaced, so demo data can never read as live.
    await expect(solarFlow.status("parser")).toHaveAttribute("data-provider", stack.llm);
    await expect(solarFlow.status("imagery")).toHaveAttribute("data-mode", stack.maps);
    await expect(solarFlow.errorBanner).toBeHidden();
  });

  test("the resolved case coordinate is southern-hemisphere, and both are on record", async ({
    api,
  }) => {
    const location = await api.caseLocation();

    // The brief prints the latitude without a minus sign. That point is open
    // sea. We keep what it said and what we use — never silently one or the
    // other.
    expect(location.raw.latitude).toBeCloseTo(CASE_INPUTS.rawLatitude, 10);
    expect(location.resolved.latitude).toBeCloseTo(CASE_INPUTS.resolvedLatitude, 10);
    expect(location.resolved.longitude).toBeCloseTo(CASE_INPUTS.longitude, 10);
    expect(location.hemisphere).toBe("southern");
    expect(location.sourceVerified).toBe(true);
  });

  test("map scale comes from the verified Mercator configuration, not image pixels", async ({
    api,
  }) => {
    const config = await api.mapConfig();

    expect(config.zoom).toBe(20);
    expect(config.scale).toBe(2);
    expect(config.sourceWidthPx).toBe(EXPECTED_ROOF.sourceWidthPx);
    expect(config.sourceHeightPx).toBe(EXPECTED_ROOF.sourceHeightPx);
    // 0.0618 m/px is what z20 scale2 gives at -34° latitude. A suite that
    // divided a ground span by the fixture's pixel width would agree with a
    // resized or cropped fixture, which is exactly the mistake to avoid.
    expect(config.groundMetresPerSourcePixel).toBeCloseTo(
      EXPECTED_ROOF.groundMetresPerSourcePixel,
      5,
    );
    // Imagery attribution must survive into the running application.
    expect(String(config.attribution).length).toBeGreaterThan(10);
  });

  test("no map API key or other credential reaches the browser", async ({ page, api }) => {
    const external: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (!url.startsWith("http://127.0.0.1") && !url.startsWith("http://localhost")) {
        external.push(url);
      }
    });

    // Opened on a real project: the workspace no longer creates one on load,
    // and this test needs the *loaded* workspace — the imagery fetch it
    // performs is precisely what could leak a key or reach a third party.
    const { projectId } = await api.createProject();
    await page.goto(`/?project=${projectId}`);
    await expect(page.getByRole("textbox", { name: "Message" })).toBeEnabled({
      timeout: 30_000,
    });

    // The browser talks to this origin only: Google and PVGIS are reached by
    // the backend, which is what keeps the key server-side and the Konva
    // canvas same-origin (and therefore exportable to PNG).
    expect(external, `unexpected third-party requests: ${external.join(", ")}`).toEqual([]);

    // Nothing that looks like a Google key is in the served markup...
    const html = await page.content();
    expect(html).not.toMatch(/AIza[0-9A-Za-z_-]{20,}/);

    // ...nor in the configuration the client is given.
    const config = JSON.stringify(await api.mapConfig());
    expect(config).not.toMatch(/AIza[0-9A-Za-z_-]{20,}/);
    expect(config.toLowerCase()).not.toContain("apikey");
  });

  test("the page raises no console errors during a full boot", async ({
    solarFlow,
    consoleCapture,
  }) => {
    await solarFlow.open();
    expect(consoleCapture.significant()).toEqual([]);
  });
});
