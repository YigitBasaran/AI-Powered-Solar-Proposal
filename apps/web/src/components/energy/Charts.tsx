"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { MONTH_LABELS, eur, number } from "@/lib/format";

/**
 * Both charts carry a single series, so neither has a legend — the heading
 * names the series. Marks are thin, gridlines and axes recede, and every
 * value label wears an ink colour rather than the series colour.
 */

const SERIES = "#2a78d6";
const SERIES_SOFT = "#cde2fb";
const NEGATIVE = "#d03b3b";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";
const INK_MUTED = "#898781";
const INK = "#0b0b0b";

const axisTick = { fontSize: 11, fill: INK_MUTED };

function TooltipCard({
  title,
  rows,
}: {
  title: string;
  rows: [string, string][];
}) {
  return (
    <div className="rounded-lg border border-slate-line bg-surface px-2.5 py-2 text-[12px] shadow-md">
      <div className="mb-1 font-semibold text-slate-ink">{title}</div>
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4 text-slate-body">
          <span>{label}</span>
          <span className="tnum font-medium text-slate-ink">{value}</span>
        </div>
      ))}
    </div>
  );
}

export function MonthlyProductionChart({ monthly }: { monthly: number[] }) {
  const data = monthly.map((value, index) => ({
    month: MONTH_LABELS[index] ?? String(index + 1),
    kwh: value,
  }));

  return (
    <div className="h-[240px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        {/* `top: 20` leaves room for the value printed above each bar; at 8 the
            tallest month's label was clipped by the plot area. */}
        <BarChart data={data} margin={{ top: 20, right: 8, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="month"
            tick={axisTick}
            tickLine={false}
            axisLine={{ stroke: AXIS }}
          />
          <YAxis
            tick={axisTick}
            tickLine={false}
            axisLine={false}
            width={46}
            tickFormatter={(v: number) => number(v)}
          />
          <Tooltip
            cursor={{ fill: "rgba(42,120,214,0.08)" }}
            content={({ active, payload, label }) =>
              active && payload?.[0] ? (
                <TooltipCard
                  title={String(label)}
                  rows={[
                    ["Production", `${number(Number(payload[0].value))} kWh`],
                  ]}
                />
              ) : null
            }
          />
          {/* 4px rounded data-end, anchored to the baseline. */}
          <Bar
            dataKey="kwh"
            fill={SERIES}
            radius={[4, 4, 0, 0]}
            maxBarSize={34}
          >
            {/* The figure, above its own bar.
                A bar chart shows shape well and value badly: reading July off
                the gridlines gets you "about 1,100", and the question a
                customer actually asks here is what a given month produces. The
                tooltip answered it, but only on hover — and not at all on the
                printed PDF or on a touch screen. */}
            <LabelList
              dataKey="kwh"
              position="top"
              offset={6}
              fill={INK_MUTED}
              fontSize={9}
              formatter={(value: number) => number(value)}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CashFlowChart({
  cashFlow,
  paybackYears,
}: {
  cashFlow: { year: number; cumulativeCashFlowEur: string }[];
  paybackYears: number | null;
}) {
  const data = cashFlow.map((row) => ({
    year: row.year,
    value: Number.parseFloat(row.cumulativeCashFlowEur),
  }));

  return (
    <div className="h-[240px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
        >
          <defs>
            <linearGradient id="cashflow-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SERIES_SOFT} stopOpacity={0.95} />
              <stop offset="100%" stopColor={SERIES_SOFT} stopOpacity={0.25} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="year"
            tick={axisTick}
            tickLine={false}
            axisLine={{ stroke: AXIS }}
            tickFormatter={(v: number) => (v % 5 === 0 ? `Yr ${v}` : "")}
          />
          <YAxis
            tick={axisTick}
            tickLine={false}
            axisLine={false}
            width={54}
            tickFormatter={(v: number) => `${Math.round(v / 1000)}k`}
          />
          <Tooltip
            content={({ active, payload, label }) =>
              active && payload?.[0] ? (
                <TooltipCard
                  title={`Year ${label}`}
                  rows={[["Cumulative", eur(Number(payload[0].value))]]}
                />
              ) : null
            }
          />
          {/* Zero is the meaning of this chart, so it gets the one strong rule. */}
          <ReferenceLine y={0} stroke={INK} strokeWidth={1.5} />
          {paybackYears !== null && Number.isFinite(paybackYears) ? (
            <ReferenceLine
              x={Math.round(paybackYears)}
              stroke={NEGATIVE}
              strokeDasharray="4 3"
              label={{
                value: `Payback ${paybackYears.toFixed(1)} yr`,
                position: "insideTopLeft",
                fontSize: 11,
                fill: INK,
                fontWeight: 600,
              }}
            />
          ) : null}
          <Area
            type="monotone"
            dataKey="value"
            stroke={SERIES}
            strokeWidth={2}
            fill="url(#cashflow-fill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
