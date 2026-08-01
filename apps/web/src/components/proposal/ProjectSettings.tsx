"use client";

import { useState } from "react";

import { ApiRequestError, api } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  Button,
  Callout,
  Card,
  Field,
  Input,
  SavedNotice,
  SectionTitle,
} from "@/components/ui/primitives";
import type { Customer, ProjectResponse } from "@/types/api";

/**
 * Rename or delete one project.
 *
 * **Rename is the only edit here, and that is the point.** Everything that
 * moves a figure — the location, the consumption, the system size, the tariff —
 * goes through the conversation, where it is validated and, once a proposal
 * exists, forks a revision. A form that let those be typed in directly would be
 * a second way to change a proposal's inputs, without any of that.
 *
 * **Delete is refused once anything has been issued.** The share link a
 * customer is holding resolves through this project, and `proposals` cascades
 * from it. The button is still shown: the server's refusal explains why, which
 * is more use than a disabled control.
 */
export function ProjectSettings({
  project,
  customer,
  onRenamed,
}: {
  project: ProjectResponse;
  customer: Customer | null;
  onRenamed: (project: ProjectResponse) => void;
}) {
  const [name, setName] = useState(project.name ?? "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = (project.name ?? "") !== name;

  async function rename() {
    setBusy(true);
    setError(null);
    try {
      onRenamed(
        await api.renameProject(project.projectId, name.trim() || null),
      );
      // The Save button disabling itself is not confirmation — it looks the
      // same as never having been dirty. Say so explicitly.
      setSaved(true);
      setTimeout(() => setSaved(false), 4000);
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not rename this.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteProject(project.projectId);
      window.location.href = customer
        ? `/customers/${customer.customerId}`
        : "/projects";
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not delete this.",
      );
      setConfirming(false);
      setBusy(false);
    }
  }

  return (
    <Card className="p-4">
      <SectionTitle>Project settings</SectionTitle>

      <form
        className="mb-3 flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (dirty && !busy) void rename();
        }}
      >
        <Field
          label="Project name"
          htmlFor="project-name"
          hint="A label for telling this apart from the customer's other projects. It changes no figure."
        >
          <Input
            id="project-name"
            value={name}
            onChange={setName}
            testId="project-name-input"
          />
        </Field>
        <div>
          <Button
            type="submit"
            disabled={!dirty || busy}
            testId="save-project-name"
          >
            {busy ? "Saving…" : "Save name"}
          </Button>
          {saved ? <SavedNotice testId="project-name-saved">Name saved</SavedNotice> : null}
        </div>
      </form>

      {error ? (
        <Callout tone="warning" testId="project-settings-error">
          {error}
        </Callout>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-line pt-3">
        <Button
          variant="danger-outline"
          onClick={() => {
            setError(null);
            setConfirming(true);
          }}
          testId="delete-project"
        >
          Delete project
        </Button>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Delete this project?"
        confirmLabel="Delete permanently"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={() => void remove()}
        testId="delete-project-dialog"
      >
        <p>
          This removes the conversation and any draft analysis for
          {project.name ? ` "${project.name}"` : " this project"}
          {customer ? ` (${customer.displayName})` : ""}.
        </p>
        <p className="mt-2">
          A project that has already issued a proposal cannot be deleted — its
          share link still resolves for the customer, and deleting it would
          break that.
        </p>
      </ConfirmDialog>
    </Card>
  );
}
