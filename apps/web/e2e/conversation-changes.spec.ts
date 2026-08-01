import { expect, test } from "./fixtures/proposal";
import { EXPECTED, EXPECTED_FX } from "./fixtures/expected-values";

/**
 * Correcting a value after the analysis has run.
 *
 * Two things had to be true at once, and the interesting one is the second.
 *
 * The change has to actually take effect — the old build wrote `analysis_json`
 * in one place and never cleared it, so a corrected consumption left every
 * figure describing the old one.
 *
 * And it has to recalculate **only what depends on it**. Re-running the whole
 * pipeline would re-fetch the exchange rate, moving the number the customer was
 * quoted out from under them mid-conversation — the same failure the immutable
 * proposal exists to prevent. So the untouched sections are compared field for
 * field rather than assumed.
 */

test.describe("@p1 changing a value", () => {
  test("a consumption change moves the financials and nothing else", async ({ api }) => {
    const projectId = await api.completeIntake("6 kWp", "1,150 kWh");
    const before = await api.runAnalysis(projectId);

    // 400 kWh a month, not 900. At 6 kWp the system produces 9,502 kWh a year,
    // so 1,150 *and* 900 a month are both above it and the saving — capped at
    // whatever is actually used — is identical for the two. Comparing those
    // would have shown the saving unchanged and read as a broken recompute.
    const reply = await api.chat(projectId, "actually make it 400 kWh a month");
    expect(reply.status).toBe(200);
    expect(reply.body.accepted).toBe(true);
    // The rail does not rewind: one corrected number is not a lost workflow.
    expect(reply.body.currentStep).toBe("proposal");
    expect(reply.body.recalculated).toEqual(["monthly_consumption_kwh"]);

    const after = (await api.project(projectId)).analysis;

    expect(JSON.stringify(after.roof)).toBe(JSON.stringify(before.roof));
    expect(JSON.stringify(after.layout)).toBe(JSON.stringify(before.layout));
    expect(JSON.stringify(after.energy)).toBe(JSON.stringify(before.energy));
    // Including the moment it was retrieved: no second observation was made.
    expect(JSON.stringify(after.exchangeRate)).toBe(JSON.stringify(before.exchangeRate));
    expect(after.exchangeRate.rate).toBe(EXPECTED_FX.rate);

    expect(after.financial.annualConsumptionKwh).toBe(4_800);
    expect(after.financial.coveragePercent).toBe(100);
    expect(Number(after.financial.annualSavingsEur)).toBeLessThan(
      Number(before.financial.annualSavingsEur),
    );
  });

  test("the reply names what moved and what carried over", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();

    await solarFlow.send("actually make it 900 kWh a month");

    const message = solarFlow.lastAssistantMessage;
    await expect(message).toContainText("900");
    await expect(message).toContainText(/recalculat/i);
    await expect(message).toContainText(/roof/i);
    await expect(message).toContainText(/exchange rate/i);
  });

  test("a size change recalculates the layout and the KPIs follow", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");

    await solarFlow.send("change it to the largest option");
    await expect(solarFlow.kpi("system-size")).toHaveText(
      `${EXPECTED["9.6"].actualCapacityKwp} kWp`,
      { timeout: 90_000 },
    );
    await expect(solarFlow.kpi("annual-production")).toHaveText(
      `${Math.round(EXPECTED["9.6"].annualProductionKwh).toLocaleString("en-GB")} kWh`,
    );
  });

  test("a selective recompute equals a fresh analysis of the same inputs", async ({ api }) => {
    // If these ever diverged, an edited project would quietly disagree with a
    // freshly analysed one for identical inputs.
    const edited = await api.completeIntake("6 kWp");
    await api.runAnalysis(edited);
    await api.chat(edited, "change it to the largest option");

    const fresh = await api.completeIntake("9.6 kWp");
    await api.runAnalysis(fresh);

    const a = (await api.project(edited)).analysis;
    const b = (await api.project(fresh)).analysis;
    for (const section of ["roof", "layout", "energy", "financial"] as const) {
      expect(JSON.stringify(withoutRetrievalTimes(a[section])), section).toBe(
        JSON.stringify(withoutRetrievalTimes(b[section])),
      );
    }
  });
});

/**
 * The same value with every retrieval timestamp blanked.
 *
 * Two analyses of identical inputs must produce identical figures. They cannot
 * produce identical *timestamps* - one observation happened after the other,
 * and `energy.pvgis` records when. Only the moment an observation was made is
 * normalised; endpoint, source, radiation database, request parameters and
 * every yield stay under comparison, which is the same rule the backend's
 * `VOLATILE_SNAPSHOT_PATHS` applies.
 */
