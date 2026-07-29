import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS } from "./fixtures/expected-values";

/**
 * The chat answers questions, at every step, without losing anyone's place.
 *
 * The defect this replaces was step-shaped rather than wording-shaped. The
 * three `ASK_*` intents were unreachable at exactly the three steps a customer
 * is most likely to ask from — the workflow's own branches returned first — so
 * *"which options do we have?"* at the consumption step came back as
 * *"I couldn't read a consumption figure."*
 *
 * So the property under test is not that a reply is helpful. It is that asking
 * is **inert**: the step does not move, the KPIs do not appear or change, and
 * the flow still completes afterwards.
 */

test.describe("@p0 questions during the workflow", () => {
  test("a question at the location step is answered and the step holds", async ({ solarFlow }) => {
    await solarFlow.open();

    await solarFlow.send("why do you need my location?");
    await expect(solarFlow.lastAssistantMessage).toContainText(/Galway Road|Cape Town/i);
    await expect(solarFlow.lastAssistantMessage).not.toContainText(/couldn't read/i);

    // Still at the location step: nothing was stored, nothing advanced.
    await expect(solarFlow.systemSizeCards).toBeHidden();
    await expect(solarFlow.kpiRow).toHaveCount(0);
  });

  test("the roof answers with real numbers before anything is calculated", async ({
    solarFlow,
  }) => {
    // The roof is fixed and derived from the calibration, so it needs no
    // analysis, no consumption and no system size. This is the clearest sign
    // that the chat is not a wizard with a chat skin.
    await solarFlow.open();

    await solarFlow.send("how big is the roof?");
    await expect(solarFlow.lastAssistantMessage).toContainText(/m2/);
    await expect(solarFlow.lastAssistantMessage).toContainText(/4 facets/);
    await expect(solarFlow.kpiRow).toHaveCount(0);
  });

  test("the question that used to be read as a consumption figure", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);

    // Typo included: this is the message from the transcript that prompted the
    // redesign. It used to come back as "I couldn't read a consumption figure".
    await solarFlow.send("whicj options that we have?");
    await expect(solarFlow.lastAssistantMessage).not.toContainText(/couldn't read/i);
    await expect(solarFlow.lastAssistantMessage).toContainText(/3\.6/);
    await expect(solarFlow.lastAssistantMessage).toContainText(/9\.6/);

    // The consumption step is still waiting, and still says so.
    await expect(solarFlow.systemSizeCards).toBeHidden();
    await solarFlow.send("1,150 kWh");
    await expect(solarFlow.systemSizeCards).toBeVisible();
  });

  test("asking at the system-size step does not select a size", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);
    await solarFlow.enterMonthlyConsumption(1150);

    // "how large is the roof?" used to *select 9.6 kWp*, because bare `large`
    // was in the size vocabulary and the extractor ran before any question
    // detection.
    await solarFlow.send("how large is the roof?");
    await expect(solarFlow.systemSizeCards).toBeVisible();
    await expect(solarFlow.kpiRow).toHaveCount(0);

    await solarFlow.selectSystemSize(6);
    await solarFlow.waitForAnalysis();
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
  });

  test("questions after the analysis quote the analysis, and change nothing", async ({
    solarFlow,
    api,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await solarFlow.waitForAnalysis();

    const before = await solarFlow.kpi("payback").textContent();

    await solarFlow.send("what is my payback?");
    await expect(solarFlow.lastAssistantMessage).toContainText(/payback/i);
    await expect(solarFlow.lastAssistantMessage).toContainText(/\d/);

    await solarFlow.send("where do the production figures come from?");
    await expect(solarFlow.lastAssistantMessage).toContainText(/PVGIS/i);

    // Not one KPI moved.
    await expect(solarFlow.kpi("payback")).toHaveText(before!);
    void api;
  });

  test("the flow still completes after a question at every step", async ({ solarFlow }) => {
    await solarFlow.open();

    await solarFlow.send("what can you actually do?");
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);

    await solarFlow.send("what unit should I give?");
    await solarFlow.enterMonthlyConsumption(1150);

    await solarFlow.send("what does kWp mean?");
    await solarFlow.selectSystemSize(6);
    await solarFlow.waitForAnalysis();

    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
    const { shareToken } = await solarFlow.finalizeProposal();
    expect(shareToken.length).toBeGreaterThan(15);
  });
});

test.describe("@p0 the fixed property is stated honestly", () => {
  test("an address elsewhere is blocked, and the case property offered", async ({ api }) => {
    // Behaviour change, deliberate. The old build accepted any input, stored
    // it as the project's location, and analysed Cape Town's roof under it —
    // so every figure downstream carried a property it did not describe.
    for (const input of ["10 Downing Street, London", "51.5074, -0.1278", "Istanbul"]) {
      const { projectId } = await api.createProject();
      const reply = await api.chat(projectId, input);

      expect(reply.body.accepted, `"${input}" was accepted`).toBe(false);
      expect(reply.body.currentStep).toBe("location");
      expect(reply.body.assistantMessage).toContain("-34.046582");

      const project = await api.project(projectId);
      expect(project.rawLocationInput, `"${input}" was stored`).toBeNull();
    }
  });

  test("the case coordinate is accepted, and the sign error explained", async ({ api }) => {
    for (const input of [
      CASE_INPUTS.locationInput,
      "-34.04658, 18.46491",
      // The brief prints the latitude without its minus sign; pasting that
      // verbatim identifies this property, not a point at sea.
      "34.04658242871865, 18.46491476666948",
    ]) {
      const { projectId } = await api.createProject();
      const reply = await api.chat(projectId, input);

      expect(reply.body.accepted, `"${input}" was refused`).toBe(true);
      expect(reply.body.currentStep).toBe("consumption");
      expect(reply.body.assistantMessage.toLowerCase()).toContain("open sea");
    }
  });

  test("confirming the offer continues the flow", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.send("10 Downing Street, London");
    await expect(solarFlow.lastAssistantMessage).toContainText(/Shall I go ahead/i);

    await solarFlow.send("yes");
    await solarFlow.enterMonthlyConsumption(1150);
    await expect(solarFlow.systemSizeCards).toBeVisible();
  });
});
