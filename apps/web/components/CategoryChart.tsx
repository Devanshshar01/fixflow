"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

interface CategoryChartProps {
  data: { category: string; count: number }[];
}

const COLORS = [
  "#3b82f6",
  "#6366f1",
  "#8b5cf6",
  "#a855f7",
  "#ec4899",
  "#f43f5e",
  "#f97316",
  "#eab308",
];

export default function CategoryChart({ data }: CategoryChartProps) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-gray-400">
        No failures analyzed yet
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: -20 }}>
        <XAxis
          dataKey="category"
          tick={{ fontSize: 12, fill: "#6b7280" }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fontSize: 12, fill: "#6b7280" }}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ fill: "#f3f4f6" }}
          contentStyle={{
            border: "1px solid #e5e7eb",
            borderRadius: "6px",
            fontSize: "12px",
          }}
        />
        <Bar dataKey="count" radius={[4, 4, 0, 0]}>
          {data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={COLORS[index % COLORS.length]}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}