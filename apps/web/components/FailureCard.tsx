"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import type { WorkflowRun } from "@/lib/api";
import {
  formatRelative,
  formatMs,
  confidenceLabel,
  sourceLabel,
  categoryColor,
  cn,
} from "@/lib/utils";

interface FailureCardProps {
  run: WorkflowRun;
  repoFullName: string;
}

export default function FailureCard({ run, repoFullName }: FailureCardProps) {
  const [expanded, setExpanded] = useState(false);
  const analysis = run.analysis;

  const conf = analysis ? confidenceLabel(analysis.confidence) : null;

  return (
    <div className="card hover:border-gray-300 transition-colors">
      {/* Header row */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            {analysis && (
              <span className={cn("badge", categoryColor(analysis.category))}>
                {analysis.category}
              </span>
            )}
            {analysis && conf && (
              <span className={cn("badge", conf.className)}>{conf.label}</span>
            )}
            {analysis && (
              <span className="badge badge-gray">{sourceLabel(analysis.source)}</span>
            )}
          </div>

          <p className="font-medium text-gray-900 text-sm truncate">
            {run.workflow_name ?? "Unknown workflow"}
          </p>

          <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
            {run.pr_number && (
              <a
                href={`https://github.com/${repoFullName}/pull/${run.pr_number}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 hover:text-blue-600 transition-colors"
              >
                PR #{run.pr_number}
                <ExternalLink className="w-3 h-3" />
              </a>
            )}
            {run.head_sha && (
              <span className="font-mono">{run.head_sha.slice(0, 7)}</span>
            )}
            <span>{formatRelative(run.triggered_at)}</span>
            {run.analysis_ms != null && (
              <span>analyzed in {formatMs(run.analysis_ms)}</span>
            )}
          </div>
        </div>

        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex-shrink-0 p-1.5 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? (
            <ChevronUp className="w-4 h-4" />
          ) : (
            <ChevronDown className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Expanded analysis */}
      {expanded && analysis && (
        <div className="mt-4 pt-4 border-t border-gray-100 space-y-3">
          {analysis.failed_step && (
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Failed step
              </p>
              <p className="text-sm font-mono text-gray-800 bg-gray-50 px-2.5 py-1.5 rounded">
                {analysis.failed_step}
              </p>
            </div>
          )}

          {analysis.root_cause && (
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Root cause
              </p>
              <p className="text-sm text-gray-800 leading-relaxed">
                {analysis.root_cause}
              </p>
            </div>
          )}

          {analysis.fix && (
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                Fix
              </p>
              <p className="text-sm text-gray-800 leading-relaxed">{analysis.fix}</p>
            </div>
          )}

          {analysis.redaction_count > 0 && (
            <p className="text-xs text-gray-500">
              🔒 {analysis.redaction_count} secret
              {analysis.redaction_count !== 1 ? "s" : ""} redacted before analysis
            </p>
          )}
        </div>
      )}

      {expanded && !analysis && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-sm text-gray-500">No analysis data available.</p>
        </div>
      )}
    </div>
  );
}