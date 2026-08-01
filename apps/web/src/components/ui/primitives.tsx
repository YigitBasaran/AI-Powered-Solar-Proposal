import { clsx, type ClassValue } from "clsx";
import type { ReactNode } from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function Card({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-slate-line bg-surface shadow-[0_1px_2px_rgba(11,11,11,0.04)]",
        // A grid or flex item defaults to `min-width: auto`, so wide content -
        // a scrollable table, or a canvas that renders at its default width
        // before the ResizeObserver measures it - stretches the track instead
        // of scrolling inside. On a phone that pushed the whole page 320 px
        // wider than the screen.
        "min-w-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function SectionTitle({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="text-sm font-semibold tracking-tight text-slate-ink">
        {children}
      </h2>
      {action}
    </div>
  );
}

/**
 * Provenance badge.
 *
 * Present wherever a number is shown whose source could be a fixture. The
 * whole point is that demo data is never mistaken for live data, so the label
 * is text — colour alone never carries the meaning.
 */
export function SourceBadge({
  tone,
  label,
  className,
  testId,
}: {
  tone: "live" | "cache" | "fixture" | "replay";
  label: string;
  className?: string;
  testId?: string;
}) {
  const tones = {
    live: "bg-[#e8f6ec] text-good-700 border-[#bfe4c8]",
    cache: "bg-[#eef2f8] text-navy-800 border-[#cfdcec]",
    fixture: "bg-solar-100 text-[#8a5210] border-[#f0d9ac]",
    // Shares the fixture palette on purpose: both mean "not a live
    // observation", and a reader should not have to learn a third colour to
    // notice that.
    replay: "bg-solar-100 text-[#8a5210] border-[#f0d9ac]",
  } as const;

  return (
    <span
      data-testid={testId}
      // Machine-readable so a test never has to infer provenance from colour -
      // which is also why the human-visible label is text, not a colour alone.
      data-tone={tone}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        tones[tone],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          tone === "live"
            ? "bg-good-600"
            : tone === "cache"
              ? "bg-navy-600"
              : "bg-solar-500",
        )}
      />
      {label}
    </span>
  );
}

export function Kpi({
  label,
  value,
  note,
  emphasis,
  testId,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  emphasis?: boolean;
  /** A KPI is a styled number with no semantic role of its own, so tests need
   *  a stable hook to read the value without matching on formatted text. */
  testId?: string;
}) {
  return (
    <Card
      className={cn("p-3.5", emphasis && "border-navy-700/25 bg-[#f7fafd]")}
    >
      <div className="text-[10.5px] font-medium uppercase tracking-wide text-slate-muted">
        {label}
      </div>
      <div
        data-testid={testId}
        className="mt-1 text-[22px] font-semibold leading-tight tracking-tight text-slate-ink"
      >
        {value}
      </div>
      {note ? (
        <div
          data-testid={testId ? `${testId}-note` : undefined}
          className="mt-0.5 text-[11.5px] text-slate-muted"
        >
          {note}
        </div>
      ) : null}
    </Card>
  );
}

