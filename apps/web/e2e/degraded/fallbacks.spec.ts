import { expect, test } from "../fixtures/proposal";
import { CASE_INPUTS, EXPECTED_FX } from "../fixtures/expected-values";
import { extractPdf } from "../helpers/pdf";

/**
 * Tier B — the degraded stack.
 *
 * This project runs against :3101 / :8101, an API configured with PVGIS and FX
 * pointed at the reserved discard port and Ollama at a port nothing listens on.
 * Those calls are made by the **backend**, so no amount of browser route
 * interception could reach them: the only honest way to test the fallbacks is
 * a stack that genuinely cannot reach its dependencies.
 *
 * The bar is not "it does not crash". It is: the proposal still completes, and
 * every degraded number is labelled as such rather than passed off as live.
 */

test.describe("@p0 degraded external services", () => {
  test("the stack reports itself as attempting live, and is not silently fixture", async ({
    stack,
  }) => {
    // Guard against the worst failure mode of a degraded tier: accidentally
    // running against the healthy stack and reporting a pass.
    expect(stack.fx, "degraded stack must be configured for live FX").toBe("live");
    expect(stack.llm).toBe("ollama");
    // PVGIS points at the replay stub here, deliberately. Without production
    // figures there is no analysis, and the FX and maps fallbacks this tier
    // exists to prove would have nothing to be asserted against. The
    // PVGIS-unavailable path has its own stack — see pvgis-failure.spec.ts.
    expect(stack.pvgisEndpoint).toContain("127.0.0.1");
  });

  test("a full proposal still completes with every dependency unreachable", async ({
    solarFlow,
    roofView,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    // The whole point of the fallbacks: the customer still gets an answer.
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
    const counts = await roofView.panelCounts();
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(15);

    const { shareToken } = await solarFlow.finalizeProposal();
    expect(shareToken.length).toBeGreaterThan(15);
  });

  test("FX falls back without ever substituting parity", async ({ api, solarFlow }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const fx = analysis.exchangeRate;

    expect(fx.isLive).toBe(false);
    expect(fx.retrievalSource).toMatch(/fallback|fixture|cache/);
    // The one thing that must never happen when a rate lookup fails.
    expect(Number(fx.rate)).not.toBe(1);
    expect(fx.rate).toBe(EXPECTED_FX.rate);
    expect(Number(analysis.financial.convertedCapex.amount)).not.toBeCloseTo(
      Number(analysis.financial.originalCapex.amount),
      2,
    );

    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await expect(solarFlow.fx("source-badge")).not.toHaveAttribute("data-tone", "live");
    await expect(solarFlow.fx("source-badge")).toContainText(/unavailable|fixture|cached/i);
  });

  test("the LLM is unreachable, so the deterministic parser answers", async ({
    api,
    solarFlow,
  }) => {
    // Ollama is configured but pointed at a dead port. The rules parser must
    // take over silently and correctly — the workflow cannot depend on a model.
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_INPUTS.locationInput);
    const consumption = await api.chat(projectId, "1150 kWh");
    expect(consumption.body.accepted).toBe(true);
    expect(consumption.body.parserSource).toBe("rules");

    const size = await api.chat(projectId, "the middle one please");
    expect(size.body.accepted).toBe(true);
    expect(size.body.assistantMessage).toContain("6 kWp");

    await solarFlow.open();
    await expect(solarFlow.status("parser")).toBeVisible();
  });

  test("the degraded proposal's PDF is still complete and correctly labelled", async ({
    api,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const pdf = await api.proposalPdf(shareToken);
    expect(pdf.status).toBe(200);

    const { text } = await extractPdf(pdf.body);
    expect(text).toContain("Solar Feasibility Proposal");
    expect(text).toContain(EXPECTED_FX.rate);
    // Provenance survives the fallback path.
    expect(text.toLowerCase()).toMatch(/fixture|cached|not live/);
  });

  test("the browser sees a real image, or a handled failure — never a broken canvas", async ({
    page,
    solarFlow,
    roofView,
  }) => {
    // The satellite raster *is* browser-originated, so intercepting it here is
    // legitimate. Serve HTML where an image is expected.
    await page.route("**/api/v1/maps/satellite*", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html>not an image</html>" }),
    );

    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    // The analysis is backend-side, so it still succeeds...
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
    // ...and the stage still renders its vector layers over the missing image.
    expect(await roofView.stageHasContent()).toBe(true);
    await expect(solarFlow.errorBanner).toBeHidden();
  });
});
