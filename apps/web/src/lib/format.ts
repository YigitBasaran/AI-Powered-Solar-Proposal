/**
 * Formatters.
 *
 * Money arrives from the API as a string because it is Decimal on the server.
 * These helpers format for display only — they never re-derive a value, so a
 * figure on screen always traces back to one the backend calculated.
 */

const numberFormat = new Intl.NumberFormat("en-GB");
const moneyFormat = new Intl.NumberFormat("en-GB", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function kwh(value: number, digits = 0): string {
  return `${value.toLocaleString("en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} kWh`;
}

export function number(value: number, digits = 0): string {
  return value.toLocaleString("en-GB", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function eur(value: string | number, opts?: { compact?: boolean }): string {
  const amount = typeof value === "string" ? Number.parseFloat(value) : value;
  if (!Number.isFinite(amount)) return "—";
  if (opts?.compact) return `€${numberFormat.format(Math.round(amount))}`;
  return `€${moneyFormat.format(amount)}`;
}

export function usd(value: string | number): string {
  const amount = typeof value === "string" ? Number.parseFloat(value) : value;
  if (!Number.isFinite(amount)) return "—";
  return `$${moneyFormat.format(amount)}`;
}

export function percent(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

export function years(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Not reached";
  return `${value.toFixed(1)} yr`;
}

export function area(value: number): string {
  return `${value.toFixed(2)} m²`;
}

export function metres(value: number, digits = 2): string {
  return `${value.toFixed(digits)} m`;
}

export function degrees(value: number, digits = 1): string {
  return `${value.toFixed(digits)}°`;
}

export function shortDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * Human label for where a number actually came from.
 *
 * Used everywhere a value is shown, because the one thing that must never
 * happen is fixture data reading as live.
 */
export function dataSourceLabel(source: string): {
  label: string;
  tone: "live" | "cache" | "fixture";
} {
  switch (source) {
    case "live":
      return { label: "Live", tone: "live" };
    case "cache":
      return { label: "Cached", tone: "cache" };
    case "live_fallback_cache":
      return { label: "Cached (live unavailable)", tone: "cache" };
    case "fixture":
      return { label: "Demo fixture", tone: "fixture" };
    case "live_fallback_fixture":
      return { label: "Demo fixture (live unavailable)", tone: "fixture" };
    default:
      return { label: source || "Unknown", tone: "fixture" };
  }
}

export const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;
