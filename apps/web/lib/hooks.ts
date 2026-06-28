"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { authApi, reposApi, analyticsApi } from "./api";
import type {
  User,
  Repository,
  WorkflowRun,
  AnalyticsSummary,
  Installation,
  PatternStat,
} from "./api";

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

export function useRepoFailures(
  repoId: string,
  limit = 20,
  offset = 0,
  pollIntervalMs = 30_000
) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [total, setTotal] = useState(0);
  const [repoName, setRepoName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchData = useCallback(() => {
    if (!repoId) return;
    reposApi
      .failures(repoId, { limit, offset })
      .then((data) => {
        setRuns(data.failures);
        setTotal(data.total);
        setRepoName(data.repository.full_name);
        setError(null);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [repoId, limit, offset]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    timerRef.current = setInterval(fetchData, pollIntervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchData, pollIntervalMs]);

  return { runs, total, repoName, loading, error, refresh: fetchData };
}

export function useAnalyticsSummary(pollIntervalMs = 60_000) {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = useCallback(() => {
    analyticsApi
      .summary()
      .then((data) => {
        setSummary(data);
        setError(null);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, pollIntervalMs);
    return () => clearInterval(timer);
  }, [fetchData, pollIntervalMs]);

  return { summary, loading, error, refresh: fetchData };
}

export function usePatternStats(limit = 20) {
  const [patterns, setPatterns] = useState<PatternStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    analyticsApi
      .patterns(limit)
      .then((data) => setPatterns(data.patterns))
      .catch(setError)
      .finally(() => setLoading(false));
  }, [limit]);

  return { patterns, loading, error };
}