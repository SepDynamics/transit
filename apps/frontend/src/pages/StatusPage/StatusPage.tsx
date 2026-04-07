/**
 * Public Service Status Page
 *
 * Rider-facing view that shows:
 *   - Network-level severity banner
 *   - Per-route status tiles with plain-language severity
 *   - Active alerts feed
 *   - Reliability scorecard table
 *
 * Consumes /api/status/* endpoints only.
 * No internal scoring vocabulary is surfaced to the user.
 */
import { useEffect, useState } from "react";
import type {
  PublicStatusAlertsResponse,
  PublicStatusNetworkResponse,
  PublicStatusRoutesResponse,
  PublicStatusScorecardResponse,
  RouteStatus,
} from "../../types/transit";
import { fetchJson } from "../../utils/api";
import { formatDelay, formatPercent, relativeTime, relativeTimeFromMs } from "../../utils/formatters";
import "./StatusPage.css";

// ---------------------------------------------------------------------------
// Polling hook
// ---------------------------------------------------------------------------

function useStatusData() {
  const [network, setNetwork] = useState<PublicStatusNetworkResponse | null>(null);
  const [routes, setRoutes] = useState<PublicStatusRoutesResponse | null>(null);
  const [alerts, setAlerts] = useState<PublicStatusAlertsResponse | null>(null);
  const [scorecard, setScorecard] = useState<PublicStatusScorecardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [networkPayload, routesPayload, alertsPayload, scorecardPayload] =
          await Promise.all([
            fetchJson<PublicStatusNetworkResponse>("/api/status/network"),
            fetchJson<PublicStatusRoutesResponse>("/api/status/routes"),
            fetchJson<PublicStatusAlertsResponse>("/api/status/alerts"),
            fetchJson<PublicStatusScorecardResponse>("/api/status/scorecard?limit=288"),
          ]);
        if (!active) return;
        setNetwork(networkPayload);
        setRoutes(routesPayload);
        setAlerts(alertsPayload);
        setScorecard(scorecardPayload);
        setLoading(false);
        setError(null);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "status unavailable");
        setLoading(false);
      }
    };
    load();
    const timer = window.setInterval(load, 15_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { network, routes, alerts, scorecard, loading, error };
}

// ---------------------------------------------------------------------------
// Network banner
// ---------------------------------------------------------------------------

