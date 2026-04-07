/**
 * Shared display-formatting helpers for the Transit Sentinel UI.
 * All functions are pure and free of side-effects.
 */

export const formatPercent = (value?: number | null, digits = 0): string =>
  typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(digits)}%`
    : "n/a";

export const formatHazard = (value?: number | null): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "0.00";

export const formatDelay = (value?: number | null): string => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  const sign = value < 0 ? "-" : "";
  const totalSeconds = Math.abs(Math.round(value));
  if (totalSeconds < 60) return `${sign}${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds ? `${sign}${minutes}m ${seconds}s` : `${sign}${minutes}m`;
};

export const formatHeadway = (value?: number | null): string => {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "n/a";
  const minutes = value / 60;
  return `${minutes.toFixed(minutes >= 10 ? 0 : 1)} min`;
};

export const relativeTime = (timestamp?: string | null): string => {
  if (!timestamp) return "n/a";
  const value = Date.parse(timestamp);
  if (Number.isNaN(value)) return timestamp;
  const diffSeconds = Math.round((Date.now() - value) / 1000);
  if (Math.abs(diffSeconds) < 60) return `${diffSeconds}s ago`;
  if (Math.abs(diffSeconds) < 3600) return `${Math.round(diffSeconds / 60)}m ago`;
  return `${Math.round(diffSeconds / 3600)}h ago`;
};

export const relativeTimeFromMs = (timestampMs?: number | null): string => {
  if (typeof timestampMs !== "number" || !Number.isFinite(timestampMs)) return "n/a";
  return relativeTime(new Date(timestampMs).toISOString());
};

export const humanizeToken = (value?: string | null): string =>
  value ? value.replace(/_/g, " ") : "n/a";

export const actionTone = (action?: string): string => {
  if (action === "dispatch_relief" || action === "short_turn") return "danger";
  if (
    action === "inspect_terminal" ||
    action === "hold" ||
    action === "warn_riders" ||
    action === "mark_feed_degraded"
  ) {
    return "warning";
  }
  return "calm";
};

export type ServiceState = "loading" | "online" | "offline";

export const serviceTone = (state: ServiceState): string => {
  if (state === "online") return "online";
  if (state === "offline") return "offline";
  return "loading";
};

export interface ProvenanceFactor {
  factor: string;
  label?: string;
  score?: number;
  weight?: number;
  weighted_score?: number;
}

export interface ProvenancePayload {
  feature_coverage?: number;
  signal_agreement?: number;
  feed_freshness?: number;
  metrics?: {
    position_coverage?: number;
    trip_update_coverage?: number;
    feed_age_seconds?: number;
  };
  hazard_components?: Record<string, number>;
  top_factors?: ProvenanceFactor[];
}

export const topFactorSummary = (provenance?: ProvenancePayload | null): string => {
  const factors = provenance?.top_factors ?? [];
  if (!factors.length) return "no dominant factors";
  return factors
    .slice(0, 2)
    .map((factor) => factor.label ?? humanizeToken(factor.factor))
    .join(", ");
};
