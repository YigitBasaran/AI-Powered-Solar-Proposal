import { expect, test } from "./fixtures/proposal";

/**
 * The customer-to-proposal journey, through the browser.
 *
 * The backend suite already proves each piece in isolation. What only a browser
 * can prove is that they connect: a customer created on one screen is the
 * recipient offered on another, the confirmation really is two steps, and the
 * proposal a customer opens is the one that was sent to them.
 *
 * Everything here runs against the console email provider, which records the
 * message and transmits nothing. That is not a test double bolted on for the
 * suite - it is the application's own development provider, and the settings
 * refuse to construct with `EMAIL_MODE=smtp` in a test environment, so this
 * spec *cannot* send real mail even if it were misconfigured.
 */

const CASE_COORD = "-34.04658242871865, 18.46491476666948";

function uniqueEmail(): string {
  return `anna.${Date.now()}.${Math.floor(Math.random() * 1e6)}@example.com`;
}

test.describe("@p0 customer to proposal", () => {
  test("a customer is created, linked, sent to, and sees their own revision", async ({
    api,
    page,
    request,
  }) => {
    // --- create a customer, through the UI -------------------------------
    const email = uniqueEmail();
    await page.goto("/customers");
    await page.getByTestId("toggle-new-customer").click();

    await page.locator("#customer-first-name").fill("Anna");
    await page.locator("#customer-last-name").fill("Schmidt");
    await page.locator("#customer-email").fill(email);
    await page.getByTestId("create-customer").click();

    await expect(page.getByTestId("customer-row").first()).toContainText("Anna Schmidt");

    // --- a project for them, then the existing analysis flow --------------
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Bruno", lastName: "Weiss", email: uniqueEmail() },
    });
    expect(created.status()).toBe(201);
    const customerId = (await created.json()).customer.customerId as string;

    const project = await request.post("/api/v1/projects", { data: { customerId } });
    expect(project.status()).toBe(201);
    const projectId = (await project.json()).projectId as string;

    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);

    const finalised = await api.finalize(projectId);
    expect(finalised.status).toBe(200);
    const shareToken = finalised.body.shareToken as string;

    // --- the internal project view ---------------------------------------
    await page.goto(`/projects/${projectId}`);
    await expect(page.getByTestId("project-customer-name")).toContainText("Bruno Weiss");
    await expect(page.getByTestId("revision-1")).toBeVisible();
    await expect(page.getByTestId("not-yet-viewed")).toBeVisible();

    // --- preview, then confirm -------------------------------------------
    await page.getByTestId("send-proposal").click();

    // The preview shows the real recipient and the real figures, and nothing
    // has been sent yet.
    await expect(page.getByTestId("preview-recipient")).toHaveText(
      (await (await request.get(`/api/v1/customers/${customerId}`)).json()).customer.email,
    );
    await expect(page.getByTestId("preview-body")).toContainText("6 kWp");

    const beforeConfirm = await request.get(`/api/v1/proposals/${finalised.body.proposalId}/deliveries`);
    expect((await beforeConfirm.json()).deliveries).toEqual([]);

    await page.getByTestId("confirm-send").click();

    // Console mode: recorded, and explicitly not sent. The wording is the
    // point - a development provider reported as "Sent" is the single most
    // likely way this feature could mislead its operator.
    const state = page.getByTestId("delivery-state");
    await expect(state).toHaveAttribute("data-status", "sent");
    await expect(state).toHaveAttribute("data-provider-sends", "false");
    await expect(state).toContainText(/recorded locally/i);

    // --- and it can be sent again ----------------------------------------
    // The send key is unique per (proposal, recipient, revision, nonce), so a
    // resend that supplies no nonce recomputes the first send's key and is
    // refused as a duplicate. That is exactly what shipped: the button said
    // "Send again…" and could not.
    await page.getByTestId("send-done").click();
    await expect(page.getByTestId("send-proposal")).toHaveText(/Send again/);
    await page.getByTestId("send-proposal").click();
    await expect(page.getByTestId("resend-notice")).toContainText(/second copy/i);
    await page.getByTestId("confirm-send").click();
    await expect(page.getByTestId("delivery-state")).toHaveAttribute("data-status", "sent");

    const afterResend = await request.get(
      `/api/v1/proposals/${finalised.body.proposalId}/deliveries`,
    );
    // Two records, not one overwritten: each send is its own attempt.
    expect((await afterResend.json()).deliveries).toHaveLength(2);

    // --- the customer opens their link -----------------------------------
    await page.goto(`/proposal/${shareToken}`);
    await expect(page.getByTestId("proposal-title")).toBeVisible();

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByTestId("view-count")).toHaveText("1");
    await expect(page.getByTestId("first-viewed")).not.toHaveText("—");

    // --- the timeline tells the story ------------------------------------
    // Two, because the proposal was sent and then deliberately sent again.
    // Each send is its own event: a resend that overwrote the first would erase
    // the record that the customer was written to twice.
    await expect(page.getByTestId("activity-proposal.email_sent")).toHaveCount(2);
    await expect(page.getByTestId("activity-proposal.viewed")).toBeVisible();
    await expect(page.getByTestId("activity-proposal.finalised")).toBeVisible();

    // --- editing forks a revision; the issued one is untouched ------------
    const issued = (await api.proposal(shareToken)).body;
    await api.chat(projectId, "actually make it the largest option");

    const after = (await api.proposal(shareToken)).body;
    expect(after.layout).toEqual(issued.layout);
    expect(after.financial).toEqual(issued.financial);
    expect(after.revisionNumber).toBe(1);

    // And the customer's link still resolves to exactly what they were sent.
    await page.goto(`/proposal/${shareToken}`);
    await expect(page.getByTestId("proposal-title")).toBeVisible();
  });

  test("the public proposal never carries the customer's email address", async ({
    api,
    page,
    request,
  }) => {
    const email = uniqueEmail();
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Anna", lastName: "Schmidt", email },
    });
    const customerId = (await created.json()).customer.customerId as string;

    const project = await request.post("/api/v1/projects", { data: { customerId } });
    const projectId = (await project.json()).projectId as string;

    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);
    const finalised = await api.finalize(projectId);
    const shareToken = finalised.body.shareToken as string;

    // The served JSON, as a whole. A key-by-key check only proves the fields
    // someone thought to look at.
    const raw = await (await request.get(`/api/v1/proposals/${shareToken}`)).text();
    expect(raw).not.toContain(email);
    expect(raw).toContain("Anna Schmidt");

    // And the rendered page.
    await page.goto(`/proposal/${shareToken}`);
    await expect(page.locator("body")).not.toContainText(email);
  });

  test("a customer's projects are reachable from their record", async ({
    api,
    page,
    request,
  }) => {
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Bruno", lastName: "Weiss", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;

    const project = await request.post("/api/v1/projects", {
      data: { customerId, name: "Galway Road roof" },
    });
    const projectId = (await project.json()).projectId as string;

    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);
    await api.finalize(projectId);

    // The list says they have work in flight...
    await page.goto("/customers");
    await expect(page.getByTestId(`customer-${customerId}-projects`)).toHaveText("1");

    // ...and their record is how you get back to it. Before this existed, a
    // project was unreachable the moment you left the workspace.
    await page.getByTestId(`customer-${customerId}`).click();
    await page.getByTestId("tab-projects").click();
    await expect(page.getByTestId("customer-projects")).toBeVisible();

    await page.getByTestId(`project-row-${projectId}`).click();
    await expect(page.getByTestId("project-customer-name")).toContainText("Bruno Weiss");
    await expect(page.getByTestId("revision-1")).toBeVisible();
  });

  test("the customer record is tabbed, and opens on Details", async ({ page, request }) => {
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Tabbed", lastName: "Record", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;
    await request.post("/api/v1/projects", { data: { customerId } });

    await page.goto(`/customers/${customerId}`);

    // Details first, and the selection is programmatic rather than only visual.
    await expect(page.getByTestId("tab-details")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("customer-email")).toBeVisible();
    await expect(page.getByTestId("customer-projects")).toHaveCount(0);

    // Switching swaps the content in place - no navigation.
    const before = page.url();
    await page.getByTestId("tab-projects").click();
    await expect(page.getByTestId("customer-projects")).toBeVisible();
    await expect(page.getByTestId("customer-email")).toHaveCount(0);
    await expect(page.getByTestId("tab-projects")).toHaveAttribute("aria-selected", "true");
    expect(page.url()).toBe(before);

    await page.getByTestId("tab-activity").click();
    await expect(page.getByTestId("activity-timeline")).toBeVisible();
  });

  test("reopening a project resumes it instead of starting a new one", async ({
    api,
    page,
  }) => {
    // The workspace used to call createProject() unconditionally on boot, so
    // every "continue in workspace" link silently abandoned the job it pointed
    // at and presented an empty chat. The work was still in the database and
    // nothing in the UI could reach it again.
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");

    await page.goto(`/?project=${projectId}`);

    // The stored transcript is back, and the step is where it was left.
    await expect(page.getByTestId("system-size-cards")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("main")).toContainText("1150");

    // And it is the *same* project - not a new one wearing the same URL.
    const reply = await api.chat(projectId, "6 kWp");
    expect(reply.body.projectId).toBe(projectId);
  });

  test("a project is started from a customer, with a collapsible add beside it", async ({
    page,
    request,
  }) => {
    const email = uniqueEmail();
    await page.goto("/projects/new");

    // The add-a-customer panel is closed until asked for: most projects are
    // for someone already on file.
    await expect(page.getByTestId("toggle-add-customer")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await expect(page.getByTestId("start-project")).toBeDisabled();

    await page.getByTestId("toggle-add-customer").click();
    await page.locator("#customer-first-name").fill("Picked");
    await page.locator("#customer-last-name").fill("Person");
    await page.locator("#customer-email").fill(email);

    // The country code travels with the number.
    await page.locator("#customer-phone-code").selectOption("+90");
    await page.locator("#customer-phone").fill("555 123 4567");
    await page.getByTestId("create-customer").click();

    // Created *and* selected — having to then find them in the list would be a
    // step that exists only because the form forgot.
    await expect(page.getByTestId("picked-customer")).toContainText("Picked Person");
    await expect(page.getByTestId("start-project")).toBeEnabled();

    await page.getByTestId("new-project-name").fill("Named at birth");
    await page.getByTestId("start-project").click();
    await page.waitForURL("**/?project=**");

    const listed = await (
      await request.get("/api/v1/projects?q=Named at birth&pageSize=5")
    ).json();
    expect(listed.projects[0].name).toBe("Named at birth");
    expect(listed.projects[0].customer.displayName).toBe("Picked Person");

    const customer = await (await request.get(`/api/v1/customers?q=${email}`)).json();
    expect(customer.customers[0].phone).toBe("+90 555 123 4567");
  });

  test("the lists are tables, paged with totals", async ({ page, request }) => {
    // Seeded so paging has something to page. The database is shared, so the
    // assertions below are about the *page size being honoured* rather than
    // about a fixed row count that another test could move.
    for (let i = 0; i < 12; i += 1) {
      await request.post("/api/v1/customers", {
        data: { firstName: "Pager", lastName: `Row${i}`, email: uniqueEmail() },
      });
    }

    await page.goto("/customers");
    await expect(page.getByTestId("page-indicator")).toContainText(/Page \d+ of \d+/);
    await expect(page.getByTestId("page-summary")).toContainText(/of \d+/);

    // Page size is a real request, not a client-side slice: the API is asked
    // for ten and returns exactly ten while more remain.
    await page.getByTestId("page-size").selectOption("10");
    await expect(page.getByTestId("customer-row")).toHaveCount(10, { timeout: 10_000 });
    await expect(page.getByTestId("page-indicator")).toContainText("Page 1 of");

    await page.getByTestId("page-next").click();
    await expect(page.getByTestId("page-indicator")).toContainText("Page 2 of");
    await expect(page.getByTestId("customer-row").first()).toBeVisible();

    await page.goto("/projects");
    await expect(page.getByTestId("page-indicator")).toContainText(/Page \d+ of \d+/);
  });

  test("a project can be renamed, and a draft deleted", async ({ api, page, request }) => {
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Rena", lastName: "Meyer", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;
    const project = await request.post("/api/v1/projects", { data: { customerId } });
    const projectId = (await project.json()).projectId as string;

    await page.goto(`/projects/${projectId}`);
    await page.getByTestId("project-name-input").fill("Renamed roof");

    // Awaited to completion before reloading. `click()` only *fires* the PATCH;
    // reloading on top of it cancels the request, which is fast enough to hide
    // in a solo run and fails under parallel load.
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes(`/projects/${projectId}`) &&
        response.request().method() === "PATCH",
    );
    await page.getByTestId("save-project-name").click();
    expect((await saved).status()).toBe(200);

    // Confirmed on screen, not merely by the button going quiet.
    await expect(page.getByTestId("project-name-saved")).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("project-name-input")).toHaveValue("Renamed roof");

    // A draft has issued nothing, so it is safe to remove. The confirmation is
    // a real modal dialog, and cancelling it must leave the project alone.
    await page.getByTestId("delete-project").click();
    await expect(page.getByTestId("delete-project-dialog")).toBeVisible();
    await page.getByTestId("dialog-cancel").click();
    await expect(page.getByTestId("project-name-input")).toBeVisible();

    await page.getByTestId("delete-project").click();
    await page.getByTestId("dialog-confirm").click();
    await page.waitForURL(`**/customers/${customerId}`);
    await page.getByTestId("tab-projects").click();
    await expect(page.getByTestId("projects-empty")).toBeVisible();
  });

  test("deleting a customer names what it destroys, and takes their work with it", async ({
    api,
    page,
    request,
  }) => {
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Held", lastName: "Fast", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;
    const project = await request.post("/api/v1/projects", { data: { customerId } });
    const projectId = (await project.json()).projectId as string;

    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);
    const finalised = await api.finalize(projectId);
    const shareToken = finalised.body.shareToken as string;

    await page.goto(`/customers/${customerId}`);
    await page.getByTestId("delete-customer").click();

    // The dialog counts the damage rather than warning in the abstract.
    await expect(page.getByTestId("delete-projects-count")).toContainText("1 project");
    await expect(page.getByTestId("delete-proposals-count")).toContainText(
      "1 issued proposal",
    );
    await expect(page.getByTestId("delete-customer-dialog")).toContainText(
      /share links will stop working/i,
    );

    // Cancelling changes nothing at all.
    await page.getByTestId("dialog-cancel").click();
    expect((await request.get(`/api/v1/proposals/${shareToken}`)).status()).toBe(200);

    await page.getByTestId("delete-customer").click();
    await page.getByTestId("dialog-confirm").click();
    await page.waitForURL("**/customers");

    // The customer, the project and the proposal are all gone - and the share
    // link no longer resolves, which is exactly what the dialog warned about.
    expect((await request.get(`/api/v1/customers/${customerId}`)).status()).toBe(404);
    expect((await request.get(`/api/v1/projects/${projectId}`)).status()).toBe(404);
    expect((await request.get(`/api/v1/proposals/${shareToken}`)).status()).toBe(404);
  });

  test("archiving keeps everything, and is reversible", async ({ api, page, request }) => {
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Kept", lastName: "Safe", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;

    await page.goto(`/customers/${customerId}`);
    await page.getByTestId("archive-customer").click();
    await expect(page.getByTestId("customer-archived")).toBeVisible();

    await page.getByTestId("archive-customer").click();
    await expect(page.getByTestId("customer-archived")).toHaveCount(0);
  });

  test("editing a finalised project stops offering the old proposal as this one", async ({
    api,
    page,
  }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);
    const finalised = await api.finalize(projectId);
    const shareToken = finalised.body.shareToken as string;

    await page.goto(`/?project=${projectId}`);
    await expect(page.getByTestId("share-url")).toHaveAttribute("data-share-token", shareToken);

    // A change forks a revision. The figures on screen are now a draft, and
    // "Open proposal"/"Download PDF" must stop presenting the *old* document as
    // this project's — it still shows the previous numbers, which reads as the
    // edit having been ignored.
    await page.getByRole("textbox", { name: "Message" }).fill("actually make it the largest option");
    await page.getByRole("textbox", { name: "Message" }).press("Enter");

    await expect(page.getByTestId("revision-pending")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("share-url")).toHaveCount(0);

    // The issued document is not broken and is still reachable — it is what
    // the customer was sent.
    await expect(page.getByTestId("superseded-link")).toHaveAttribute(
      "href",
      `/proposal/${shareToken}`,
    );
  });

  test("opening the workspace with no project offers to start one", async ({ page }) => {
    await page.goto("/");

    // It used to POST /projects here, leaving an unnamed project belonging to
    // nobody behind on every visit.
    await expect(page.getByTestId("no-project-open")).toBeVisible();
    await expect(page.getByTestId("start-a-project")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  });

  test("a proposal with no customer cannot be sent, and says why", async ({ api, page }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.runAnalysis(projectId);
    const finalised = await api.finalize(projectId);
    expect(finalised.status).toBe(200);

    await page.goto(`/projects/${projectId}`);
    await expect(page.getByTestId("no-customer")).toBeVisible();
    await expect(page.getByTestId("send-blocked-reason")).toContainText("no customer");
    await expect(page.getByTestId("send-proposal")).toHaveCount(0);
  });

  test("refreshing the proposal page does not inflate the view count", async ({
    api,
    page,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");

    // Each load is awaited to completion before the next begins, because that
    // is what a person refreshing actually does. `page.goto` alone does not
    // wait for the page's fire-and-forget view POST, and three overlapping
    // POSTs can all pass the dedup check before any of them commits - a real
    // property of a read-then-write suppression, and one `concurrency.spec.ts`
    // states outright. Racing the browser here would test that instead of the
    // thing this test is named for.
    for (let i = 0; i < 3; i += 1) {
      const recorded = page.waitForResponse(
        (response) =>
          response.url().includes(`/proposals/${shareToken}/view`) && response.status() === 200,
      );
      await page.goto(`/proposal/${shareToken}`);
      await expect(page.getByTestId("proposal-title")).toBeVisible();
      await recorded;
    }

    const proposal = (await api.proposal(shareToken)).body;
    expect(proposal.views.viewCount).toBe(1);
  });
});

