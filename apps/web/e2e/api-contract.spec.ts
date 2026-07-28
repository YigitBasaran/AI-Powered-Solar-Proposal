import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS } from "./fixtures/expected-values";

/**
 * How the API behaves when it is used wrongly.
 *
 * Not exhaustive input fuzzing — that belongs to the backend suite, which owns
 * the schema. What is checked here is the contract a browser depends on: a
 * consistent error envelope, no stack traces on the wire, and no state change
 * from a request that was refused.
 */

test.describe("@p1 API contract", () => {
  test("errors share one envelope, with a request id and no stack trace", async ({ request }) => {
    const cases: [string, () => Promise<{ status: number; text: string }>][] = [
      [
        "unknown project",
        async () => {
          const r = await request.get("/api/v1/projects/00000000-0000-0000-0000-000000000000");
          return { status: r.status(), text: await r.text() };
        },
      ],
      [
        "unknown proposal",
        async () => {
          const r = await request.get("/api/v1/proposals/nope");
          return { status: r.status(), text: await r.text() };
        },
      ],
      [
        "malformed chat body",
        async () => {
          const r = await request.post("/api/v1/projects/does-not-exist/chat", {
            data: { notAMessage: true },
          });
          return { status: r.status(), text: await r.text() };
        },
      ],
    ];

    for (const [label, run] of cases) {
      const { status, text } = await run();
      expect(status, label).toBeGreaterThanOrEqual(400);
      expect(status, label).toBeLessThan(500);

      const body = JSON.parse(text);
      expect(body, label).toHaveProperty("error");
      expect(typeof body.error.code, label).toBe("string");
      expect(typeof body.error.message, label).toBe("string");
      expect(typeof body.error.requestId, label).toBe("string");

      // An error message is for the user, not a window into the server.
      expect(text, label).not.toContain("Traceback");
      expect(text, label).not.toContain("/app/app/");
      expect(text.toLowerCase(), label).not.toContain("sqlalchemy");
    }
  });

  test("a refused message leaves the workflow exactly where it was", async ({ api }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_INPUTS.locationInput);

    const before = await api.chat(projectId, "not a number at all");
    expect(before.body.accepted).toBe(false);
    expect(before.body.currentStep).toBe("consumption");

    // A second refusal must not advance it either, and the good answer must
    // still work afterwards.
    const again = await api.chat(projectId, "definitely not a number");
    expect(again.body.currentStep).toBe("consumption");

    const good = await api.chat(projectId, "1150 kWh");
    expect(good.body.accepted).toBe(true);
    expect(good.body.currentStep).toBe("system_size");
  });

  test("an oversized message is rejected without touching the workflow", async ({
    api,
    request,
  }) => {
    const { projectId } = await api.createProject();
    const response = await request.post(`/api/v1/projects/${projectId}/chat`, {
      data: { message: "x".repeat(100_000) },
    });

    expect(response.status()).toBeGreaterThanOrEqual(400);
    const project = await request.get(`/api/v1/projects/${projectId}`);
    expect((await project.json()).currentStep).toBe("location");
  });

  test("finalising twice returns the same proposal, not a second one", async ({ api }) => {
    const { projectId } = await api.analysedProject("6 kWp");

    const first = await api.finalize(projectId);
    const second = await api.finalize(projectId);

    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    // A customer who double-clicks must not end up with two links to two
    // documents that could later diverge.
    expect(second.body.shareToken).toBe(first.body.shareToken);
    expect(second.body.proposalId).toBe(first.body.proposalId);
  });

  test("the share token is unguessable", async ({ api }) => {
    const tokens = new Set<string>();
    for (let i = 0; i < 3; i += 1) {
      const { shareToken } = await api.finalisedProposal("3.6 kWp");
      tokens.add(shareToken);
      expect(shareToken.length).toBeGreaterThanOrEqual(24);
      expect(shareToken).toMatch(/^[A-Za-z0-9_-]+$/);
    }
    // Distinct, and not a sequence.
    expect(tokens.size).toBe(3);
  });
});
