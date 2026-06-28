"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, AlertCircle, Inbox } from "lucide-react";
import { useRepoFailures } from "@/lib/hooks";
import FailureCard from "@/components/FailureCard";

const PAGE_SIZE = 20;

export default function RepoFailuresPage() {
  const { id } = useParams<{ id: string }>();
  const [offset, setOffset] = useState(0);

  const { runs, total, repoName, loading, error } = useRepoFailures(
    id,
    PAGE_SIZE,
    offset
  );

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-6 w-48 bg-gray-200 rounded" />
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-20 bg-gray-200 rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="flex items-center gap-2 text-red-600 text-sm">
          <AlertCircle className="w-4 h-4" />
          Failed to load failures.
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-3xl">
      {/* Header */}
      <div className="mb-8">
        <Link
          href="/dashboard/repos"
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Repositories
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 font-mono">
          {repoName}
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {total} failure{total !== 1 ? "s" : ""} analyzed
        </p>
      </div>

      {/* Failures list */}
      {runs.length === 0 ? (
        <div className="card text-center py-12">
          <Inbox className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="font-medium text-gray-900 mb-1">No failures yet</p>
          <p className="text-sm text-gray-500 max-w-xs mx-auto">
            FixFlow will analyze failures and show them here when your CI
            breaks.
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3 mb-6">
            {runs.map((run) => (
              <FailureCard key={run.id} run={run} repoFullName={repoName} />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm">
              <p className="text-gray-500">
                Page {currentPage} of {totalPages}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  disabled={offset === 0}
                  className="px-3 py-1.5 rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Previous
                </button>
                <button
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  disabled={currentPage >= totalPages}
                  className="px-3 py-1.5 rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}