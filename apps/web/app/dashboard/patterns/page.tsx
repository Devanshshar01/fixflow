"use client";

import { usePatternStats } from "@/lib/hooks";
import { FailureCardSkeleton } from "@/components/Skeleton";
import { cn, categoryColor, severityColor, formatRelative } from "@/lib/utils";
import { Zap, AlertCircle, ExternalLink, TrendingUp } from "lucide-react";
import type { PatternStat } from "@/lib/api";

export default function PatternsPage() {
  const { patterns, loading, error } = usePatternStats(50);

  if (loading) {
    return (
      <div className="p-8 max-w-3xl space-y-3">
        <div className="h-7 w-40 bg-gray-200 rounded animate-pulse mb-8" />
        {[...Array(8)].map((_, i) => (
          <FailureCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="flex items-center gap-2 text-red-600 text-sm card">
          <AlertCircle className="w-4 h-4" />
          Failed to load patterns: {error.message}
        </div>
      </div>
    );
  }

  const totalHits = patterns.reduce(
    (sum: number, p: PatternStat) => sum + p.hit_count,
    0
  );

  return (
    <div className="p-8 max-w-3xl">
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-1">
          <Zap className="w-5 h-5 text-blue-600" />
          <h1 className="text-2xl font-bold text-gray-900">Rule engine</h1>
        </div>
        <p className="text-sm text-gray-500">
          {patterns.length} active patterns &middot;{" "}
          {totalHits.toLocaleString()} total hits
        </p>
      </div>

      {patterns.length === 0 && (
        <div className="card text-center py-14">
          <TrendingUp className="w-9 h-9 text-gray-200 mx-auto mb-3" />
          <p className="font-semibold text-gray-900 mb-1">No pattern hits yet</p>
          <p className="text-sm text-gray-500 max-w-xs mx-auto">
            Pattern hit counts appear here after FixFlow analyzes CI failures.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {patterns.map((pattern: PatternStat, index: number) => {
          const pct =
            totalHits > 0
              ? Math.round((pattern.hit_count / totalHits) * 100)
              : 0;

          return (
            <div
              key={pattern.rule_id}
              className="card hover:border-gray-300 transition-colors"
            >
              <div className="flex items-start justify-between gap-4 mb-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-mono text-gray-400 tabular-nums w-5">
                    {index + 1}
                  </span>
                  <span className={cn("badge", categoryColor(pattern.category))}>
                    {pattern.category}
                  </span>
                  <span className={cn("badge", severityColor(pattern.severity))}>
                    {pattern.severity}
                  </span>
                  <span className="text-xs font-mono text-gray-400">
                    {pattern.rule_id}
                  </span>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className="text-sm font-bold text-gray-900 tabular-nums">
                    {pattern.hit_count.toLocaleString()}
                  </span>
                  <span className="text-xs text-gray-400">hits</span>
                </div>
              </div>

              <div className="h-1 bg-gray-100 rounded-full overflow-hidden mb-3">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-500"
                  style={{ width: `${Math.max(pct, 1)}%` }}
                />
              </div>

              <p className="text-sm text-gray-700 leading-relaxed mb-1">
                {pattern.root_cause_template}
              </p>

              <div className="flex items-center justify-between mt-2">
                <p className="text-xs text-gray-400">
                  Last hit {formatRelative(pattern.updated_at)}
                </p>
                {pattern.fix_url && (
                  <a
                    href={pattern.fix_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 transition-colors"
                  >
                    Docs
                    <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}