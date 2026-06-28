import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "-";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function confidenceLabel(confidence: number | null | undefined): {
  label: string;
  className: string;
} {
  if (confidence == null || confidence === 100)
    return { label: "Deterministic", className: "badge-green" };
  if (confidence >= 75)
    return { label: `${confidence}% confident`, className: "badge-green" };
  if (confidence >= 40)
    return { label: `${confidence}% confident`, className: "badge-amber" };
  return { label: `${confidence}% confident`, className: "badge-red" };
}

export function sourceLabel(source: string): string {
  const map: Record<string, string> = {
    rule_engine: "Rule engine",
    gemini: "Gemini AI",
    ollama: "Ollama",
    degraded: "Degraded",
    unknown: "Unknown",
  };
  return map[source] ?? source;
}

export function categoryColor(category: string): string {
  const map: Record<string, string> = {
    node: "badge-blue",
    python: "badge-blue",
    docker: "badge-gray",
    go: "badge-gray",
    rust: "badge-gray",
    java: "badge-gray",
    ruby: "badge-gray",
    testing: "badge-amber",
    permissions: "badge-red",
    secrets: "badge-red",
    other: "badge-gray",
    unknown: "badge-gray",
  };
  return map[category] ?? "badge-gray";
}

export function severityColor(severity: string): string {
  const map: Record<string, string> = {
    critical: "badge-red",
    high: "badge-amber",
    medium: "badge-blue",
    low: "badge-gray",
  };
  return map[severity] ?? "badge-gray";
}

export function pluralize(
  count: number,
  singular: string,
  plural?: string
): string {
  return count === 1 ? singular : plural ?? `${singular}s`;
}