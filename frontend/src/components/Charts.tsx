import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { money, Summary } from "../api";

const SPEND = "#5b7cfa";
const INCOME = "#2bb38a";
const GRID = "var(--grid)";
const AXIS = { fontSize: 12, fill: "var(--muted)" };

const monthLabel = (m: string) =>
  new Date(`${m}-01T00:00:00`).toLocaleString("en-US", { month: "short", year: "2-digit" });

export function MonthlyChart({ data }: { data: Summary["by_month"] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={4}>
        <CartesianGrid vertical={false} stroke={GRID} />
        <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis tickFormatter={(v) => money(v)} tick={AXIS} axisLine={false} tickLine={false} width={64} />
        <Tooltip
          formatter={(v: number) => money(v, true)}
          labelFormatter={monthLabel}
          cursor={{ fill: "var(--hover)" }}
          contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", background: "var(--card)" }}
        />
        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="income" name="Income" fill={INCOME} radius={[4, 4, 0, 0]} maxBarSize={36} />
        <Bar dataKey="spend" name="Spending" fill={SPEND} radius={[4, 4, 0, 0]} maxBarSize={36} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function CategoryChart({
  data,
  active,
  onSelect,
}: {
  data: Summary["by_category"];
  active?: string;
  onSelect: (c: string | undefined) => void;
}) {
  const rows = data.filter((d) => d.amount > 0).slice(0, 10);
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, rows.length * 30)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 56, left: 0, bottom: 0 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="category" tick={AXIS} axisLine={false} tickLine={false} width={110} />
        <Tooltip
          formatter={(v: number) => money(v, true)}
          cursor={{ fill: "var(--hover)" }}
          contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", background: "var(--card)" }}
        />
        <Bar
          dataKey="amount"
          name="Spent"
          radius={[0, 4, 4, 0]}
          barSize={18}
          cursor="pointer"
          onClick={(d: { category: string }) => onSelect(active === d.category ? undefined : d.category)}
          label={{ position: "right", formatter: (v: number) => money(v), fontSize: 12, fill: "var(--muted)" }}
        >
          {rows.map((r) => (
            <Cell key={r.category} fill={SPEND} fillOpacity={!active || active === r.category ? 1 : 0.3} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
