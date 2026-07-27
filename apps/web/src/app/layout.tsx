import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "solarVis AI — Solar Proposal Flow",
  description:
    "Chat-driven solar feasibility: satellite roof reconstruction, panel placement, PVGIS production, FX-converted financials and a shareable proposal.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
