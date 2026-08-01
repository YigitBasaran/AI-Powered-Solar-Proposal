"use client";

import { useEffect, useState } from "react";
import {
  FilePlus2,
  FolderOpen,
  Menu,
  PanelLeftClose,
  Sun,
  Users,
} from "lucide-react";

import { cn } from "@/components/ui/primitives";
import { StatusBandProvider } from "./StatusBand";

/**
 * The application shell: a collapsible left rail on every route.
 *
 * Until this existed there was no navigation at all. `/customers` and
 * `/projects` were reachable only by typing the URL, so the workspace at `/`
 * was effectively the whole application and everything built around it was
 * invisible.
 *
 * Three details are deliberate.
 *
 * **The collapsed state persists.** It is a layout preference, not a per-page
 * one, so it is kept in `localStorage` and read before first paint would
 * otherwise flash the wrong width.
 *
 * **It becomes a drawer on a phone.** A fixed rail would eat a third of a
 * 375 px viewport, and this application already had a horizontal-overflow bug
 * from a wide element in a grid track. Below `sm` it is off-canvas and opened
 * by a button in the header.
 *
 * **Collapsed still means labelled.** Icons keep their accessible names and
 * gain a `title`, because an icon-only rail is unreadable to anyone who did
 * not build it.
 */
const LINKS = [
  { href: "/", label: "Workspace", icon: Sun, exact: true },
  { href: "/customers", label: "Customers", icon: Users, exact: false },
  { href: "/projects", label: "Projects", icon: FolderOpen, exact: false },
] as const;

const STORAGE_KEY = "solarvis:sidebar-collapsed";

export function AppShell({
  children,
  pathname,
}: {
  children: React.ReactNode;
  pathname: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  function toggleCollapsed() {
    setCollapsed((previous) => {
      const next = !previous;
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  // Route changes close the drawer; leaving it open would cover the page the
  // customer just navigated to.
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  return (
    <div className="flex min-h-screen">
      {drawerOpen ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-slate-ink/30 sm:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      ) : null}

      <nav
        // A real `id`, because `aria-controls` on the collapse button refers to
        // it. A `data-testid` alone leaves that reference dangling, which axe
        // reports as `aria-valid-attr-value`.
        id="app-sidebar"
        aria-label="Main"
        data-testid="app-sidebar"
        data-collapsed={collapsed}
        className={cn(
          "z-40 flex shrink-0 flex-col gap-1 border-r border-black/20 bg-rail p-2",
          // Pinned to the viewport, with its own scrollbar. As an ordinary
          // flex child it scrolled away with the page, so on a long project
          // timeline every destination was off-screen exactly when you wanted
          // to leave the page.
          "sm:sticky sm:top-0 sm:h-screen sm:overflow-y-auto",
          "max-sm:fixed max-sm:inset-y-0 max-sm:left-0 max-sm:w-56 max-sm:overflow-y-auto",
          "max-sm:transition-transform",
          drawerOpen ? "max-sm:translate-x-0" : "max-sm:-translate-x-full",
          collapsed ? "sm:w-14" : "sm:w-52",
        )}
      >
        {/* The collapse control sits at the top, beside the mark — it is the
            first thing you reach for, and at the bottom of a scrolling rail it
            was the first thing to disappear. */}
        <div
          className={cn(
            "mb-1 flex items-center gap-2 py-1.5",
            collapsed ? "justify-center px-0" : "justify-between px-1.5",
          )}
        >
          {!collapsed ? (
            <span className="flex min-w-0 items-center gap-2">
              <Sun className="size-5 shrink-0 text-solar-500" aria-hidden />
              <span className="truncate text-[13px] font-semibold tracking-tight text-rail-ink">
                solarVis AI
              </span>
            </span>
          ) : null}

          <button
            type="button"
            onClick={toggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls="app-sidebar"
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            title={collapsed ? "Expand navigation" : "Collapse navigation"}
            data-testid="toggle-sidebar"
            className={cn(
              "hidden shrink-0 rounded-lg p-1.5 text-rail-muted hover:bg-white/10 sm:block",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
            )}
          >
            {collapsed ? (
              <Menu className="size-4" aria-hidden />
            ) : (
              <PanelLeftClose className="size-4" aria-hidden />
            )}
          </button>
        </div>

        {LINKS.map(({ href, label, icon: Icon, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href);
          return (
            <a
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              title={collapsed ? label : undefined}
              data-testid={`nav-${label.toLowerCase()}`}
              className={cn(
                "flex items-center gap-2.5 rounded-lg py-1.5 text-[13px]",
                collapsed ? "justify-center px-0" : "px-2.5",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
                active
                  ? "bg-rail-active font-medium text-white"
                  : "text-rail-ink hover:bg-white/10",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {/* Never icon-only to assistive technology, even when collapsed. */}
              <span className={cn("truncate", collapsed && "sr-only")}>
                {label}
              </span>
            </a>
          );
        })}

        <a
          href="/projects/new"
          title={collapsed ? "New project" : undefined}
          data-testid="nav-new-project"
          className={cn(
            "mt-1 flex items-center gap-2.5 rounded-lg border border-white/20 py-1.5",
            collapsed ? "justify-center px-0" : "px-2.5",
            "text-[13px] text-rail-ink hover:bg-white/10",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
          )}
        >
          <FilePlus2 className="size-4 shrink-0" aria-hidden />
          <span className={cn("truncate", collapsed && "sr-only")}>
            New project
          </span>
        </a>
      </nav>

      {/* `min-w-0`: without it a wide child - the Konva stage, a scrolling
          table - stretches this track instead of scrolling inside it, which is
          exactly the horizontal-overflow bug `responsive.spec.ts` guards. */}
      <div className="flex min-w-0 flex-1 flex-col">
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open navigation"
          data-testid="open-nav"
          className="flex items-center gap-2 border-b border-slate-line bg-surface px-3 py-2 text-[13px] text-slate-body sm:hidden"
        >
          <Menu className="size-4" aria-hidden />
          Menu
        </button>
        {/* Every operator page, not just the workspace. Whether a figure came
            from a live source or a fixture is the one thing this application
            insists on showing, and it used to be visible on one screen. */}
        <StatusBandProvider>{children}</StatusBandProvider>
      </div>
    </div>
  );
}
