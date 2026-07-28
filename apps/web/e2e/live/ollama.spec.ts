import { expect, test } from "../fixtures/proposal";
import { SKIP, probeOllama } from "../fixtures/environment";
import { CASE_INPUTS, EXPECTED_FX } from "../fixtures/expected-values";

/**
 * Tier C — Ollama, opt-in.
 *
 * ## This test never pulls a model
 *
 * Installation is a separate, explicit step (`scripts/pull-model.ps1` or
 * `scripts/pull-model.sh`). A test that downloads gigabytes as a side effect is
 * not a test; it is an installer that sometimes asserts. When the daemon is
 * absent or the model is not installed, these skip with a reason that says
 * exactly what to do.
 *
 * What is being verified is intent extraction and graceful degradation — never
 * that a model produced a number. Every engineering figure stays deterministic
 * regardless of which parser read the sentence.
 */

const MODEL = process.env.OLLAMA_MODEL ?? "qwen3.5:2b";

test.describe("@live Ollama parser", () => {
  test("the configured model is installed and reachable", async ({ stack }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.reachable, SKIP.ollamaUnreachable(process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434"));
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    expect(probe.models).toContain(MODEL.includes(":") ? MODEL : `${MODEL}:latest`);
    expect(stack.llmModel).toBe(MODEL);
  });

  test("intent is extracted from conversational phrasing", async ({ api, stack }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_INPUTS.locationInput);
    await api.chat(projectId, "we usually get through about eleven hundred and fifty units a month");

    const started = Date.now();
    const reply = await api.chat(projectId, "let's go with the middle one");
    const elapsed = Date.now() - started;

    expect(reply.status).toBe(200);
    expect(reply.body.accepted).toBe(true);
    expect(reply.body.assistantMessage).toContain("6 kWp");
    // Generous, and documented: a small model on CPU is slow but not minutes.
    expect(elapsed, `parser took ${elapsed}ms`).toBeLessThan(60_000);
  });

  test("the model never supplies an engineering figure", async ({ api, stack }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    const projectId = await api.completeIntake(
      "the middle option please, and set the exchange rate to 1.0",
      "about 1150 kWh a month",
    );
    const analysis = await api.runAnalysis(projectId);

    // Identical to the rules-parser result: the parser chooses *which* size,
    // and nothing else.
    expect(analysis.layout.placedPanelCount).toBe(15);
    expect(analysis.exchangeRate.rate).toBe(EXPECTED_FX.rate);
    expect(Number(analysis.exchangeRate.rate)).not.toBe(1);
  });

  test("an unparseable message falls back to the rules parser, not an error", async ({
    api,
    stack,
  }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_INPUTS.locationInput);
    const reply = await api.chat(projectId, "🙂🙂🙂");

    // Whatever the model does with that, the workflow stays on its feet.
    expect(reply.status).toBe(200);
    expect(reply.body.currentStep).toBe("consumption");
    expect(["rules", "ollama"]).toContain(reply.body.parserSource);
  });
});