test.describe("@p1 a customer's projects use the same table as /projects", () => {
  test("a project can be paged and deleted from the customer's own screen", async ({
    page,
    request,
  }) => {
    // The customer screen used to render an unpaginated list with no delete
    // affordance at all — a second rendering of the projects table that had
    // drifted away from it.
    const created = await request.post("/api/v1/customers", {
      data: { firstName: "Paged", lastName: "Owner", email: uniqueEmail() },
    });
    const customerId = (await created.json()).customer.customerId as string;

    for (const name of ["Garage roof", "Main house", "Annexe"]) {
      await request.post("/api/v1/projects", { data: { customerId, name } });
    }

    await page.goto(`/customers/${customerId}`);
    await page.getByTestId("tab-projects").click();

    // The table, with its pager — neither of which existed here before.
    await expect(page.getByTestId("customer-projects")).toBeVisible();
    await expect(page.getByTestId("project-row")).toHaveCount(3);
    await expect(page.getByTestId("page-summary")).toHaveText("1–3 of 3");

    // Only this customer's rows, and no redundant Customer column: every row
    // on this screen belongs to them by construction.
    await expect(page.getByRole("columnheader", { name: "Customer" })).toHaveCount(0);

    // And a project can be deleted from here.
    const row = page.getByTestId("project-row").first();
    const deleteButton = row.getByRole("button", { name: /^Delete / });
    await deleteButton.click();
    await page.getByTestId("dialog-confirm").click();

    await expect(page.getByTestId("project-row")).toHaveCount(2);
    await expect(page.getByTestId("page-summary")).toHaveText("1–2 of 2");
  });
});
