/**
 * Public Service Status Page
 *
 * Rider-facing view that shows:
 *   - Network-level severity banner + stats bar
 *   - Live map of vehicles + corridors
 *   - Triage queue (ranked routes needing attention)
 *   - Feed-quality checks with freshness indicators
 *   - Active alerts list
 *   - Per-route status tiles (grouped by mode)
 *   - Public reliability scorecard
 */
import { lazy, Suspense, useEffect, useState, useCallback, useRef } from "react";
import type {
  PublicStatusNetworkResponse,
  PublicStatusAlertsResponse,
  PublicStatusTriageResponse,
  PublicStatusFeedQualityResponse,
  PublicStatusRoutesResponse,
  PublicStatusScorecardResponse,
  TransitMapResponse,
} from "../../types/transit";
import { fetchCachedJson } from "../../utils/api";
import "./StatusPage.css";

// Lazy-load the map component (MapLibre is ~500KB)
const TransitMap = lazy(() => import("../../components/TransitMap"));

// ---------------------------------------------------------------------------
// Polling intervals
// ---------------------------------------------------------------------------
const NETWORK_POLL_MS = 10_000;
const TRIAGE_POLL_MS = 10_000;
const ALERTS_POLL_MS = 15_000;
const FEED_POLL_MS = 15_000;
const ROUTES_POLL_MS = 15_000;
const SCORECARD_POLL_MS = 30_000;
const MAP_POLL_MS = 30_000;

// ---------------------------------------------------------------------------
// Severity display helpers
// ---------------------------------------------------------------------------
function severityColor(sev: string): string {
  const m: Record<string, string> = {
    good: "green", advisory: "yellow", delay: "orange",
    disruption: "red", severe: "red", unknown: "gray",
  };
  return m[sev] ?? "gray";
}

function severityBg(sev: string): string {
  const m: Record<string, string> = {
    good: "#166534", advisory: "#854d0e", delay: "#9a3412",
    disruption: "#991b1b", severe: "#7f1d1d", unknown: "#4b5563",
  };
  return m[sev] ?? "#4b5563";
}

