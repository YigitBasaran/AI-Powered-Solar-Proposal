import { expect, test } from "../fixtures/proposal";
import { CASE_INPUTS } from "../fixtures/expected-values";

/**
 * Tier B — what the stack says when the model is genuinely unreachable.
 *
 * This project runs against :3101 / :8101, where `LLM_PROVIDER=ollama` and
 * `OLLAMA_BASE_URL` points at a host that does not resolve. So every message
 * the deterministic router cannot settle produces a real, failed HTTP attempt —
 * which is exactly the state the old contract could not express.
 *
 * `parserSource` used to read `"rules"` both when the rules parser succeeded
 * and when Ollama was called and failed. A silenced language layer was
 * therefore indistinguishable from a healthy one, and that is how a defect
 * which switched the whole thing off survived a build.
 */

test.describe("@p0 degraded language model", () => {
  test("the stack is configured for a model it cannot reach", async ({ stack }) => {
    // Guard against the worst failure of a degraded tier: silently running
    // against the healthy stack and reporting a pass.
    expect(stack.llm, "degraded stack must be configured for ollama").toBe("ollama");
  });

  test("a clear message is answered by rules, and that is not a fallback", async ({ api }) => {
    const { projectId } = await api.createProject();
    const reply = await api.chat(projectId, CASE_INPUTS.locationInput);

    const interpretation = reply.body.interpretation;
    expect(interpretation.configuredProvider).toBe("ollama");
    expect(interpretation.attemptedProvider, "no HTTP call should have been made").toBeNull();
    expect(interpretation.effectiveProvider).toBe("rules");
    expect(interpretation.fallbackReason).toBe("rules_sufficient");
    expect(reply.body.parserSource).toBe("rules");
  });

  test("an unreachable model is reported as attempted and failed", async ({ api }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_INPUTS.locationInput);
    await api.chat(projectId, "1,150 kWh");

    // Beyond the deterministic router: no digits, no ordinal keyword, not a
    // question. The router declines and the model is genuinely called.
    const reply = await api.chat(projectId, "whichever one my neighbour got");

    const interpretation = reply.body.interpretation;
    expect(interpretation.attemptedProvider).toBe("ollama");
    expect(interpretation.effectiveProvider).toBe("rules");
    expect(
      interpretation.fallbackReason,
      "a real failure must not be reported as rules_sufficient",
    ).not.toBe("rules_sufficient");
    expect(interpretation.fallbackReason).toBe("unreachable");
    expect(interpretation.modelName).toBeTruthy();
  });

  test("the customer sees the fallback chip only on the failed message", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);

    // A clean deterministic answer: no chip, on a stack configured for Ollama.
    await expect(solarFlow.lastAssistantMessage.getByTestId("fallback-chip")).toHaveCount(0);

    await solarFlow.enterMonthlyConsumption(1150);
    await solarFlow.send("whichever one my neighbour got");

    const chip = solarFlow.lastAssistantMessage.getByTestId("fallback-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("Handled with safe fallback");
  });

  test("provider detail is behind a disclosure, not in the bubble", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);
    await solarFlow.enterMonthlyConsumption(1150);
    await solarFlow.send("whichever one my neighbour got");

    const details = solarFlow.lastAssistantMessage.getByTestId("message-telemetry");
    await expect(details).not.toHaveAttribute("open", "");

    await details.locator("summary").click();
    await expect(details.getByTestId("configured-provider")).toHaveText("ollama");
    await expect(details.getByTestId("effective-provider")).toHaveText("rules");
    await expect(details.getByTestId("fallback-reason")).toContainText(/could not be reached/i);
  });

  test("the workflow still completes with no model at all", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");

    // And questions are still answered — the answer service is deterministic.
    await solarFlow.send("how is payback calculated?");
    await expect(solarFlow.lastAssistantMessage).toContainText(/annual saving/i);
  });
});
