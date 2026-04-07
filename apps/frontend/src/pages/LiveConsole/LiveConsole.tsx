import { lazy, Suspense, useEffect, useState } from "react";
import type {
  CorridorHistoryResponse,
  EntitiesResponse,
  IncidentResponse,
  IncidentPayload,
  LineCard,
  ProvenancePayload,
  RegimeResponse,
  ScorecardResponse,
  SourceResponse,
  TransitHealth,
  TransitMapResponse,
  TransitReplayTrace,
  TrendResponse,
  VehicleCard,
  VehicleHistoryResponse,
} from "../../types/transit";
import "./LiveConsole.css";

const TransitMap = lazy(() => import("../../components/TransitMap"));

declare global {
  interface Window {
    __TRANSIT_SENTINEL_CONFIG__?: Record<string, unknown>;
  }
}

type RuntimeConfig = {
  API_URL?: string;
  API_BEARER_TOKEN?: string;
};

const runtimeConfig: RuntimeConfig =
  (typeof window !== "undefined"
    ? (window.__TRANSIT_SENTINEL_CONFIG__ as RuntimeConfig | undefined)
    : undefined) || {};

const stringOrUndefined = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined;

const API_BASE = stringOrUndefined(runtimeConfig.API_URL) ?? (import.meta.env.VITE_API_HOST || "");
const API_BEARER_TOKEN = stringOrUndefined(runtimeConfig.API_BEARER_TOKEN) ?? "";
const normalisedBase = API_BASE ? API_BASE.replace(/\/$/, "") : "";

