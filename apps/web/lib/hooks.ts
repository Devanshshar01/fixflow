"use client";

import { useEffect, useState, useCallback } from "react";
import { authApi, reposApi, analyticsApi } from "./api";
import type {
  User,
  Repository,
  WorkflowRun,
  AnalyticsSummary,
  Installation,
} from "./api";

// ── useUser ────────────────────────────────────────────────────────────────────

export function useUser() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    authApi
      .me()
      .then(setUser)
      .catch((err) => {
        setError(err);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  return { user, loading, error };
}

// ── useRepositories ────────────────────────────────────────────────────────────

export function useRepositories() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    reposApi
      .list()
      .then((data) => {
        setRepos(data.repositories);
        setInstallations(data.installations);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { repos, installations, loading, error, refresh };
}

// ── useRepoFailures ────────────────────────────────────────────────────────────

export function useRepoFailures(repoId: string, limit = 20, offset = 0) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [total, setTotal] = useState(0);
  const [repoName, setRepoName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!repoId) return;
    setLoading(true);
    reposApi
      .failures(repoId, { limit, offset })
      .then((data) => {
        setRuns(data.failures);
        setTotal(data.total);
        setRepoName(data.repository.full_name);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [repoId, limit, offset]);

  return { runs, total, repoName, loading, error };
}

// ── useAnalyticsSummary ────────────────────────────────────────────────────────

export function useAnalyticsSummary() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    analyticsApi
      .summary()
      .then(setSummary)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  return { summary, loading, error };
}   