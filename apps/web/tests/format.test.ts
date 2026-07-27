import { describe, expect, it } from "vitest";

import { dataSourceLabel, eur, kwh, percent, usd, years } from "@/lib/format";

describe("money formatting", () => {
  it("formats a Decimal string without losing cents", () => {
    // The API sends Decimal as a string precisely so this round-trip is exact.
    expect(eur("2530.58")).toBe("€2,530.58");
    expect(usd("10000.00")).toBe("$10,000.00");
  });

  it("keeps two decimal places even on round amounts", () => {
    expect(eur("8789.70")).toBe("€8,789.70");
  });

  it("drops decimals only when explicitly asked to", () => {
    expect(eur("38721.30", { compact: true })).toBe("€38,721");
  });

  it("handles negative cumulative cash flow", () => {
    expect(eur("-8789.70")).toBe("€-8,789.70");
  });

  it("returns a dash rather than NaN for unusable input", () => {
    expect(eur("not-a-number")).toBe("—");
  });
});

describe("units", () => {
  it("formats kWh with thousands separators", () => {
    expect(kwh(9502.18)).toBe("9,502 kWh");
  });

  it("formats percentages to one decimal", () => {
    expect(percent(68.94)).toBe("68.9%");
  });

  it("says so plainly when payback is never reached", () => {
    expect(years(null)).toBe("Not reached");
  });

  it("formats a payback period", () => {
    expect(years(3.7034)).toBe("3.7 yr");
  });
});

describe("data source labels", () => {
  it("marks live data as live", () => {
    expect(dataSourceLabel("live")).toEqual({ label: "Live", tone: "live" });
  });

  it("never lets a fixture read as live", () => {
    for (const source of ["fixture", "live_fallback_fixture"]) {
      const result = dataSourceLabel(source);
      expect(result.tone).toBe("fixture");
      expect(result.label.toLowerCase()).toContain("demo");
    }
  });

  it("distinguishes a cache fallback from a live value", () => {
    expect(dataSourceLabel("live_fallback_cache").tone).toBe("cache");
  });

  it("treats an unrecognised source as untrusted rather than live", () => {
    expect(dataSourceLabel("something-new").tone).toBe("fixture");
  });
});
