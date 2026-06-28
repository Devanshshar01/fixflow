/**
 * API client — sends session token as Authorization: Bearer header.
 * Token is stored in localStorage to work across the onrender.com /
 * vercel.app domain boundary (cross-domain cookies are blocked).
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Token storage ──────────────────────────────────────────────────────────────

export const tokenStore = {
  get(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("fixflow_token");
  },
  set(token: string): void {
    if (typeof window === "undefined") return;
    localStorage.setItem("fixflow_token", token);
  },
  clear(): void {
    if (typeof window === "undefined") return;
    localStorage.removeItem("fixflow_token");
  },
};

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
  min_analysis_ms: number | null;
  max_analysis_ms: number | null;
  rule_engine_rate_pct: number;
  stuck_runs: number;
  by_source: Record<string, number>;
  by_category: { category: string; count: number }[];
}

export interface PatternStat {
  rule_id: string;
  category: string;
  severity: string;
  hit_count: number;
  success_rate: number | null;
  root_cause_template: string;
  fix: string;
  fix_url: string | null;
  updated_at: string;
}

export interface PatternCandidate {
  candidate_id: string;
  pattern: string;
  category: string;
  root_cause: string;
  fix: string;
  created_at: string;
}

// ── Error class ────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Base fetch ─────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = tokenStore.get();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    // Keep credentials for same-domain cookie fallback
    credentials: "include",
  });

  if (res.status === 401) {
    // Token expired or invalid — clear it so the user gets redirected to login
    tokenStore.clear();
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }

  return res.json() as Promise<T>;
}

// ── Auth ───────────────────────────────────────────────────────────────────────

export const authApi = {
  me: (): Promise<User> => apiFetch<User>("/auth/me"),
  loginUrl: (): string => `${API_URL}/auth/login`,
  logout: (): void => {
    tokenStore.clear();
    window.location.href = `${API_URL}/auth/logout`;
  },
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
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const query = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch(`/repositories/${repoId}/failures${query}`);
  },
};

// ── Analytics ──────────────────────────────────────────────────────────────────

export const analyticsApi = {
  summary: (): Promise<AnalyticsSummary> =>
    apiFetch("/analytics/summary"),

  recent: (limit = 20): Promise<{ runs: WorkflowRun[] }> =>
    apiFetch(`/analytics/recent?limit=${limit}`),

  patterns: (limit = 20): Promise<{ patterns: PatternStat[] }> =>
    apiFetch(`/analytics/patterns?limit=${limit}`),

  candidates: (): Promise<{ candidates: PatternCandidate[] }> =>
    apiFetch("/analytics/patterns/candidates"),

  submitCandidate: (body: {
    analysis_id: string;
    proposed_pattern: string;
    notes?: string;
  }): Promise<{ status: string; candidate_id: string; message: string }> =>
    apiFetch("/analytics/patterns/candidate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};