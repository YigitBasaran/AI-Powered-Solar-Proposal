"use client";

import { usePathname } from "next/navigation";

import { AppShell } from "./AppShell";

/**
 * Reads the current route and hands it to `AppShell`.
 *
 * Split from `AppShell` so the shell itself takes `pathname` as a prop and can
 * be rendered in a test without a router.
 *
 * **The public proposal page gets no shell**, and that is a security decision
 * rather than a stylistic one. `/proposal/{token}` is the page a customer
 * opens from an email. There is no authentication anywhere in this
 * application, so a navigation rail on that page would hand anyone holding a
 * share link — or anyone it was forwarded to — a labelled route into the full
 * customer list. The operator's navigation belongs on the operator's pages.
 *
 * **`/dev/*` gets no shell either.** The roof-calibration tool is a developer
 * utility for tracing geometry against a raster, not part of the sales
 * product; "Customers" and "Projects" mean nothing there. It also lays out a
 * 900 px canvas beside a fixed inspector and already overflowed a 1280 px
 * viewport before this rail existed — taking another 208 px from it would have
 * pushed that overflow down to 1024 for no benefit to anyone.
 */
const UNSHELLED = ["/proposal/", "/dev/"];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";

  if (UNSHELLED.some((prefix) => pathname.startsWith(prefix))) {
    return <>{children}</>;
  }
  return <AppShell pathname={pathname}>{children}</AppShell>;
}