function NetworkBanner({ network }: { network: PublicStatusNetworkResponse }) {
  const sev = network.severity || "unknown";
  return (
    <div className={`network-banner network-banner--${sev}`}>
      <div className="network-banner__status">
        <div className={`network-banner__dot severity-dot--${sev}`} />
        <div className="network-banner__label">
          <div className="network-banner__title">{network.severity_label}</div>
          <div className="network-banner__subtitle">
            {network.disrupted_route_count > 0
              ? `${network.disrupted_route_count} route${network.disrupted_route_count !== 1 ? "s" : ""} with disruptions`
              : "All routes operating normally"}
          </div>
        </div>
      </div>
      <div className="network-banner__stats">
        <div className="network-banner__stat">
          <span>Active routes</span>
          <strong>{network.active_route_count}</strong>
        </div>
        <div className="network-banner__stat">
          <span>Active alerts</span>
          <strong>{network.incident_count}</strong>
        </div>
        <div className="network-banner__stat">
          <span>Feed</span>
          <strong>{network.feed_status?.collection_source ?? "—"}</strong>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Route tile
// ---------------------------------------------------------------------------

function RouteTile({ route }: { route: RouteStatus }) {
  const sev = route.severity || "unknown";
  return (
    <div className={`route-tile route-tile--${sev}`}>
      <div className="route-tile__header">
        <div className="route-tile__name">{route.label}</div>
        <span className={`route-tile__severity-badge severity-badge--${sev}`}>
          <span>{route.short_summary}</span>
        </span>
      </div>
      <div className="route-tile__body">{route.headline}</div>
      {route.advisories.length > 0 && (
        <div className="route-tile__advisories">
          {route.advisories.map((advisory, idx) => (
            <div key={idx} className="route-tile__advisory">
              {advisory}
            </div>
          ))}
        </div>
      )}
      <div className="route-tile__meta">
        {route.median_delay_seconds != null && (
          <span>Median delay: {formatDelay(route.median_delay_seconds)}</span>
        )}
        {route.active_alert_count > 0 && (
          <span>{route.active_alert_count} alert{route.active_alert_count !== 1 ? "s" : ""}</span>
        )}
        {route.timestamp_ms != null && (
          <span>Updated {relativeTimeFromMs(route.timestamp_ms)}</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// On-time bar helper
// ---------------------------------------------------------------------------

function OnTimeBar({ pct }: { pct?: number | null }) {
  const value = typeof pct === "number" && Number.isFinite(pct) ? pct : 0;
  const tier = value >= 80 ? "good" : value >= 60 ? "ok" : "poor";
  return (
    <span className="on-time-bar">
      <span className="on-time-bar__track">
        <span
          className={`on-time-bar__fill on-time-bar__fill--${tier}`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </span>
      <span>{formatPercent(value, 0)}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function StatusPage() {
  const { network, routes, alerts, scorecard, loading, error } = useStatusData();

  const routeList = routes?.routes ?? [];
  const alertList = alerts?.alerts ?? [];
  const corridorList = scorecard?.corridors ?? [];
  const now = network?.generated_at ?? routes?.generated_at;

  return (
    <main className="status-page">
      <div className="status-page__shell">
        {/* Network banner */}
        {network ? (
          <NetworkBanner network={network} />
        ) : loading ? (
          <div className="network-banner network-banner--unknown">
            <div className="network-banner__status">
              <div className="network-banner__dot severity-dot--unknown" />
              <div className="network-banner__label">
                <div className="network-banner__title">Loading service status…</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="network-banner network-banner--unknown">
            <div className="network-banner__status">
              <div className="network-banner__dot severity-dot--unknown" />
              <div className="network-banner__label">
                <div className="network-banner__title">Status unavailable</div>
                {error && (
                  <div className="network-banner__subtitle">
                    <code>{error}</code>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Route tiles */}
        {routeList.length > 0 && (
          <div className="status-section">
            <div className="status-section__header">
              <h2 className="status-section__title">Route status</h2>
              <span className="status-section__count">{routeList.length} routes</span>
            </div>
            <div className="route-tiles">
              {routeList.map((route) => (
                <RouteTile key={route.entity_id} route={route} />
              ))}
            </div>
          </div>
        )}

        {/* Active alerts */}
        {alertList.length > 0 && (
          <div className="status-section">
            <div className="status-section__header">
              <h2 className="status-section__title">Active alerts</h2>
              <span className="status-section__count">{alertList.length} alert{alertList.length !== 1 ? "s" : ""}</span>
            </div>
            <div className="alert-list">
              {alertList.map((alert, idx) => {
                const sev = alert.severity || "unknown";
                return (
                  <div key={alert.alert_id ?? idx} className={`alert-card alert-card--${sev}`}>
                    <div className="alert-card__header">
                      <span className="alert-card__route">{alert.route_label}</span>
                      <span className={`route-tile__severity-badge severity-badge--${sev}`}>
                        {alert.severity_label}
                      </span>
                    </div>
                    <div className="alert-card__headline">{alert.headline}</div>
                    {alert.recommended_action && (
                      <div className="alert-card__action">{alert.recommended_action}</div>
                    )}
                    {alert.timestamp_ms != null && (
                      <div className="alert-card__route">
                        {relativeTimeFromMs(alert.timestamp_ms)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Reliability scorecard */}
        {corridorList.length > 0 && (
          <div className="status-section">
            <div className="status-section__header">
              <h2 className="status-section__title">Reliability scorecard</h2>
              {scorecard?.window_snapshots != null && (
                <span className="status-section__count">
                  {scorecard.window_snapshots} snapshots
                </span>
              )}
            </div>
            <table className="scorecard-table">
              <thead>
                <tr>
                  <th>Route</th>
                  <th>On-time</th>
                  <th>Avg delay</th>
                  <th>Incidents</th>
                </tr>
              </thead>
              <tbody>
                {corridorList.map((corridor) => (
                  <tr key={corridor.entity_id}>
                    <td className="scorecard-table__label">{corridor.label}</td>
                    <td>
                      <OnTimeBar pct={corridor.on_time_pct} />
                    </td>
                    <td>{formatDelay(corridor.avg_delay_seconds)}</td>
                    <td>{corridor.incident_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {scorecard?.network && (
              <div style={{ marginTop: 10, fontSize: "0.82rem", color: "var(--text-subtle)", textAlign: "right" }}>
                Network: {formatPercent(scorecard.network.on_time_pct, 1)} on-time •{" "}
                avg delay {formatDelay(scorecard.network.avg_delay_seconds)}
              </div>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="status-footer">
          <span>Transit Sentinel — Public Service Status</span>
          {now && <span>Updated {relativeTime(now)}</span>}
        </div>
      </div>
    </main>
  );
}