function ageString(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "unknown";
  if (seconds < 60) return `${seconds}s ago`;
  const m = Math.floor(seconds / 60);
  return `${m}m ago`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function StatusPage() {
  const [network, setNetwork] = useState<PublicStatusNetworkResponse | null>(null);
  const [triage, setTriage] = useState<PublicStatusTriageResponse | null>(null);
  const [alerts, setAlerts] = useState<PublicStatusAlertsResponse | null>(null);
  const [feedQuality, setFeedQuality] = useState<PublicStatusFeedQualityResponse | null>(null);
  const [routesPayload, setRoutesPayload] = useState<PublicStatusRoutesResponse | null>(null);
  const [scorecard, setScorecard] = useState<PublicStatusScorecardResponse | null>(null);
  const [mapData, setMapData] = useState<TransitMapResponse | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [showMap, setShowMap] = useState(false);
  const [feedAgeNow, setFeedAgeNow] = useState<number | null>(null);
  const feedAgeRef = useRef<number | null>(null);

  // Poll network status (used for the top banner)
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusNetworkResponse>("/api/status/network"); if (active) setNetwork(d); }
      catch { /* ignore polling errors */ }
    };
    load(); const t = window.setInterval(load, NETWORK_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Poll triage
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusTriageResponse>("/api/status/triage?limit=8"); if (active) setTriage(d); }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, TRIAGE_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Poll alerts
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusAlertsResponse>("/api/status/alerts"); if (active) setAlerts(d); }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, ALERTS_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Poll feed quality
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusFeedQualityResponse>("/api/status/feed-quality"); if (active) { setFeedQuality(d); feedAgeRef.current = d.age_seconds ?? null; } }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, FEED_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Live feed age counter
  useEffect(() => {
    const tick = () => {
      if (feedAgeRef.current !== null) setFeedAgeNow(feedAgeRef.current + 1);
    };
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, []);

  // Poll routes
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusRoutesResponse>("/api/status/routes"); if (active) setRoutesPayload(d); }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, ROUTES_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Poll scorecard
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<PublicStatusScorecardResponse>("/api/status/scorecard?limit=60"); if (active) setScorecard(d); }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, SCORECARD_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Poll map data
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const d = await fetchCachedJson<TransitMapResponse>("/api/status/map"); if (active) setMapData(d); }
      catch { /* */ }
    };
    load(); const t = window.setInterval(load, MAP_POLL_MS);
    return () => { active = false; window.clearInterval(t); };
  }, []);

  // Filter routes by search
  const routes = routesPayload?.routes ?? [];
  const filteredRoutes = searchTerm
    ? routes.filter(r => r.label.toLowerCase().includes(searchTerm.toLowerCase()))
    : routes;

  // Group by mode
  const groupByMode = (routes: typeof filteredRoutes) => {
    const groups: Record<string, typeof filteredRoutes> = {};
    for (const r of routes) {
      const mode = guessMode(r.label) || (r.route_id ? (isNaN(Number(r.route_id)) ? "Rail" : "Bus") : "Other");
      if (!groups[mode]) groups[mode] = [];
      groups[mode].push(r);
    }
    return groups;
  };
  const grouped = groupByMode(filteredRoutes);

  // Scorecard network stats
  const netStats = scorecard?.network;

  return (
    <main className="status-page">
      {/* ================================================================ */}
      {/* NETWORK BANNER */}
      {/* ================================================================ */}
      {network && (
        <header className="sp-banner" style={{ background: severityBg(network.severity) }}>
          <div className="sp-banner-inner">
            <div className="sp-banner-left">
              <span className="sp-banner-severity">{network.severity_label}</span>
              <span className="sp-banner-subtitle">LA Metro Network - Live Status</span>
            </div>
            <div className="sp-banner-stats">
              <div className="sp-stat">
                <span className="sp-stat-value">{network.active_route_count}</span>
                <span className="sp-stat-label">Routes</span>
              </div>
              <div className="sp-stat">
                <span className="sp-stat-value">{network.disrupted_route_count}</span>
                <span className="sp-stat-label">Disrupted</span>
              </div>
              <div className="sp-stat">
                <span className="sp-stat-value">{network.incident_count}</span>
                <span className="sp-stat-label">Incidents</span>
              </div>
              <div className="sp-stat">
                <span className="sp-stat-value">{feedAgeNow !== null ? ageString(feedAgeNow) : "..."}</span>
                <span className="sp-stat-label">Last update</span>
              </div>
            </div>
          </div>
        </header>
      )}

      {/* ================================================================ */}
      {/* MAP SECTION */}
      {/* ================================================================ */}
      <section className="sp-section sp-map-section">
        <div className="sp-section-header">
          <h2>Live Vehicle Positions</h2>
          <button className="sp-toggle-map" onClick={() => setShowMap(v => !v)}>
            {showMap ? "Hide map" : "Show map"}
          </button>
        </div>
        {showMap && mapData && (
          <div className="sp-map-container">
            <Suspense fallback={<div className="sp-map-loading">Loading map...</div>}>
              <TransitMap
                mapData={mapData}
                defaultCenter={[-118.2437, 34.0522]}
                defaultZoom={11}
                className="sp-map"
                style={{ width: "100%", height: "100%" }}
              />
            </Suspense>
          </div>
        )}
        {showMap && !mapData && (
          <div className="sp-map-placeholder">Map data loading...</div>
        )}
        <div className="sp-map-meta">
          {mapData && (
            <span>{mapData.vehicle_count} vehicles &middot; {mapData.corridor_count} routes &middot; Color-coded by service health</span>
          )}
        </div>
      </section>

      {/* ================================================================ */}
      {/* LIV E TRIAGE — what needs attention */}
      {/* ================================================================ */}
      <section className="sp-section sp-triage-section">
        <h2 className="sp-section-title">What Needs Attention</h2>
        {triage && triage.routes.length > 0 ? (
          <div className="sp-triage-list">
            {triage.routes.map(r => (
              <div key={r.rank} className={`sp-triage-row sp-triage-${r.severity}`}>
                <span className="sp-triage-rank">#{r.rank}</span>
                <div className="sp-triage-body">
                  <span className="sp-triage-label">{r.label}</span>
                  <span className="sp-triage-headline">{r.headline}</span>
                  <div className="sp-triage-evidence">
                    {r.evidence.slice(0, 2).map((e, i) => <span key={i} className="sp-evidence-chip">{e}</span>)}
                    {r.active_alert_count > 0 && <span className="sp-evidence-chip">{r.active_alert_count} alert{r.active_alert_count !== 1 ? "s" : ""}</span>}
                  </div>
                </div>
                <span className="sp-triage-action">{r.recommended_action}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="sp-empty">No routes currently need attention.</p>
        )}
      </section>

      {/* ================================================================ */}
      {/* SCORECARD OVERVIEW + FEED QUALITY in a 2-col layout */}
      {/* ================================================================ */}
      <div className="sp-two-col">
        {/* Scorecard summary */}
        <section className="sp-section">
          <h2 className="sp-section-title">Service Reliability</h2>
          {netStats ? (
            <div className="sp-scorecard-grid">
              <div className="sp-scorecard-stat">
                <span className="sp-scorecard-value">{netStats.on_time_pct ?? "—"}%</span>
                <span className="sp-scorecard-label">On-time</span>
              </div>
              <div className="sp-scorecard-stat">
                <span className="sp-scorecard-value">{netStats.healthy_pct ?? "—"}%</span>
                <span className="sp-scorecard-label">Stable routes</span>
              </div>
              <div className="sp-scorecard-stat sp-scorecard-warn">
                <span className="sp-scorecard-value">{netStats.unstable_pct ?? "—"}%</span>
                <span className="sp-scorecard-label">Unstable</span>
              </div>
              <div className="sp-scorecard-stat">
                <span className="sp-scorecard-value">{netStats.avg_delay_seconds ?? "—"}s</span>
                <span className="sp-scorecard-label">Avg delay</span>
              </div>
            </div>
          ) : (
            <p className="sp-empty">Loading reliability data...</p>
          )}
          {scorecard && scorecard.corridors.length > 0 && (
            <details className="sp-details">
              <summary>Worst-performing routes</summary>
              <div className="sp-scorecard-rows">
                {scorecard.corridors
                  .filter(c => (c.unstable_pct ?? 0) >= 15)
                  .sort((a, b) => (b.unstable_pct ?? 0) - (a.unstable_pct ?? 0))
                  .slice(0, 8)
                  .map(c => (
                    <div key={c.entity_id ?? c.label} className="sp-scorecard-row">
                      <span className="sp-sc-label">{c.label}</span>
                      <span className="sp-sc-value">{c.unstable_pct ?? 0}% unstable</span>
                      <span className="sp-sc-sub">{c.avg_delay_seconds ?? 0}s avg delay</span>
                    </div>
                  ))}
              </div>
            </details>
          )}
        </section>

        {/* Feed quality */}
        <section className="sp-section">
          <h2 className="sp-section-title">Source & Feed Quality</h2>
          {feedQuality ? (
            <div className="sp-feed-checks">
              {feedQuality.checks.map(c => (
                <div key={c.check_id} className={`sp-feed-check sp-feed-${c.status}`}>
                  <span className="sp-feed-dot" />
                  <span className="sp-feed-label">{c.label}</span>
                  <span className="sp-feed-detail">{c.detail}</span>
                </div>
              ))}
              <div className="sp-feed-freshness">
                <span>Last updated: </span>
                <strong>{feedAgeNow !== null ? ageString(feedAgeNow) : "unknown"}</strong>
                {" · "}
                <span>Source: {feedQuality.feed_status?.feed_label ?? "LA Metro feeds"}</span>
              </div>
            </div>
          ) : (
            <p className="sp-empty">Loading feed status...</p>
          )}
        </section>
      </div>

      {/* ================================================================ */}
      {/* ACTIVE ALERTS */}
      {/* ================================================================ */}
      <section className="sp-section">
        <h2 className="sp-section-title">
          Priority Alerts
          {alerts && alerts.alert_count > 0 && <span className="sp-badge">{alerts.alert_count}</span>}
        </h2>
        {alerts && alerts.alerts.length > 0 ? (
          <div className="sp-alerts-list">
            {alerts.alerts.slice(0, 10).map(a => (
              <div key={a.alert_id ?? a.entity_id} className={`sp-alert-row sp-alert-${a.severity}`}>
                <span className="sp-alert-severity" style={{ background: a.severity_color }} />
                <div className="sp-alert-body">
                  <strong className="sp-alert-route">{a.route_label}</strong>
                  <span className="sp-alert-headline">{a.headline}</span>
                </div>
                <span className="sp-alert-action">{a.recommended_action}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="sp-empty">No priority alerts.</p>
        )}
      </section>

      {/* ================================================================ */}
      {/* ROUTE LIST */}
      {/* ================================================================ */}
      <section className="sp-section sp-routes-section">
        <div className="sp-route-header">
          <h2 className="sp-section-title">Route Status</h2>
          <input
            className="sp-search"
            type="text"
            placeholder="Search routes..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="sp-route-count">{routes.length} routes tracked</div>

        {Object.entries(grouped).map(([mode, modeRoutes]) => (
          <details key={mode} className="sp-mode-group" open>
            <summary className="sp-mode-title">{mode} ({modeRoutes.length})</summary>
            <div className="sp-route-cards">
              {modeRoutes.map(r => (
                <div key={r.entity_id} className={`sp-route-card sp-card-${r.severity}`}>
                  <div className="sp-card-top">
                    <span className="sp-card-severity" style={{ background: severityColor(r.severity) }} />
                    <span className="sp-card-label">{r.label}</span>
                  </div>
                  <div className="sp-card-body">{r.headline}</div>
                  <div className="sp-card-meta">
                    {r.median_delay_seconds !== null && r.median_delay_seconds !== undefined && (
                      <span className="sp-card-delay">{r.median_delay_seconds > 0 ? `+${r.median_delay_seconds}s` : "on time"}</span>
                    )}
                    {r.active_alert_count > 0 && <span className="sp-card-alerts">{r.active_alert_count} alert{r.active_alert_count !== 1 ? "s" : ""}</span>}
                  </div>
                </div>
              ))}
            </div>
          </details>
        ))}
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function guessMode(label: string): string {
  const l = label.toLowerCase();
  if (l.includes("red line") || l.includes("orange line") || l.includes("blue line") || l.includes("green line") || l.includes("green-") || l.includes("mattapan")) return "Rapid Transit";
  if (l.includes("commuter rail") || l.includes("fitchburg") || l.includes("lowell") || l.includes("worcester") || l.includes("providence") || l.includes("needham") || l.includes("framingham") || l.includes("fairmount") || l.includes("middleton") || l.includes("newburyport") || l.includes("rockport") || l.includes("haverhill") || l.includes("kingston") || l.includes("greenbush")) return "Commuter Rail";
  if (l.includes("ferry") || l.includes("boat")) return "Ferry";
  if (l.includes("bus") || l.includes("route ") || /^\d+/.test(label)) return "Bus";
  return "Other";
}
