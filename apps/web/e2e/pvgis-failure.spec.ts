import { expect, test } from "./fixtures/proposal";

/**
 * What happens when PVGIS genuinely does not answer.
 *
 * This runs against its own stack — API only, no browser, no Next build —
 * whose `PVGIS_BASE_URL` points at a fault path on the replay stub that always
 * returns 503. That is the only honest way to test it: PVGIS is called by the
 * backend, so no amount of browser route interception could reach it.
 *
 * The behaviour under test replaced the fixture fallback. There is no captured
 * payload to fall back to, no synthetic estimate and no partial answer: the
 * analysis fails, says why, and finalisation stays blocked.
 */

const CASE_COORD = "-34.04658242871865, 18.46491476666948";

test.describe("@p0 PVGIS unavailable", () => {
  test("the stack really is pointed at an endpoint that fails", async ({ stack }) => {
    // Guard against the worst failure of a negative test: passing because the
    // stack was healthy and nothing was ever attempted.
    expect(stack.pvgisEndpoint).toContain("__fault/unavailable");
    expect(stack.pvgisTrusted, "a fault endpoint is never proposal-grade").toBe(false);
  });

  test("an analysis fails rather than inventing production figures", async ({ api }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");

    const failure = await api.runAnalysisExpectingFailure(projectId);

    expect(failure.status).toBe(502);
    expect(failure.body.error.code).toBe("PVGIS_UNAVAILABLE");

    const project = await api.project(projectId);
    expect(project.analysis, "no snapshot may be stored").toBeNull();
  });
});
