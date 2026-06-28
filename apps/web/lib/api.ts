/**
 * Typed API client for FixFlow backend.
 * All requests include credentials (session cookie).
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  github_id: number;
  login: string;
  name: string | null;
  avatar_url: string | null;
  created_at: string;
}

export interface Installation {
  id: string;
  account_login: string;
  account_type: string;
}

export interface Repository {
  id: string;
  full_name: string;
  default_branch: string;
  is_active: boolean;
  added_at: string;
  total_failures: number;
  last_failure_at: string | null;
}

export interface FailureAnalysis {
  category: string;
  source: string;
  confidence: number | null;
  failed_step: string | null;
  root_cause: string | null;
  fix: string | null;
  redaction_count: number;
}

export interface WorkflowRun {
  id: string;
  github_run_id: number;
  workflow_name: string | null;
  head_sha: string | null;
  pr_number: number | null;
  triggered_at: string;
  analyzed_at: string | null;
  analysis_ms: number | null;
  comment_posted: boolean;
  analysis: FailureAnalysis | null;
}

export interface AnalyticsSummary {
  total_analyzed: number;
  last_24h: number;
  avg_analysis_ms: number | null;
  rule_engine_rate_pct: number;
  stuck_runs: number;
  by_source: Record<string, number>;
  by_category: { category: string; count: number }[];
}

// ── Base fetch wrapper ─────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }

  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Auth ───────────────────────────────────────────────────────────────────────

export const authApi = {
  me: (): Promise<User> => apiFetch<User>("/auth/me"),
  loginUrl: (): string => `${API_URL}/auth/login`,
  logoutUrl: (): string => `${API_URL}/auth/logout`,
};

// ── Repositories ───────────────────────────────────────────────────────────────

export const reposApi = {
  list: (): Promise<{ repositories: Repository[]; installations: Installation[] }> =>
    apiFetch("/repositories"),

  failures: (
    repoId: string,
    params?: { limit?: number; offset?: number }
  ): Promise<{
    repository: Pick<Repository, "id" | "full_name" | "default_branch">;
    total: number;
    limit: number;
    offset: number;
    failures: WorkflowRun[];
  }> => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch(`/repositories/${repoId}/failures${query}`);
  },
};

// ── Analytics ──────────────────────────────────────────────────────────────────

export const analyticsApi = {
  summary: (): Promise<AnalyticsSummary> => apiFetch("/analytics/summary"),
  recent: (limit = 20): Promise<{ runs: WorkflowRun[] }> =>
    apiFetch(`/analytics/recent?limit=${limit}`),
};