const buildApiUrl = (path: string): string => (/^https?:\/\//i.test(path) ? path : `${normalisedBase}${path}`);

const buildTransitQuery = (scope: string, traceId?: string | null): string => {
  const params = new URLSearchParams();
  params.set("scope", scope);
  if (traceId) {
    params.set("trace_id", traceId);
  }
  return params.toString();
};

async function fetchJson<T>(path: string): Promise<T> {
  const headers = new Headers();
  if (API_BEARER_TOKEN) {
    headers.set("Authorization", `Bearer ${API_BEARER_TOKEN}`);
  }
  const response = await fetch(buildApiUrl(path), { headers });
  if (!response.ok) {
    throw new Error(path.replace(/^\//, ""));
  }
  return (await response.json()) as T;
}

type ServiceState = "loading" | "online" | "offline";

const formatPercent = (value?: number | null, digits = 0): string =>
  typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "n/a";

const formatHazard = (value?: number | null): string =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "0.00";

const formatDelay = (value?: number | null): string => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  const sign = value < 0 ? "-" : "";
  const totalSeconds = Math.abs(Math.round(value));
  if (totalSeconds < 60) return `${sign}${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds ? `${sign}${minutes}m ${seconds}s` : `${sign}${minutes}m`;
};

const formatHeadway = (value?: number | null): string => {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "n/a";
  const minutes = value / 60;
  return `${minutes.toFixed(minutes >= 10 ? 0 : 1)} min`;
};

const relativeTime = (timestamp?: string | null): string => {
  if (!timestamp) return "n/a";
  const value = Date.parse(timestamp);
  if (Number.isNaN(value)) return timestamp;
  const diffSeconds = Math.round((Date.now() - value) / 1000);
  if (Math.abs(diffSeconds) < 60) return `${diffSeconds}s ago`;
  if (Math.abs(diffSeconds) < 3600) return `${Math.round(diffSeconds / 60)}m ago`;
  return `${Math.round(diffSeconds / 3600)}h ago`;
};

const relativeTimeFromMs = (timestampMs?: number | null): string => {
  if (typeof timestampMs !== "number" || !Number.isFinite(timestampMs)) return "n/a";
  return relativeTime(new Date(timestampMs).toISOString());
};

const traceOptionLabel = (trace: TransitReplayTrace): string => {
  const parts = [trace.trace_id];
  if (typeof trace.snapshot_count === "number" && trace.snapshot_count > 0) {
    parts.push(`${trace.snapshot_count} snapshots`);
  }
  if (trace.latest_snapshot_timestamp_ms) {
    parts.push(relativeTimeFromMs(trace.latest_snapshot_timestamp_ms));
  }
  return parts.join(" • ");
};

const replayTraces = (payload: SourceResponse): TransitReplayTrace[] =>
  payload.traces?.length ? payload.traces : (payload.trace_ids ?? []).map((trace_id) => ({ trace_id }));

const actionTone = (action?: string): string => {
  if (action === "dispatch_relief" || action === "short_turn") return "danger";
  if (action === "inspect_terminal" || action === "hold" || action === "warn_riders" || action === "mark_feed_degraded") {
    return "warning";
  }
  return "calm";
};

const serviceTone = (state: ServiceState): string => {
  if (state === "online") return "online";
  if (state === "offline") return "offline";
  return "loading";
};

const humanizeToken = (value?: string | null): string => (value ? value.replace(/_/g, " ") : "n/a");

const topFactorSummary = (provenance?: ProvenancePayload | null): string => {
  const factors = provenance?.top_factors ?? [];
  if (!factors.length) return "no dominant factors";
  return factors
    .slice(0, 2)
    .map((factor) => factor.label ?? humanizeToken(factor.factor))
    .join(", ");
};

export default function LiveConsole() {
  const [serviceState, setServiceState] = useState<ServiceState>("loading");
  const [transitHealth, setTransitHealth] = useState<TransitHealth | null>(null);
  const [entities, setEntities] = useState<EntitiesResponse>({
    lines: [],
    active_lines: [],
    scheduled_later_lines: [],
    inactive_lines: [],
    vehicles: [],
  });
  const [regimeResponse, setRegimeResponse] = useState<RegimeResponse>({ regimes: [], recurring_regimes: [] });
  const [incidentResponse, setIncidentResponse] = useState<IncidentResponse>({ incidents: [] });
  const [trendResponse, setTrendResponse] = useState<TrendResponse>({
    summary: {
      corridor_count: 0,
      unstable_corridor_count: 0,
      recent_incident_count: 0,
      recent_action_counts: {},
      recent_regime_counts: {},
    },
    corridors: [],
  });
  const [sourceResponse, setSourceResponse] = useState<SourceResponse>({
    scopes: [
      { id: "all", label: "All feeds" },
      { id: "live", label: "Live feed" },
    ],
    traces: [],
  });
  const [scope, setScope] = useState("all");
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedCorridorId, setSelectedCorridorId] = useState<string | null>(null);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [vehicleHistory, setVehicleHistory] = useState<VehicleHistoryResponse>({ observations: [], regimes: [], incidents: [] });
  const [corridorHistory, setCorridorHistory] = useState<CorridorHistoryResponse>({
    observations: [],
    regimes: [],
    incidents: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [mapData, setMapData] = useState<TransitMapResponse | null>(null);
  const [scorecardResponse, setScorecardResponse] = useState<ScorecardResponse | null>(null);

  useEffect(() => {
    let active = true;
    const loadSources = async () => {
      try {
        const payload = await fetchJson<SourceResponse>("/api/transit/sources");
        if (!active) return;
        setSourceResponse(payload);
      } catch {
        if (!active) return;
      }
    };
    loadSources();
    const timer = window.setInterval(loadSources, 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const traceIds = replayTraces(sourceResponse).map((trace) => trace.trace_id).filter(Boolean);
    if (scope === "live") {
      if (selectedTraceId) {
        setSelectedTraceId("");
      }
      return;
    }
    if (selectedTraceId && !traceIds.includes(selectedTraceId)) {
      setSelectedTraceId("");
    }
  }, [scope, selectedTraceId, sourceResponse.trace_ids, sourceResponse.traces]);

  useEffect(() => {
    let active = true;
    const loadDashboard = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const [healthPayload, entitiesPayload, regimePayload, incidentPayload, trendPayload] = await Promise.all([
          fetchJson<TransitHealth>(`/api/transit/health?${query}`),
          fetchJson<EntitiesResponse>(`/api/transit/entities?${query}`),
          fetchJson<RegimeResponse>(`/api/transit/regimes?${query}`),
          fetchJson<IncidentResponse>(`/api/transit/incidents?${query}`),
          fetchJson<TrendResponse>(`/api/transit/trends?${query}`),
        ]);
        if (!active) return;
        setTransitHealth(healthPayload);
        setEntities(entitiesPayload);
        setRegimeResponse(regimePayload);
        setIncidentResponse(incidentPayload);
        setTrendResponse(trendPayload);
        setServiceState("online");
        setError(null);
      } catch (loadError) {
        if (!active) return;
        setServiceState("offline");
        setError(loadError instanceof Error ? loadError.message : "transit api unavailable");
      }
    };
    loadDashboard();
    const timer = window.setInterval(loadDashboard, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  useEffect(() => {
    let active = true;
    const loadScorecard = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<ScorecardResponse>(`/api/transit/scorecard?${query}&limit=720`);
        if (!active) return;
        setScorecardResponse(payload);
      } catch {
        if (!active) return;
      }
    };
    loadScorecard();
    const timer = window.setInterval(loadScorecard, 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  useEffect(() => {
    if (!entities.lines.length) {
      setSelectedCorridorId(null);
      return;
    }
    const selectionExists = entities.lines.some((line) => line.entity_id === selectedCorridorId);
    if (!selectionExists) {
      setSelectedCorridorId(entities.active_lines?.[0]?.entity_id ?? entities.lines[0].entity_id);
    }
  }, [entities.active_lines, entities.lines, selectedCorridorId]);

  useEffect(() => {
    if (!entities.vehicles.length) {
      setSelectedEntityId(null);
      return;
    }
    const selectionExists = entities.vehicles.some((vehicle) => vehicle.entity_id === selectedEntityId);
    if (!selectionExists) {
      setSelectedEntityId(entities.vehicles[0].entity_id);
    }
  }, [entities.vehicles, selectedEntityId]);

  useEffect(() => {
    if (!selectedEntityId) {
      setVehicleHistory({ observations: [], regimes: [], incidents: [] });
      return;
    }
    let active = true;
    const loadVehicleHistory = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<VehicleHistoryResponse>(
          `/api/transit/history?${query}&entity_id=${encodeURIComponent(selectedEntityId)}&limit=72`,
        );
        if (!active) return;
        setVehicleHistory(payload);
      } catch {
        if (!active) return;
      }
    };
    loadVehicleHistory();
    const timer = window.setInterval(loadVehicleHistory, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId, selectedEntityId]);

  useEffect(() => {
    if (!selectedCorridorId) {
      setCorridorHistory({ observations: [], regimes: [], incidents: [] });
      return;
    }
    let active = true;
    const loadCorridorHistory = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<CorridorHistoryResponse>(
          `/api/transit/history?${query}&entity_id=${encodeURIComponent(selectedCorridorId)}&limit=72`,
        );
        if (!active) return;
        setCorridorHistory(payload);
      } catch {
        if (!active) return;
      }
    };
    loadCorridorHistory();
    const timer = window.setInterval(loadCorridorHistory, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedCorridorId, selectedTraceId]);

  // Map data — polled on the same cadence as the main dashboard
  useEffect(() => {
    let active = true;
    const loadMap = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<TransitMapResponse>(`/api/transit/map?${query}`);
        if (!active) return;
        setMapData(payload);
      } catch {
        // Map data is best-effort; don't disrupt the main dashboard on failure
        if (!active) return;
      }
    };
    loadMap();
    const timer = window.setInterval(loadMap, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  const activeLines = entities.active_lines ?? entities.lines;
  const scheduledLaterLines = entities.scheduled_later_lines ?? [];
  const selectedVehicle = entities.vehicles.find((vehicle) => vehicle.entity_id === selectedEntityId) || null;
  const selectedCorridor =
    entities.lines.find((line) => line.entity_id === selectedCorridorId) ||
    (corridorHistory.entity ? { ...corridorHistory.entity, label: corridorHistory.entity.label ?? "Unknown corridor" } : null);
  const selectedRegime = selectedVehicle?.regime ?? null;
  const selectedCorridorRegime =
    corridorHistory.regimes[corridorHistory.regimes.length - 1] ||
    regimeResponse.regimes.find((regime) => regime.entity_id === selectedCorridorId) ||
    null;
  const corridorTimelinePoints = corridorHistory.regimes.slice(-36);
  const corridorIncidents = corridorHistory.incidents.slice(-3).reverse();
  const observationPoints = vehicleHistory.observations.slice(-12);
  const vehiclesOnSelectedCorridor = selectedCorridorId
    ? entities.vehicles.filter((vehicle) => vehicle.corridor_entity_id === selectedCorridorId)
    : [];
  const scorecardTopCorridors = scorecardResponse?.corridors.slice(0, 4) ?? [];

  const selectCorridor = (corridorEntityId: string, preferredVehicleId?: string | null) => {
    if (!corridorEntityId) {
      if (preferredVehicleId) {
        setSelectedEntityId(preferredVehicleId);
      }
      return;
    }
    setSelectedCorridorId(corridorEntityId);
    if (preferredVehicleId) {
      setSelectedEntityId(preferredVehicleId);
      return;
    }
    if (selectedVehicle?.corridor_entity_id === corridorEntityId) {
      return;
    }
    const vehicle = entities.vehicles.find((item) => item.corridor_entity_id === corridorEntityId);
    if (vehicle) {
      setSelectedEntityId(vehicle.entity_id);
    }
  };

  const renderCorridorCards = (lines: LineCard[], emptyLabel: string) => (
    <div className="node-grid">
      {lines.map((line) => (
        <button
          key={line.entity_id}
          type="button"
          className={line.entity_id === selectedCorridorId ? "node-card is-active" : "node-card"}
          onClick={() => selectCorridor(line.entity_id)}
        >
          <div className="node-card__header">
            <strong>{line.label}</strong>
            <span className={`badge badge--${actionTone(line.top_action)}`}>{humanizeToken(line.top_action)}</span>
          </div>
          <div className="node-card__stats">
            <div>
              <span>Vehicles</span>
              <strong>{line.vehicle_count}</strong>
            </div>
            <div>
              <span>Median delay</span>
              <strong>{formatDelay(line.median_delay_seconds)}</strong>
            </div>
            <div>
              <span>Sched headway</span>
              <strong>{formatHeadway(line.scheduled_headway_seconds)}</strong>
            </div>
            <div>
              <span>Hazard</span>
              <strong>{formatHazard(line.avg_hazard)}</strong>
            </div>
            <div>
              <span>Alerts</span>
              <strong>{line.active_alert_count}</strong>
            </div>
          </div>
          <div className="signature-card__meta">
            <span>{humanizeToken(line.activity_status)}</span>
            <span>{humanizeToken(line.activity_reason)}</span>
          </div>
        </button>
      ))}
      {!lines.length ? <div className="empty-state">{emptyLabel}</div> : null}
    </div>
  );

  return (
    <main className="sentinel">
      <div className="sentinel__shell">
        <section className="hero panel">
          <div className="hero__copy">
            <div className="hero__eyebrow">Transit Sentinel</div>
            <h1 className="hero__title">Detect corridor instability before service fully collapses.</h1>
            <p className="hero__summary">
              GTFS schedule and GTFS-RT feeds are normalized into route, corridor, and vehicle signals, then translated
              into operator-facing actions.
            </p>
            <div className="hero__chips">
              <span className="chip">GTFS static schedule</span>
              <span className="chip">GTFS-RT vehicle feeds</span>
              <span className="chip">Operator actions</span>
            </div>
          </div>
          <div className="hero__rail">
            <div className={`status-pill status-pill--${serviceTone(serviceState)}`}>
              <span className="status-pill__dot" />
              API {serviceState}
            </div>
            <div className="hero__meta">
              <span>Feed source</span>
              <strong>{transitHealth?.feed_status?.collection_source ?? "awaiting feed"}</strong>
            </div>
            <div className="hero__meta">
              <span>Last feed tick</span>
              <strong>{relativeTime(transitHealth?.feed_status?.updated_at)}</strong>
            </div>
            <div className="hero__meta">
              <span>Visible vehicles</span>
              <strong>{transitHealth?.feed_status?.vehicle_count ?? 0}</strong>
            </div>
            <div className="hero__meta">
              <span>Active alerts</span>
              <strong>{transitHealth?.feed_status?.alert_count ?? 0}</strong>
            </div>
          </div>
        </section>

        <section className="toolbar panel">
          <div>
            <h2 className="section__title">View scope</h2>
            <p className="section__hint">Switch between the configured transit feed views.</p>
          </div>
          <div className="toolbar__controls">
            {replayTraces(sourceResponse).length ? (
              <label className="trace-picker">
                <span>Replay trace</span>
                <select value={selectedTraceId} onChange={(event) => setSelectedTraceId(event.target.value)} disabled={scope === "live"}>
                  <option value="">All traces</option>
                  {replayTraces(sourceResponse).map((trace) => (
                    <option key={trace.trace_id} value={trace.trace_id}>
                      {traceOptionLabel(trace)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <div className="toggle-strip">
              {sourceResponse.scopes.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={option.id === scope ? "toggle-strip__button is-active" : "toggle-strip__button"}
                  onClick={() => setScope(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </section>

        {error ? (
          <section className="panel error-banner">
            <strong>Dashboard degraded.</strong> <code>{error}</code>
          </section>
        ) : null}

        <section className="overview-grid">
          <article className="metric-card panel">
            <span className="metric-card__label">Active now</span>
            <strong className="metric-card__value">{transitHealth?.active_line_count ?? transitHealth?.line_count ?? 0}</strong>
            <span className="metric-card__meta">{transitHealth?.vehicle_count ?? 0} visible vehicles</span>
          </article>
          <article className="metric-card panel">
            <span className="metric-card__label">Scheduled later</span>
            <strong className="metric-card__value">{transitHealth?.scheduled_later_line_count ?? 0}</strong>
            <span className="metric-card__meta">{transitHealth?.visible_line_count ?? 0} surfaced corridors</span>
          </article>
          <article className="metric-card panel">
            <span className="metric-card__label">Average hazard</span>
            <strong className="metric-card__value">{formatHazard(transitHealth?.avg_hazard)}</strong>
            <span className="metric-card__meta">max {formatHazard(transitHealth?.max_hazard)}</span>
          </article>
          <article className="metric-card panel">
            <span className="metric-card__label">Open incidents</span>
            <strong className="metric-card__value">{transitHealth?.incident_count ?? 0}</strong>
            <span className="metric-card__meta">{transitHealth?.critical_incidents ?? 0} critical</span>
          </article>
          <article className="metric-card panel">
            <span className="metric-card__label">Score confidence</span>
            <strong className="metric-card__value">{formatPercent((transitHealth?.avg_confidence ?? 0) * 100)}</strong>
            <span className="metric-card__meta">
              {Object.entries(transitHealth?.regime_counts ?? {})
                .map(([regime, count]) => `${humanizeToken(regime)} ${count}`)
                .join(" • ") || "no corridor mix yet"}
            </span>
          </article>
          <article className="metric-card panel">
            <span className="metric-card__label">Worst corridor</span>
            <strong className="metric-card__value">{transitHealth?.worst_corridor?.label ?? "n/a"}</strong>
            <span className="metric-card__meta">{humanizeToken(transitHealth?.worst_corridor?.regime)}</span>
          </article>
        </section>

        <section className="section panel">
          <div className="section__header">
            <div>
              <h2 className="section__title">Live map</h2>
              <p className="section__hint">
                Vehicle positions colored by corridor regime. Click a vehicle for details.
              </p>
            </div>
          </div>
          <div className="map-container">
            <Suspense fallback={<div className="empty-state">Loading map...</div>}>
              <TransitMap
                mapData={mapData}
                defaultCenter={[-71.0589, 42.3601]}
                defaultZoom={11}
                style={{ width: "100%", height: "480px", borderRadius: 8, overflow: "hidden" }}
              />
            </Suspense>
          </div>
        </section>

        <section className="section split-grid">
          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Network scorecard</h2>
                <p className="section__hint">
                  Rolling public-data KPI summary over the persisted scorecard window.
                </p>
              </div>
            </div>
            <div className="detail-grid detail-grid--expanded">
              <div className="detail-card">
                <span>Window snapshots</span>
                <strong>{scorecardResponse?.window_snapshots ?? 0}</strong>
              </div>
              <div className="detail-card">
                <span>Tracked corridors</span>
                <strong>{scorecardResponse?.corridor_count ?? 0}</strong>
              </div>
              <div className="detail-card">
                <span>Total incidents</span>
                <strong>{scorecardResponse?.total_incidents ?? 0}</strong>
              </div>
              <div className="detail-card">
                <span>Average hazard</span>
                <strong>{formatHazard(scorecardResponse?.network.avg_hazard)}</strong>
              </div>
              <div className="detail-card">
                <span>Average delay</span>
                <strong>{formatDelay(scorecardResponse?.network.avg_delay_seconds)}</strong>
              </div>
              <div className="detail-card">
                <span>On-time proxy</span>
                <strong>{formatPercent(scorecardResponse?.network.on_time_pct, 1)}</strong>
              </div>
            </div>
            <div className="signature-list">
              <article className="signature-card">
                <div className="signature-card__header">
                  <strong>Network action mix</strong>
                  <span>{Object.keys(scorecardResponse?.network.top_actions ?? {}).length} actions</span>
                </div>
                <p>
                  {Object.entries(scorecardResponse?.network.top_actions ?? {})
                    .map(([action, count]) => `${humanizeToken(action)} ${count}`)
                    .join(" • ") || "No rolling action history yet."}
                </p>
              </article>
              <article className="signature-card">
                <div className="signature-card__header">
                  <strong>Network regime mix</strong>
                  <span>{scorecardResponse?.network.unstable_corridor_count ?? 0} unstable corridors</span>
                </div>
                <p>
                  {Object.entries(scorecardResponse?.network.top_regimes ?? {})
                    .map(([regime, count]) => `${humanizeToken(regime)} ${count}`)
                    .join(" • ") || "No rolling regime history yet."}
                </p>
              </article>
            </div>
          </article>

          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Corridor scorecard watchlist</h2>
                <p className="section__hint">
                  Highest-risk corridors over the rolling scorecard window.
                </p>
              </div>
            </div>
            <div className="scorecard-list">
              {scorecardTopCorridors.map((corridor) => (
                <button
                  key={corridor.entity_id}
                  type="button"
                  className={corridor.entity_id === selectedCorridorId ? "scorecard-card is-active" : "scorecard-card"}
                  onClick={() => selectCorridor(corridor.entity_id)}
                >
                  <div className="scorecard-card__header">
                    <strong>{corridor.label}</strong>
                    <span className={`badge badge--${actionTone(corridor.top_action)}`}>{humanizeToken(corridor.top_action)}</span>
                  </div>
                  <div className="scorecard-card__stats">
                    <div>
                      <span>Avg hazard</span>
                      <strong>{formatHazard(corridor.avg_hazard)}</strong>
                    </div>
                    <div>
                      <span>P90 hazard</span>
                      <strong>{formatHazard(corridor.hazard_p90)}</strong>
                    </div>
                    <div>
                      <span>Avg delay</span>
                      <strong>{formatDelay(corridor.avg_delay_seconds)}</strong>
                    </div>
                    <div>
                      <span>On-time</span>
                      <strong>{formatPercent(corridor.on_time_pct, 1)}</strong>
                    </div>
                    <div>
                      <span>Incidents</span>
                      <strong>{corridor.incident_count}</strong>
                    </div>
                    <div>
                      <span>Snapshots</span>
                      <strong>{corridor.snapshot_count}</strong>
                    </div>
                  </div>
                  <div className="signature-card__meta">
                    <span>{humanizeToken(corridor.top_regime)}</span>
                    <span>{formatPercent(corridor.unstable_pct, 1)} unstable</span>
                    <span>{formatPercent(corridor.healthy_pct, 1)} healthy</span>
                  </div>
                </button>
              ))}
              {!scorecardTopCorridors.length ? (
                <div className="empty-state">No scorecard history yet for the selected scope.</div>
              ) : null}
            </div>
          </article>
        </section>

        <section className="section panel">
          <div className="section__header">
            <div>
              <h2 className="section__title">Corridor overview</h2>
              <p className="section__hint">Current route-level rollups split between live telemetry and later scheduled service.</p>
            </div>
          </div>
          <div className="section__header">
            <div>
              <h3 className="section__title">Active Now</h3>
              <p className="section__hint">Corridors with current vehicle or trip telemetry in the selected scope.</p>
            </div>
          </div>
          {renderCorridorCards(activeLines, "No active corridors with live telemetry.")}
          <div className="section__header">
            <div>
              <h3 className="section__title">Scheduled Later</h3>
              <p className="section__hint">Corridors without live telemetry right now that are expected to return later.</p>
            </div>
          </div>
          {renderCorridorCards(scheduledLaterLines, "No scheduled-later corridors in this snapshot.")}
        </section>

        <section className="section split-grid">
          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Corridor trend watch</h2>
                <p className="section__hint">
                  Rolling corridor memory from the persisted transit store, ordered by current instability.
                </p>
              </div>
            </div>
            <div className="signature-list">
              {trendResponse.corridors.map((corridor) => (
                <button
                  key={corridor.entity_id}
                  type="button"
                  className={corridor.entity_id === selectedCorridorId ? "trend-card is-active" : "trend-card"}
                  onClick={() => selectCorridor(corridor.entity_id)}
                >
                  <div className="signature-card__header">
                    <strong>{corridor.label}</strong>
                    <span className={`badge badge--${actionTone(corridor.latest_action)}`}>{humanizeToken(corridor.latest_action)}</span>
                  </div>
                  <div className="trend-card__stats">
                    <div>
                      <span>Latest hazard</span>
                      <strong>{formatHazard(corridor.latest_hazard)}</strong>
                    </div>
                    <div>
                      <span>Recent incidents</span>
                      <strong>{corridor.incident_count}</strong>
                    </div>
                    <div>
                      <span>Median delay</span>
                      <strong>{formatDelay(corridor.latest_delay_seconds)}</strong>
                    </div>
                    <div>
                      <span>Snapshots</span>
                      <strong>{corridor.snapshot_count}</strong>
                    </div>
                  </div>
                  <div className="trend-sparkline">
                    {corridor.hazard_series.map((value, index) => (
                      <span
                        key={`${corridor.entity_id}-${index}`}
                        className={`trend-sparkline__bar trend-sparkline__bar--${value >= 0.75 ? "danger" : value >= 0.45 ? "warning" : "calm"}`}
                        style={{ height: `${16 + Math.max(0, value) * 84}%` }}
                      />
                    ))}
                  </div>
                  <div className="signature-card__meta">
                    <span>{humanizeToken(corridor.latest_regime)}</span>
                    <span>{corridor.recent_actions.map(humanizeToken).join(" • ") || "steady action mix"}</span>
                    <span>{humanizeToken(corridor.latest_activity_status)}</span>
                  </div>
                </button>
              ))}
              {!trendResponse.corridors.length ? <div className="empty-state">No corridor trend history yet.</div> : null}
            </div>
          </article>

          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Trend summary</h2>
                <p className="section__hint">Recent corridor and action mix over the rolling store window.</p>
              </div>
            </div>
            <div className="detail-grid detail-grid--expanded">
              <div className="detail-card">
                <span>Tracked corridors</span>
                <strong>{trendResponse.summary.corridor_count}</strong>
              </div>
              <div className="detail-card">
                <span>Unstable now</span>
                <strong>{trendResponse.summary.unstable_corridor_count}</strong>
              </div>
              <div className="detail-card">
                <span>Recent incidents</span>
                <strong>{trendResponse.summary.recent_incident_count}</strong>
              </div>
            </div>
            <div className="signature-list">
              <article className="signature-card">
                <div className="signature-card__header">
                  <strong>Recent action mix</strong>
                  <span>{Object.keys(trendResponse.summary.recent_action_counts).length} actions</span>
                </div>
                <p>
                  {Object.entries(trendResponse.summary.recent_action_counts)
                    .map(([action, count]) => `${humanizeToken(action)} ${count}`)
                    .join(" • ") || "No recent action memory yet."}
                </p>
              </article>
              <article className="signature-card">
                <div className="signature-card__header">
                  <strong>Recent regime mix</strong>
                  <span>{Object.keys(trendResponse.summary.recent_regime_counts).length} regimes</span>
                </div>
                <p>
                  {Object.entries(trendResponse.summary.recent_regime_counts)
                    .map(([regime, count]) => `${humanizeToken(regime)} ${count}`)
                    .join(" • ") || "No recent regime memory yet."}
                </p>
              </article>
            </div>
          </article>
        </section>

        <section className="section split-grid">
          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Selected corridor memory</h2>
                <p className="section__hint">
                  {selectedCorridor
                    ? `${selectedCorridor.label ?? "Unknown corridor"} with ${vehiclesOnSelectedCorridor.length} visible vehicles in this scope`
                    : "Select a corridor above"}
                </p>
              </div>
            </div>
            <div className="timeline">
              <div className="timeline__bars">
                {corridorTimelinePoints.map((point, index) => (
                  <div
                    key={`${point.signature ?? "point"}-${index}`}
                    className={`timeline__bar timeline__bar--${actionTone(point.action)}`}
                    style={{ height: `${12 + Math.max(0, Number(point.hazard ?? 0)) * 88}%` }}
                    title={`${point.regime ?? "unknown"} ${formatHazard(point.hazard)}`}
                  />
                ))}
              </div>
            </div>
            <div className="detail-grid">
              <div className="detail-card">
                <span>Regime</span>
                <strong>{humanizeToken(selectedCorridorRegime?.regime ?? selectedCorridor?.activity_status)}</strong>
              </div>
              <div className="detail-card">
                <span>Hazard</span>
                <strong>{formatHazard(selectedCorridorRegime?.hazard ?? selectedCorridor?.avg_hazard)}</strong>
              </div>
              <div className="detail-card">
                <span>Median delay</span>
                <strong>{formatDelay(selectedCorridorRegime?.metrics?.median_delay_seconds ?? selectedCorridor?.median_delay_seconds)}</strong>
              </div>
              <div className="detail-card">
                <span>Recent incidents</span>
                <strong>{corridorHistory.incidents.length}</strong>
              </div>
              <div className="detail-card">
                <span>Visible vehicles</span>
                <strong>{vehiclesOnSelectedCorridor.length || selectedCorridor?.vehicle_count || 0}</strong>
              </div>
              <div className="detail-card">
                <span>Activity</span>
                <strong>{humanizeToken(selectedCorridor?.activity_status)}</strong>
              </div>
            </div>
            <div className="corridor-vehicle-strip">
              {vehiclesOnSelectedCorridor.map((vehicle) => (
                <button
                  key={vehicle.entity_id}
                  type="button"
                  className={vehicle.entity_id === selectedEntityId ? "chip chip--small corridor-vehicle-chip is-active" : "chip chip--small corridor-vehicle-chip"}
                  onClick={() => selectCorridor(vehicle.corridor_entity_id ?? "", vehicle.entity_id)}
                  disabled={!vehicle.corridor_entity_id}
                >
                  {vehicle.label}
                </button>
              ))}
              {!vehiclesOnSelectedCorridor.length ? (
                <div className="empty-state">No visible vehicles on this corridor in the selected scope.</div>
              ) : null}
            </div>
            <div className="signature-list">
              {corridorIncidents.map((incident) => (
                <article key={incident.incident_id} className="signature-card">
                  <div className="signature-card__header">
                    <strong>{incident.label}</strong>
                    <span>{relativeTimeFromMs(incident.timestamp_ms)}</span>
                  </div>
                  <p>{incident.summary}</p>
                  <div className="signature-card__meta">
                    <span>{humanizeToken(incident.regime)}</span>
                    <span>{humanizeToken(incident.action)}</span>
                    <span>{formatPercent((incident.confidence ?? 0) * 100)} confidence</span>
                  </div>
                </article>
              ))}
              {!corridorIncidents.length ? <div className="empty-state">No persisted incident memory yet for this corridor.</div> : null}
            </div>
          </article>

          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Incident feed</h2>
                <p className="section__hint">
                  Current operator actions across the network.
                  {selectedCorridor ? ` Selected corridor: ${selectedCorridor.label ?? selectedCorridor.entity_id}.` : ""}
                </p>
              </div>
            </div>
            <div className="incident-feed">
              {incidentResponse.incidents.map((incident) => (
                <article key={incident.incident_id} className="incident-card">
                  <div className="incident-card__header">
                    <strong>{incident.label}</strong>
                    <span className={`badge badge--${actionTone(incident.action)}`}>{humanizeToken(incident.action)}</span>
                  </div>
                  <p>{incident.summary}</p>
                  <div className="incident-card__footer">{incident.recommended_action}</div>
                  <div className="incident-card__meta">
                    <span>{humanizeToken(incident.regime)}</span>
                    <span>{formatPercent((incident.confidence ?? 0) * 100)} confidence</span>
                    <span>{topFactorSummary(incident.provenance)}</span>
                  </div>
                </article>
              ))}
              {!incidentResponse.incidents.length ? <div className="empty-state">No active incidents.</div> : null}
            </div>
          </article>
        </section>

        <section className="section panel">
          <div className="section__header">
            <div>
              <h2 className="section__title">Vehicle inventory</h2>
              <p className="section__hint">Current vehicle observations and assigned corridor state.</p>
            </div>
          </div>
          <div className="vehicle-grid">
            {entities.vehicles.map((vehicle) => {
              const active = vehicle.entity_id === selectedEntityId;
              return (
                <button
                  key={vehicle.entity_id}
                  type="button"
                  className={active ? "vehicle-card is-active" : "vehicle-card"}
                  onClick={() => {
                    if (vehicle.corridor_entity_id) {
                      selectCorridor(vehicle.corridor_entity_id, vehicle.entity_id);
                      return;
                    }
                    setSelectedEntityId(vehicle.entity_id);
                  }}
                >
                  <div className="vehicle-card__header">
                    <div>
                      <strong>{vehicle.label}</strong>
                      <span>{vehicle.route_label ?? "Unknown route"}</span>
                    </div>
                    <span className={`badge badge--${actionTone(vehicle.regime?.action)}`}>
                      {humanizeToken(vehicle.regime?.action ?? "monitor")}
                    </span>
                  </div>
                  <div className="vehicle-card__metrics">
                    <div>
                      <span>Delay</span>
                      <strong>{formatDelay(vehicle.delay_seconds)}</strong>
                    </div>
                    <div>
                      <span>Status</span>
                      <strong>{humanizeToken(vehicle.status)}</strong>
                    </div>
                    <div>
                      <span>Occupancy</span>
                      <strong>{humanizeToken(vehicle.occupancy_status)}</strong>
                    </div>
                    <div>
                      <span>Hazard</span>
                      <strong>{formatHazard(vehicle.regime?.hazard)}</strong>
                    </div>
                  </div>
                  <div className="vehicle-card__footer">
                    <span>{humanizeToken(vehicle.regime?.regime ?? "healthy")}</span>
                    <span>{vehicle.collection_source ?? vehicle.source ?? "unknown source"}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="section split-grid">
          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Selected vehicle details</h2>
                <p className="section__hint">Latest feed evidence for the selected vehicle.</p>
              </div>
            </div>
            <div className="detail-grid detail-grid--expanded">
              <div className="detail-card">
                <span>Route</span>
                <strong>{selectedVehicle?.route_label ?? "n/a"}</strong>
              </div>
              <div className="detail-card">
                <span>Trip</span>
                <strong>{selectedVehicle?.trip_id ?? "n/a"}</strong>
              </div>
              <div className="detail-card">
                <span>Next stop</span>
                <strong>{selectedVehicle?.stop_id ?? "n/a"}</strong>
              </div>
              <div className="detail-card">
                <span>Feed age</span>
                <strong>{formatDelay(selectedRegime?.metrics?.feed_age_seconds)}</strong>
              </div>
              <div className="detail-card">
                <span>Signal confidence</span>
                <strong>{formatPercent((selectedRegime?.confidence ?? 0) * 100)}</strong>
              </div>
              <div className="detail-card">
                <span>Top factors</span>
                <strong>{topFactorSummary(selectedRegime?.provenance)}</strong>
              </div>
            </div>
            <div className="micro-strip">
              {observationPoints.map((point, index) => (
                <span
                  key={`${point.timestamp_ms ?? index}`}
                  className="micro-strip__cell"
                  style={{ height: `${16 + Math.min(84, Math.abs(point.delay_seconds ?? 0) / 6)}%` }}
                />
              ))}
            </div>
          </article>
          <article className="panel">
            <div className="section__header">
              <div>
                <h2 className="section__title">Recurring signatures</h2>
                <p className="section__hint">Repeated corridor fingerprints in the current snapshot.</p>
              </div>
            </div>
            <div className="signature-list">
              {regimeResponse.recurring_regimes.map((signature) => (
                <article key={signature.signature} className="signature-card">
                  <div className="signature-card__header">
                    <strong>{signature.signature}</strong>
                    <span>{signature.entity_count} corridors</span>
                  </div>
                  <p>
                    {signature.regimes.map(humanizeToken).join(", ")} • {signature.actions.map(humanizeToken).join(", ")}
                  </p>
                </article>
              ))}
              {!regimeResponse.recurring_regimes.length ? <div className="empty-state">No recurring signatures yet.</div> : null}
            </div>
          </article>
        </section>
      </div>
    </main>
  );
}
