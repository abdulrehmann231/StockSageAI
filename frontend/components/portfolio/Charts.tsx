"use client";

import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PerformancePoint } from "@/lib/types";

const COLORS = [
  "#2563eb",
  "#16a34a",
  "#d97706",
  "#dc2626",
  "#7c3aed",
  "#0891b2",
  "#db2777",
  "#65a30d",
];

export function SectorAllocationChart({
  allocation,
}: {
  allocation: Record<string, number>;
}) {
  const data = Object.entries(allocation).map(([name, value]) => ({ name, value }));
  if (data.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        No priced holdings to chart.
      </p>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius={50}
          outerRadius={90}
          paddingAngle={2}
        >
          {data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function PortfolioChart({ points }: { points: PerformancePoint[] }) {
  if (points.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        No snapshots yet — the daily snapshot worker populates this over time.
      </p>
    );
  }
  const data = points.map((p) => ({
    date: p.snapshot_date,
    value: p.total_value,
    cost: p.total_cost_basis,
  }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} width={56} />
        <Tooltip formatter={(value: number) => value.toLocaleString()} />
        <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} dot={false} name="Value" />
        <Line type="monotone" dataKey="cost" stroke="#9ca3af" strokeWidth={1.5} strokeDasharray="4 4" dot={false} name="Cost basis" />
      </LineChart>
    </ResponsiveContainer>
  );
}