function withoutRetrievalTimes(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutRetrievalTimes);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, inner]) =>
        key === "retrievedAt" || key === "batchCompletedAt"
          ? [key, "<when>"]
          : [key, withoutRetrievalTimes(inner)],
      ),
    );
  }
  return value;
}

test.describe("@p1 a change the client must not ignore", () => {
  test("a corrected consumption updates the KPIs on screen", async ({ solarFlow }) => {
    // The server recomputes; the client has to re-read. Leaving the previous
    // figures on screen is the same failure as never recomputing at all — the
    // customer sees plausible numbers describing inputs they have corrected.
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();

    const before = await solarFlow.kpi("annual-saving").textContent();
    expect(before).toBeTruthy();

    // 400 kWh a month is below what the system produces, so the saving is
    // consumption-limited and genuinely moves. 900 would not: at 6 kWp both
    // 1,150 and 900 are production-limited and the saving is identical.
    await solarFlow.send("actually make it 400 kWh a month");
    await expect(solarFlow.kpi("annual-saving")).not.toHaveText(before!, { timeout: 90_000 });
    // The system itself did not change, which is the other half of the claim.
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
  });
});

test.describe("@p1 changing a finalised proposal", () => {
  test("the issued proposal and its link never change", async ({ api }) => {
    const { projectId, shareToken } = await api.finalisedProposal("6 kWp");
    const before = await api.proposal(shareToken);

    const reply = await api.chat(projectId, "actually make it the largest option");
    expect(reply.body.projectId, "the conversation moved to a revision").not.toBe(projectId);
    expect(reply.body.revisionOfProjectId).toBe(projectId);
    expect(reply.body.assistantMessage).toContain(shareToken);

    const after = await api.proposal(shareToken);
    expect(JSON.stringify(after.body.layout)).toBe(JSON.stringify(before.body.layout));
    expect(JSON.stringify(after.body.financial)).toBe(JSON.stringify(before.body.financial));
  });

  test("finalising the revision issues a second, different link", async ({ api }) => {
    const { projectId, shareToken } = await api.finalisedProposal("6 kWp");
    const revisionId = (await api.chat(projectId, "change to the largest option")).body.projectId;

    const revised = await api.finalize(revisionId);
    expect(revised.status).toBe(200);
    expect(revised.body.shareToken).not.toBe(shareToken);

    // Both links resolve, and they describe different systems.
    const old = await api.proposal(shareToken);
    const now = await api.proposal(revised.body.shareToken);
    expect(old.body.layout.requestedSystemSizeKwp).toBe(6);
    expect(now.body.layout.requestedSystemSizeKwp).toBe(9.6);
  });

  test("a repeated change reuses the one revision", async ({ api }) => {
    const { projectId } = await api.finalisedProposal("6 kWp");

    const first = (await api.chat(projectId, "change to the largest option")).body.projectId;
    const second = (await api.chat(projectId, "change to the largest option")).body.projectId;

    expect(first).not.toBe(projectId);
    expect(second, "a retried delivery forked a second draft").toBe(first);
  });

  test("the browser follows the revision, so the next message lands on it", async ({
    solarFlow,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();
    await solarFlow.finalizeProposal();

    await solarFlow.send("change it to the largest option");
    await expect(solarFlow.lastAssistantMessage).toContainText(/revision/i);

    // The proof that the client moved: the next message is accepted and takes
    // effect. Sent to the parent it would hit an immutable project instead.
    await expect(solarFlow.kpi("system-size")).toHaveText(
      `${EXPECTED["9.6"].actualCapacityKwp} kWp`,
      { timeout: 90_000 },
    );
  });
});

test.describe("@p1 starting over", () => {
  test("a reset asks first and clears only on confirmation", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();

    await solarFlow.send("start over");
    await expect(solarFlow.lastAssistantMessage).toContainText(/Shall I go ahead/i);
    // Nothing wiped by the question itself.
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");

    await solarFlow.send("yes");
    await expect(solarFlow.lastAssistantMessage).toContainText(/Starting over/i);
    await expect(solarFlow.kpiRow).toHaveCount(0);
  });

  test("asking how to start over explains instead of doing it", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();

    await solarFlow.send("how do I start over?");
    await expect(solarFlow.lastAssistantMessage).not.toContainText(/Shall I go ahead/i);
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
  });
});
