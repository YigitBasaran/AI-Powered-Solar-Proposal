/**
 * Dialling codes for the phone field.
 *
 * Deliberately a short, curated list rather than all ~200 territories: this is
 * a case study for a South-African property sold from Europe, and a picker
 * with two hundred entries is harder to use than one with twenty. `Other`
 * exists so an unlisted country is typed rather than blocked.
 *
 * The stored value is always the full international form — `+27 21 555 0100` —
 * so what is dialled does not depend on knowing where the record was created.
 */
export type DiallingCode = {
  /** ISO 3166-1 alpha-2, used only as a stable key. */
  iso: string;
  country: string;
  code: string;
};

export const DIALLING_CODES: DiallingCode[] = [
  { iso: "ZA", country: "South Africa", code: "+27" },
  { iso: "TR", country: "Türkiye", code: "+90" },
  { iso: "DE", country: "Germany", code: "+49" },
  { iso: "NL", country: "Netherlands", code: "+31" },
  { iso: "BE", country: "Belgium", code: "+32" },
  { iso: "FR", country: "France", code: "+33" },
  { iso: "ES", country: "Spain", code: "+34" },
  { iso: "IT", country: "Italy", code: "+39" },
  { iso: "AT", country: "Austria", code: "+43" },
  { iso: "CH", country: "Switzerland", code: "+41" },
  { iso: "GB", country: "United Kingdom", code: "+44" },
  { iso: "IE", country: "Ireland", code: "+353" },
  { iso: "PT", country: "Portugal", code: "+351" },
  { iso: "PL", country: "Poland", code: "+48" },
  { iso: "SE", country: "Sweden", code: "+46" },
  { iso: "NO", country: "Norway", code: "+47" },
  { iso: "DK", country: "Denmark", code: "+45" },
  { iso: "US", country: "United States", code: "+1" },
  { iso: "AE", country: "United Arab Emirates", code: "+971" },
  { iso: "AU", country: "Australia", code: "+61" },
];

/** The default when nothing else is known: the case property's country. */
export const DEFAULT_DIALLING_CODE = "+27";

/**
 * Split a stored number back into a code and the rest.
 *
 * Longest match first, because `+1` is a prefix of nothing here but `+3` would
 * be of `+31`, `+33`, `+34`… and a shortest-first scan would strip the wrong
 * thing and silently move a digit into the subscriber number.
 */
export function splitPhone(stored: string | null | undefined): {
  code: string;
  rest: string;
} {
  const value = (stored ?? "").trim();
  if (!value) return { code: DEFAULT_DIALLING_CODE, rest: "" };

  const byLongest = [...DIALLING_CODES].sort((a, b) => b.code.length - a.code.length);
  for (const { code } of byLongest) {
    if (value.startsWith(code)) {
      return { code, rest: value.slice(code.length).trim() };
    }
  }
  return { code: DEFAULT_DIALLING_CODE, rest: value };
}

/** Recombine, or produce an empty string when there is no subscriber number. */
export function joinPhone(code: string, rest: string): string {
  const digits = rest.trim();
  return digits ? `${code} ${digits}` : "";
}
