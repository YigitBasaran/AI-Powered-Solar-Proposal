"use client";

import { useEffect, useState } from "react";

import { ProjectTable } from "@/components/projects/ProjectTable";
import { Button, Card, Input } from "@/components/ui/primitives";

/**
 * Every project, newest first, as a table paged by the server.
 *
 * This is also the only screen that can find a project with **no customer** —
 * a legacy row from before customers existed, or one created by a harness. The
 * customer screens are organised by person, so those are invisible there, and
 * nobody keeps a project id.
 *
 * The table itself lives in `ProjectTable`, shared with a customer's own
 * screen, so the two cannot drift into different columns or lose a delete
 * button on one side.
 */
export default function ProjectsPage() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 150);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight text-slate-ink">Projects</h1>
        <Button
          onClick={() => {
            window.location.href = "/projects/new";
          }}
          testId="add-new-project"
        >
          Add New Project
        </Button>
      </header>

      <Card className="p-4">
        <div className="mb-3">
          <label htmlFor="project-search" className="sr-only">
            Search projects
          </label>
          <Input
            id="project-search"
            type="search"
            value={query}
            onChange={setQuery}
            placeholder="Search by project name or customer"
          />
        </div>

        <ProjectTable
          query={debounced}
          emptyMessage={debounced ? `No project matches "${debounced}".` : "No projects yet."}
        />
      </Card>
    </main>
  );
}
