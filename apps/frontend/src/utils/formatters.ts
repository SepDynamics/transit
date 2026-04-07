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

const ACTION_LABELS: Record<string, string> = {
  dispatch_relief: "Dispatch relief",
  short_turn: "Short turn",
  inspect_terminal: "Inspect terminal",
  hold: "Hold to rebalance",
  warn_riders: "Publish rider advisory",
  mark_feed_degraded: "Mark telemetry degraded",
  monitor: "Monitor",
};

const REGIME_LABELS: Record<string, string> = {
  healthy: "Stable service",
  recovering: "Recovering service",
  data_sparse: "Limited telemetry",
  bunching_onset: "Early bunching",
  corridor_unstable: "Service irregularity",
  headway_collapse: "Severe bunching / service gap",
  service_degraded: "Confirmed disruption",
  terminal_congestion: "Terminal congestion",
  stop_dwell_instability: "Extended dwell",
  terminal_blocked: "Terminal blocked",
  feed_incoherent: "Telemetry degraded",
};

const ACTIVITY_STATUS_LABELS: Record<string, string> = {
  active_now: "Active now",
  scheduled_later: "Scheduled later",
  inactive: "Inactive",
};

const ACTIVITY_REASON_LABELS: Record<string, string> = {
  live_telemetry: "Live telemetry present",
  scheduled_no_telemetry: "Scheduled with no live telemetry",
  service_starts_later: "Service starts later today",
  returns_next_service_day: "Returns next service day",
  inactive: "Outside the service window",
};

export const formatActionLabel = (
  action?: string | null,
  fallback?: string | null,
): string => fallback || (action ? ACTION_LABELS[action] ?? humanizeToken(action) : "Monitor");

export const formatRegimeLabel = (
  regime?: string | null,
  fallback?: string | null,
): string =>
  fallback || (regime ? REGIME_LABELS[regime] ?? humanizeToken(regime) : "Stable service");

export const formatActivityStatusLabel = (
  status?: string | null,
  fallback?: string | null,
): string =>
  fallback ||
  (status ? ACTIVITY_STATUS_LABELS[status] ?? humanizeToken(status) : "n/a");

export const formatActivityReasonLabel = (
  reason?: string | null,
  fallback?: string | null,
): string =>
  fallback ||
  (reason ? ACTIVITY_REASON_LABELS[reason] ?? humanizeToken(reason) : "n/a");

export const formatPriorityLabel = (
  priorityScore?: number | null,
  fallback?: string | null,
): string => {
  if (fallback) return fallback;
  if (typeof priorityScore !== "number" || !Number.isFinite(priorityScore)) return "Monitor";
  if (priorityScore >= 85) return "Immediate";
  if (priorityScore >= 65) return "High";
  if (priorityScore >= 45) return "Watch";
  return "Monitor";
};

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

export const priorityTone = (
  priorityScore?: number | null,
  fallback?: string | null,
): string => {
  const label = formatPriorityLabel(priorityScore, fallback).toLowerCase();
  if (label === "immediate") return "danger";
  if (label === "high" || label === "watch") return "warning";
  return "calm";
};

type OperationalPriorityRow = {
  priority_score?: number | null;
  hazard?: number | null;
  avg_hazard?: number | null;
  latest_hazard?: number | null;
  active_alert_count?: number | null;
  median_delay_seconds?: number | null;
  latest_delay_seconds?: number | null;
  timestamp_ms?: number | null;
  label?: string | null;
};

const numberOrZero = (value?: number | null): number =>
  typeof value === "number" && Number.isFinite(value) ? value : 0;

export const compareOperationalPriority = (
  left: OperationalPriorityRow,
  right: OperationalPriorityRow,
): number =>
  numberOrZero(right.priority_score) - numberOrZero(left.priority_score) ||
  numberOrZero(right.hazard ?? right.avg_hazard ?? right.latest_hazard) -
    numberOrZero(left.hazard ?? left.avg_hazard ?? left.latest_hazard) ||
  numberOrZero(right.active_alert_count) - numberOrZero(left.active_alert_count) ||
  numberOrZero(right.median_delay_seconds ?? right.latest_delay_seconds) -
    numberOrZero(left.median_delay_seconds ?? left.latest_delay_seconds) ||
  numberOrZero(right.timestamp_ms) - numberOrZero(left.timestamp_ms) ||
  String(left.label ?? "").localeCompare(String(right.label ?? ""));

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