export function Callout({
  tone = "warning",
  title,
  children,
  testId,
}: {
  tone?: "warning" | "info";
  title?: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border-l-4 px-3.5 py-2.5 text-[12.5px] leading-relaxed",
        tone === "warning"
          ? "border-solar-500 bg-solar-100 text-[#7a4a10]"
          : "border-navy-600 bg-[#eef4fa] text-navy-900",
      )}
      role={tone === "warning" ? "alert" : undefined}
      data-testid={testId}
    >
      {title ? <strong className="font-semibold">{title} </strong> : null}
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  className,
  title,
  testId,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger" | "danger-outline";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
  testId?: string;
}) {
  const variants = {
    primary: "bg-navy-900 text-white hover:bg-navy-800 disabled:bg-slate-rule",
    secondary:
      "border border-slate-line bg-surface text-slate-ink hover:bg-surface-2 disabled:text-slate-muted",
    ghost: "text-navy-800 hover:bg-[#eef4fa] disabled:text-slate-muted",
    // Destructive actions read as destructive before they are clicked, and are
    // never styled as plain text: a delete that looks like a link is one
    // mis-click from being pressed by someone who thought it navigated.
    danger: "bg-negative text-white hover:bg-[#b02f2f] disabled:bg-slate-rule",
    "danger-outline":
      "border border-negative/40 bg-surface text-negative hover:bg-negative-soft disabled:text-slate-muted",
  } as const;

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-testid={testId}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium",
        "transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-700",
        "disabled:cursor-not-allowed",
        variants[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}

/**
 * A labelled form control.
 *
 * The label is bound with `htmlFor`, and the error is bound with
 * `aria-describedby` plus `aria-invalid` — so a screen reader announces *why*
 * a field is rejected rather than only that it is red. This application had no
 * form controls at all before customers existed, so these are the first.
 */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  required,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string | null;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  const errorId = `${htmlFor}-error`;
  const hintId = `${htmlFor}-hint`;
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={htmlFor}
        className="text-[12px] font-medium text-slate-ink"
      >
        {label}
        {required ? <span className="ml-0.5 text-negative">*</span> : null}
      </label>
      {children}
      {hint ? (
        <p id={hintId} className="text-[11px] text-slate-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p
          id={errorId}
          className="text-[11px] text-negative"
          data-testid={`${htmlFor}-error`}
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Input({
  id,
  value,
  onChange,
  type = "text",
  placeholder,
  invalid,
  disabled,
  autoComplete,
  className,
  testId,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "tel" | "search";
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
  autoComplete?: string;
  className?: string;
  testId?: string;
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      autoComplete={autoComplete}
      aria-invalid={invalid || undefined}
      aria-describedby={invalid ? `${id}-error` : undefined}
      data-testid={testId ?? id}
      onChange={(event) => onChange(event.target.value)}
      className={cn(
        "w-full rounded-lg border bg-surface px-2.5 py-1.5 text-[13px] text-slate-ink",
        "placeholder:text-slate-muted focus-visible:outline-2 focus-visible:outline-offset-1",
        "focus-visible:outline-navy-700 disabled:bg-surface-2 disabled:text-slate-muted",
        invalid ? "border-negative" : "border-slate-line",
        className,
      )}
    />
  );
}

/** A label/value pair, lifted from `AnalysisPanels` so both can use it. */
export function Row({
  label,
  children,
  testId,
}: {
  label: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <dt className="text-[12px] text-slate-muted">{label}</dt>
      <dd
        className="text-[13px] font-medium text-slate-ink"
        data-testid={testId}
      >
        {children}
      </dd>
    </div>
  );
}

/**
 * A transient "that worked" message.
 *
 * `role="status"` rather than `role="alert"`: a success is announced politely,
 * without interrupting whatever a screen-reader user is in the middle of. It
 * is a live region, so the announcement happens even though the element was
 * not present when the page loaded — which is the whole difference between a
 * confirmation a sighted user sees and one everybody gets.
 */
export function SavedNotice({
  children = "Saved",
  testId = "saved-notice",
}: {
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <span
      role="status"
      data-testid={testId}
      className="inline-flex items-center gap-1.5 rounded-full bg-[#e8f6ec] px-2.5 py-1 text-[12px] font-medium text-good-700"
    >
      <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden fill="none">
        <path
          d="M3.5 8.5l3 3 6-7"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cn(
        "inline-block size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

export function DataTable({
  headers,
  children,
  className,
  label,
}: {
  headers: { label: string; align?: "left" | "right" }[];
  children: ReactNode;
  className?: string;
  /** Names the scroll region for assistive technology. */
  label: string;
}) {
  return (
    // The table is wider than a phone and scrolls sideways. A scroll container
    // with no tab stop is unreachable from the keyboard, so it takes one — and
    // a named region, so the tab stop announces what it is rather than landing
    // the user in an anonymous box.
    <div
      className={cn(
        "overflow-x-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-navy-700",
        className,
      )}
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      <table className="w-full min-w-[420px] border-collapse text-[12.5px]">
        <thead>
          <tr>
            {headers.map((header) => (
              <th
                key={header.label}
                className={cn(
                  "border-b border-slate-rule px-2 py-1.5 font-semibold text-slate-body",
                  header.align === "right" ? "text-right" : "text-left",
                )}
              >
                {header.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
