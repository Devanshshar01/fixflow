"use client";

import Link from "next/link";
import { useRepositories } from "@/lib/hooks";
import { GitBranch, AlertCircle, ArrowRight, Plus } from "lucide-react";
import { formatRelative } from "@/lib/utils";

export default function ReposPage() {
  const { repos, installations, loading, error } = useRepositories();

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-36 bg-gray-200 rounded" />
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 bg-gray-200 rounded-lg" />
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
          Failed to load repositories.
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-3xl">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Repositories</h1>
          <p className="text-sm text-gray-500 mt-1">
            {repos.length} repo{repos.length !== 1 ? "s" : ""} monitored
          </p>
        </div>
        <a
          href="https://github.com/settings/apps/fixflow-devanshsharma/installations/new"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-500 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Add repos
        </a>
      </div>

      {repos.length === 0 ? (
        <div className="card text-center py-12">
          <GitBranch className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="font-medium text-gray-900 mb-1">No repositories yet</p>
          <p className="text-sm text-gray-500 mb-6 max-w-sm mx-auto">
            Install FixFlow on a GitHub repository to start monitoring CI
            failures.
          </p>
          <a
            href="https://github.com/settings/apps/fixflow-devanshsharma/installations/new"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-500 transition-colors"
          >
            Install GitHub App
            <ArrowRight className="w-4 h-4" />
          </a>
        </div>
      ) : (
        <div className="space-y-3">
          {repos.map((repo) => (
            <Link
              key={repo.id}
              href={`/dashboard/repos/${repo.id}`}
              className="card flex items-center justify-between hover:border-gray-300 transition-colors group"
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 bg-gray-100 rounded-md flex items-center justify-center flex-shrink-0">
                  <GitBranch className="w-4 h-4 text-gray-500" />
                </div>
                <div className="min-w-0">
                  <p className="font-medium text-gray-900 text-sm truncate">
                    {repo.full_name}
                  </p>
                  <p className="text-xs text-gray-500">
                    {repo.total_failures} failure
                    {repo.total_failures !== 1 ? "s" : ""}
                    {repo.last_failure_at &&
                      ` · last ${formatRelative(repo.last_failure_at)}`}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3 flex-shrink-0">
                {repo.total_failures > 0 && (
                  <span className="badge badge-red">
                    {repo.total_failures}
                  </span>
                )}
                <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-gray-500 transition-colors" />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}