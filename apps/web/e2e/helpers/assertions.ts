import { expect, type Locator } from "@playwright/test";

/**
 * Assertion vocabulary with an explicit tolerance policy.
 *
 * The policy, stated once so no individual test has to invent one:
 *
 *  - **Exact**: panel counts, capacity, FX rate and date, stored snapshot
 *    strings. These are discrete or decimal-exact and must never drift.
 *  - **Cent-exact**: displayed currency. Money is Decimal on the server and a
 *    string on the wire; a cent of slack would hide a real rounding bug.
 *  - **Small tolerance**: only where display rounding genuinely demands it —
 *    e.g. per-facet kWh rounded for display then summed to a rounded total.
 *
 * Anything looser than this is a weakened assertion, not a tolerance.
 */

/** `€8,789.70` / `$10,000.00` / `9,502 kWh` / `68.9%` -> number. */
export function parseDisplayedNumber(text: string): number {
  const cleaned = text
    .replace(/[€$]/g, "")
    .replace(/,/g, "")
    .replace(/\s/g, "")
    .replace(/kWh|kWp|yr|m²|%|°/gi, "")
    .trim();
  const value = Number.parseFloat(cleaned);
  if (!Number.isFinite(value)) throw new Error(`Not a number: "${text}"`);
  return value;
}

/** Currency to the cent. `"2375.55"` matches `€2,375.55`. */
export async function expectMoney(locator: Locator, expected: string): Promise<void> {
  const text = (await locator.innerText()).trim();
  expect(
    Math.round(parseDisplayedNumber(text) * 100),
    `expected ${expected} but the page showed "${text}"`,
  ).toBe(Math.round(Number.parseFloat(expected) * 100));
}

/**
 * Currency displayed compactly (`€2,376`) still has to be the same money.
 * Compare to the whole-euro rounding the formatter performs — not to a
 * tolerance band, which would accept a genuinely different figure.
 */
export async function expectCompactMoney(locator: Locator, expected: string): Promise<void> {
  const text = (await locator.innerText()).trim();
  expect(
    parseDisplayedNumber(text),
    `expected ${expected} rounded to whole euro, page showed "${text}"`,
  ).toBe(Math.round(Number.parseFloat(expected)));
}

/** Exact integer or decimal-exact value shown in the DOM. */
export async function expectNumber(locator: Locator, expected: number): Promise<void> {
  const text = (await locator.innerText()).trim();
  expect(parseDisplayedNumber(text), `page showed "${text}"`).toBe(expected);
}

/** Display-rounded value: the shown text must round-trip to `expected`. */
export async function expectRounded(
  locator: Locator,
  expected: number,
  digits = 0,
): Promise<void> {
  const text = (await locator.innerText()).trim();
  const factor = 10 ** digits;
  expect(parseDisplayedNumber(text), `page showed "${text}"`).toBe(
    Math.round(expected * factor) / factor,
  );
}

/** Decimal-string equality, normalising trailing-zero differences. */
export function expectSameMoneyString(actual: string, expected: string, message?: string): void {
  expect(Math.round(Number.parseFloat(actual) * 100), message).toBe(
    Math.round(Number.parseFloat(expected) * 100),
  );
}
