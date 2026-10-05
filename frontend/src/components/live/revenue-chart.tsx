"use client";

import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney } from "@/lib/utils";

interface Point {
  hour: number;
  today: number | null;
  lastWeek: number;
}

function hourLabel(hour: number): string {
  if (hour === 0) return "12a";
  if (hour === 12) return "12p";
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

function cumulative(values: number[], upTo = values.length): number[] {
  let sum = 0;
  return values.map((v, i) => (i <= upTo ? (sum += v) : NaN));
}

export function RevenueChart({
  today,
  lastWeek,
  currency,
  currentHour,
}: {
  today: number[];
  lastWeek: number[];
  currency: string;
  currentHour: number;
}) {
  const todayCum = cumulative(today, currentHour);
  const lastWeekCum = cumulative(lastWeek);
  const data: Point[] = todayCum.map((value, hour) => ({
    hour,
    today: Number.isNaN(value) ? null : value,
    lastWeek: lastWeekCum[hour],
  }));

  return (
    <Card className="gap-2">
      <CardHeader>
        <CardTitle>Revenue pace</CardTitle>
        <CardDescription>Cumulative revenue by hour, today vs. the same day last week.</CardDescription>
      </CardHeader>
      <CardContent className="px-2 sm:px-4">
        <div className="mb-2 flex items-center gap-4 px-3 text-xs text-muted-foreground" aria-hidden>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-viz-current" /> Today
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-0 w-4 border-t-2 border-dashed border-viz-baseline" /> Last week
          </span>
        </div>
        <div className="h-52" role="img" aria-label="Line chart of cumulative revenue today versus last week">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="revenue-today" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--viz-current)" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="var(--viz-current)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="hour"
                tickFormatter={hourLabel}
                interval={3}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              />
              <YAxis
                width={52}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => formatMoney(v, currency, true)}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
              />
              <Tooltip
                cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
                content={({ active, payload, label }) => {
                  if (!active || !payload?.length) return null;
                  const point = payload[0].payload as Point;
                  return (
                    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-xl">
                      <p className="mb-1 font-medium">By {hourLabel((Number(label) + 1) % 24)}</p>
                      {point.today !== null && (
                        <p className="flex items-center gap-2">
                          <span className="h-0.5 w-3 rounded-full bg-viz-current" />
                          Today{" "}
                          <span className="tabular ml-auto pl-3">{formatMoney(point.today, currency)}</span>
                        </p>
                      )}
                      <p className="flex items-center gap-2">
                        <span className="w-3 border-t-2 border-dashed border-viz-baseline" />
                        Last week{" "}
                        <span className="tabular ml-auto pl-3">{formatMoney(point.lastWeek, currency)}</span>
                      </p>
                    </div>
                  );
                }}
              />
              <Line
                type="monotone"
                dataKey="lastWeek"
                stroke="var(--viz-baseline)"
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="today"
                stroke="var(--viz-current)"
                strokeWidth={2}
                fill="url(#revenue-today)"
                connectNulls={false}
                activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
