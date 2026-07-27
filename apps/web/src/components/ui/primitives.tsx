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
      <h2 className="text-sm font-semibold tracking-tight text-slate-ink">{children}</h2>
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
}: {
  tone: "live" | "cache" | "fixture";
  label: string;
  className?: string;
}) {
  const tones = {
    live: "bg-[#e8f6ec] text-good-700 border-[#bfe4c8]",
    cache: "bg-[#eef2f8] text-navy-800 border-[#cfdcec]",
    fixture: "bg-solar-100 text-[#8a5210] border-[#f0d9ac]",
  } as const;

  return (
    <span
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
          tone === "live" ? "bg-good-600" : tone === "cache" ? "bg-navy-600" : "bg-solar-500",
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
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  emphasis?: boolean;
}) {
  return (
    <Card className={cn("p-3.5", emphasis && "border-navy-700/25 bg-[#f7fafd]")}>
      <div className="text-[10.5px] font-medium uppercase tracking-wide text-slate-muted">
        {label}
      </div>
      <div className="mt-1 text-[22px] font-semibold leading-tight tracking-tight text-slate-ink">
        {value}
      </div>
      {note ? <div className="mt-0.5 text-[11.5px] text-slate-muted">{note}</div> : null}
    </Card>
  );
}

export function Callout({
  tone = "warning",
  title,
  children,
}: {
  tone?: "warning" | "info";
  title?: string;
  children: ReactNode;
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
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
  title?: string;
}) {
  const variants = {
    primary: "bg-navy-900 text-white hover:bg-navy-800 disabled:bg-slate-rule",
    secondary:
      "border border-slate-line bg-surface text-slate-ink hover:bg-surface-2 disabled:text-slate-muted",
    ghost: "text-navy-800 hover:bg-[#eef4fa] disabled:text-slate-muted",
  } as const;

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
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
}: {
  headers: { label: string; align?: "left" | "right" }[];
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-x-auto", className)}>
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
