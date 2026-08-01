/**
 * The navigation rail.
 *
 * The assertion that matters most is the *absence* one: the public proposal
 * page gets no shell. There is no authentication anywhere in this application,
 * so a navigation rail on the page a customer opens from an email would hand
 * anyone holding — or forwarded — a share link a labelled route into the full
 * customer list.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/AppShell";
import { Shell } from "@/components/layout/Shell";

const pathname = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => pathname() }));

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
});

describe("Shell", () => {
  it("wraps an operator page in the navigation rail", () => {
    pathname.mockReturnValue("/customers");
    render(
      <Shell>
        <p>inner</p>
      </Shell>,
    );
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();
  });

  it("gives the developer calibration tool no navigation either", () => {
    // A geometry-tracing utility, not part of the sales product.
    pathname.mockReturnValue("/dev/roof-calibration");
    render(
      <Shell>
        <p>inner</p>
      </Shell>,
    );
    expect(screen.queryByTestId("app-sidebar")).not.toBeInTheDocument();
  });

  it("gives the public proposal page no navigation at all", () => {
    pathname.mockReturnValue("/proposal/abc123token");
    render(
      <Shell>
        <p>inner</p>
      </Shell>,
    );

    expect(screen.queryByTestId("app-sidebar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("nav-customers")).not.toBeInTheDocument();
    expect(screen.getByText("inner")).toBeInTheDocument();
  });
});

describe("AppShell", () => {
  function renderAt(path: string) {
    return render(
      <AppShell pathname={path}>
        <p>inner</p>
      </AppShell>,
    );
  }

  it("offers customers, projects and a way to start work", () => {
    renderAt("/");
    for (const id of ["nav-workspace", "nav-customers", "nav-projects", "nav-new-project"]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it("marks the current section for assistive technology", () => {
    renderAt("/customers/abc");
    expect(screen.getByTestId("nav-customers")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("nav-projects")).not.toHaveAttribute("aria-current");
  });

  it("does not mark the workspace active on every route", () => {
    // `/` is a prefix of everything, so an exact match is the only correct rule.
    renderAt("/projects");
    expect(screen.getByTestId("nav-workspace")).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("nav-projects")).toHaveAttribute("aria-current", "page");
  });

  it("collapses and remembers it", async () => {
    renderAt("/");
    const sidebar = screen.getByTestId("app-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "false");

    await userEvent.click(screen.getByTestId("toggle-sidebar"));

    expect(sidebar).toHaveAttribute("data-collapsed", "true");
    expect(window.localStorage.getItem("solarvis:sidebar-collapsed")).toBe("1");
  });

  it("restores the collapsed preference on the next page", () => {
    window.localStorage.setItem("solarvis:sidebar-collapsed", "1");
    renderAt("/customers");
    expect(screen.getByTestId("app-sidebar")).toHaveAttribute("data-collapsed", "true");
  });

  it("keeps every destination named when collapsed", async () => {
    renderAt("/");
    await userEvent.click(screen.getByTestId("toggle-sidebar"));

    // An icon-only rail is unreadable to anyone who did not build it, and
    // invisible to a screen reader. The label is hidden visually, not removed.
    expect(screen.getByTestId("nav-customers")).toHaveAccessibleName(/customers/i);
    expect(screen.getByTestId("nav-projects")).toHaveAccessibleName(/projects/i);
  });
});
