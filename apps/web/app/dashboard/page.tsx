"use client";

import { useAnalyticsSummary } from "@/lib/hooks";
import StatCard from "@/components/StatCard";
import CategoryChart from "@/components/CategoryChart";
import { formatMs } from "@/lib/utils";

export default function DashboardPage() {
  const { summary, loading, error } = useAnalyticsSummary();

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse space-y-6">
          <div className="h-8 w-40 bg-gray-200 rounded" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-24 bg-gray-200 rounded-lg" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="p-8">
        <p className="text-sm text-red-600">Failed to load analytics.</p>
      </div>
    );
  }

  const ruleCount = summary.by_source["rule_engine"] ?? 0;
  const aiCount =
    (summary.by_source["gemini"] ?? 0) + (summary.by_source["ollama"] ?? 0);

  return (
    <div className="p-8 max-w-4xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Overview</h1>
        <p className="text-sm text-gray-500 mt-1">
          All CI failures analyzed by FixFlow
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Total analyzed"
          value={summary.total_analyzed}
          sub="all time"
          accent
        />
        <StatCard
          label="Last 24 hours"
          value={summary.last_24h}
          sub="failures"
        />
        <StatCard
          label="Rule engine rate"
          value={`${summary.rule_engine_rate_pct}%`}
          sub="zero AI cost"
        />
        <StatCard
          label="Avg latency"
          value={formatMs(summary.avg_analysis_ms)}
          sub="end-to-end"
        />
      </div>

      {/* Source breakdown */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">
            By resolution source
          </h2>
          <div className="space-y-3">
            {[
              {
                label: "Rule engine",
                count: ruleCount,
                color: "bg-blue-500",
              },
              { label: "AI (Gemini/Ollama)", count: aiCount, color: "bg-indigo-400" },
              {
                label: "Degraded",
                count: summary.by_source["degraded"] ?? 0,
                color: "bg-gray-300",
              },
            ].map(({ label, count, color }) => {
              const pct =
                summary.total_analyzed > 0
                  ? Math.round((count / summary.total_analyzed) * 100)
                  : 0;
              return (
                <div key={label}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm text-gray-700">{label}</span>
                    <span className="text-sm font-medium text-gray-900">
                      {count}{" "}
                      <span className="text-gray-400 font-normal">({pct}%)</span>
                    </span>
                  </div>
                  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${color}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="card">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">
            Failures by category
          </h2>
          <CategoryChart data={summary.by_category} />
        </div>
      </div>

      {/* Stuck runs warning */}
      {summary.stuck_runs > 0 && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
          ⚠️ {summary.stuck_runs} run
          {summary.stuck_runs !== 1 ? "s" : ""} appear stuck in analysis.
          Check server logs.
        </div>
      )}
    </div>
  );
}