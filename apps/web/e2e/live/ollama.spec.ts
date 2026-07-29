import { expect, test } from "../fixtures/proposal";
import { SKIP, probeOllama } from "../fixtures/environment";
import { CASE_INPUTS } from "../fixtures/expected-values";

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
  // A 2.3 B model on CPU takes tens of seconds per call, and a reasoning model
  // thinks before it answers. The default 20 s action timeout would record
  // "slow hardware" as "broken integration". These bounds are generous on
  // purpose; the assertion that matters is what comes back, not how fast.
  test.use({ actionTimeout: 180_000 });
  test.describe.configure({ timeout: 600_000 });

  test("the configured model is installed and reachable", async ({ stack }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.reachable, SKIP.ollamaUnreachable(process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434"));
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    expect(probe.models).toContain(MODEL.includes(":") ? MODEL : `${MODEL}:latest`);
    expect(stack.llmModel).toBe(MODEL);
  });

  /**
   * The model's answer must actually *reach* the state machine.
   *
   * This is the assertion that matters, and the one that caught a real defect:
   * `qwen3.5:2b` is a reasoning model, and until the client sent
   * `"think": false` Ollama returned its whole schema-constrained answer in
   * `thinking` with `response` **empty**. The client read `response`, found it
   * empty, and fell back to the rules parser — silently, correctly, and every
   * single time. `parserSource` stayed `"rules"` for every message, so the LLM
   * layer contributed nothing at all while appearing to work.
   *
   * What is *not* asserted is that the model extracts well. That is a property
   * of whichever model happens to be pulled, not of this code; the observed
   * behaviour of `qwen3.5:2b` is recorded in docs/local-ai.md instead.
   */
  test("the model's answer reaches the state machine, validated", async ({ api, stack }) => {
    test.skip(stack.llm !== "ollama", SKIP.notLive("LLM", stack.llm));
    const probe = await probeOllama();
    test.skip(!probe.installed, SKIP.ollamaAbsent(MODEL));

    // Phrases the deterministic router genuinely cannot settle: no digits, no
    // number words, no unit, no ordinal keyword, and not shaped like a
    // question.
    //
    // Two earlier probes were retired here rather than weakened. "we usually
    // get through about eleven hundred and fifty units a month" and "the one
    // that fits fifteen panels" are now parsed deterministically by
    // `conversation/numbers.py`, so they never reach the model at all. That is
    // a win in the parser; keeping them would have recorded it as a regression
    // in this test.
    const beyondRules = [
      "whichever one my neighbour got",
      "about the same as we used last winter",
      "roughly what the previous tenants were paying",
    ];

    const sources: string[] = [];
    let slowest = 0;
    for (const message of beyondRules) {
      const { projectId } = await api.createProject();
      await api.chat(projectId, CASE_INPUTS.locationInput);

      const started = Date.now();
      const reply = await api.chat(projectId, message);
      const elapsed = Date.now() - started;
      slowest = Math.max(slowest, elapsed);

      expect(reply.status, message).toBe(200);
      sources.push(reply.body.parserSource);
      console.log(
        `[live] "${message}" -> ${reply.body.parserSource} ` +
          `accepted=${reply.body.accepted} in ${(elapsed / 1000).toFixed(1)}s`,
      );
    }

    expect(
      sources,
      "the model was consulted but never produced output the schema accepted — " +
        "check that `think: false` is being sent",
    ).toContain("llm");

    // A bound that catches a hang, not a benchmark: this measures the host's
    // CPU as much as the integration.
    expect(slowest, `slowest model call ${slowest}ms`).toBeLessThan(180_000);
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
    // and nothing else. The rate is asserted as an invariant, not as a
    // literal — this tier may be running against the real ECB feed, and
    // pinning today's rate here would be the exact mistake the fixture tier
    // exists to avoid.
    expect(analysis.layout.placedPanelCount).toBe(15);
    expect(analysis.layout.feasibleSystemSizeKwp).toBe(6);
    expect(Number(analysis.exchangeRate.rate)).toBeGreaterThan(0.5);
    expect(Number(analysis.exchangeRate.rate)).toBeLessThan(1.5);
    expect(Number(analysis.exchangeRate.rate)).not.toBe(1);
    // The conversion really was applied, whatever the rate was.
    expect(Number(analysis.financial.convertedCapex.amount)).toBeCloseTo(
      Number(analysis.financial.originalCapex.amount) * Number(analysis.exchangeRate.rate),
      0,
    );
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
    // The API names the source `llm`, not the provider.
    expect(reply.status).toBe(200);
    expect(reply.body.currentStep).toBe("consumption");
    expect(["rules", "llm"]).toContain(reply.body.parserSource);
  });
});
