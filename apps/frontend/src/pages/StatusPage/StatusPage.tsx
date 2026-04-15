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
import { useEffect, useMemo, useState } from "react";
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

const SEVERITY_ORDER = ["severe", "disruption", "delay", "advisory", "good", "unknown"] as const;
type SeverityFilter = "all" | RouteStatus["severity"];

type StatusDataState = {
  network: PublicStatusNetworkResponse | null;
  routes: PublicStatusRoutesResponse | null;
  alerts: PublicStatusAlertsResponse | null;
  scorecard: PublicStatusScorecardResponse | null;
};

type RouteGroup = {
  label: string;
  routes: RouteStatus[];
};

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

const routeKey = (route: RouteStatus): string => route.entity_id || route.route_id || route.label;

const routeHref = (route: RouteStatus): string => `#status/route/${encodeURIComponent(routeKey(route))}`;

const routeMatchesHash = (route: RouteStatus, hashId: string | null): boolean => {
  if (!hashId) return false;
  return [route.entity_id, route.route_id, slugify(route.label)]
    .filter(Boolean)
    .some((value) => String(value) === hashId || slugify(String(value)) === hashId);
};

const getSelectedRouteId = (): string | null => {
  if (typeof window === "undefined") return null;
  const match = window.location.hash.match(/^#status\/route\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
};

const inferRouteGroup = (route: RouteStatus): string => {
  const token = `${route.route_id ?? ""} ${route.label ?? ""}`.toLowerCase();
  if (/\b(commuter rail|cr-|fairmount|framingham|worcester|lowell|fitchburg)\b/.test(token)) return "Commuter Rail";
  if (/\b(ferry|boat)\b/.test(token)) return "Ferry";
  if (/\b(red|orange|blue|green|mattapan|line)\b/.test(token)) return "Rapid Transit";
  if (/^\s*\d/.test(route.route_id ?? route.label)) return "Bus";
  return route.agency_key ? route.agency_key.toUpperCase() : "Other Routes";
};

const groupRoutes = (routes: RouteStatus[]): RouteGroup[] => {
  const groups = new Map<string, RouteStatus[]>();
  routes.forEach((route) => {
    const label = inferRouteGroup(route);
    groups.set(label, [...(groups.get(label) ?? []), route]);
  });
  return [...groups.entries()].map(([label, groupedRoutes]) => ({ label, routes: groupedRoutes }));
};

// ---------------------------------------------------------------------------
// Polling hook
// ---------------------------------------------------------------------------

function useStatusData() {
  const [data, setData] = useState<StatusDataState>({
    network: null,
    routes: null,
    alerts: null,
    scorecard: null,
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    let controller: AbortController | null = null;

    const schedule = (delayMs: number) => {
      timer = window.setTimeout(load, delayMs);
    };

    const load = async () => {
      if (document.visibilityState === "hidden") {
        schedule(15_000);
        return;
      }
      controller?.abort();
      controller = new AbortController();
      setRefreshing(true);
      try {
        const [networkPayload, routesPayload, alertsPayload, scorecardPayload] =
          await Promise.all([
            fetchJson<PublicStatusNetworkResponse>("/api/status/network", { signal: controller.signal }),
            fetchJson<PublicStatusRoutesResponse>("/api/status/routes", { signal: controller.signal }),
            fetchJson<PublicStatusAlertsResponse>("/api/status/alerts", { signal: controller.signal }),
            fetchJson<PublicStatusScorecardResponse>("/api/status/scorecard?limit=288", { signal: controller.signal }),
          ]);
        if (!active) return;
        setData({
          network: networkPayload,
          routes: routesPayload,
          alerts: alertsPayload,
          scorecard: scorecardPayload,
        });
        setLastUpdatedAt(networkPayload.generated_at ?? routesPayload.generated_at ?? new Date().toISOString());
        setLoading(false);
        setRefreshing(false);
        setError(null);
        schedule(15_000);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!active) return;
        setError(err instanceof Error ? err.message : "status unavailable");
        setLoading(false);
        setRefreshing(false);
        schedule(30_000);
      }
    };
    load();
    return () => {
      active = false;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  return { ...data, loading, refreshing, error, lastUpdatedAt };
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
          <strong>{network.feed_status?.collection_source ?? "n/a"}</strong>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Route tile
// ---------------------------------------------------------------------------

function RouteTile({ route, selected }: { route: RouteStatus; selected?: boolean }) {
  const sev = route.severity || "unknown";
  return (
    <a
      className={`route-tile route-tile--${sev}${selected ? " route-tile--selected" : ""}`}
      href={routeHref(route)}
    >
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
    </a>
  );
}

function RouteDrilldown({ route }: { route: RouteStatus }) {
  const sev = route.severity || "unknown";
  return (
    <div className={`route-detail route-detail--${sev}`}>
      <div>
        <div className="route-detail__eyebrow">Selected route</div>
        <h3 className="route-detail__title">{route.label}</h3>
      </div>
      <div className="route-detail__grid">
        <div>
          <span>Route</span>
          <strong>{route.route_id ?? "n/a"}</strong>
        </div>
        <div>
          <span>Status</span>
          <strong>{route.severity_label}</strong>
        </div>
        <div>
          <span>Alerts</span>
          <strong>{route.active_alert_count}</strong>
        </div>
        <div>
          <span>Delay</span>
          <strong>{formatDelay(route.median_delay_seconds)}</strong>
        </div>
      </div>
      <p>{route.body || route.headline}</p>
      <a className="route-detail__clear" href="#status">
        Clear selection
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Percent bar helper
// ---------------------------------------------------------------------------

function PercentBar({ pct }: { pct?: number | null }) {
  const hasValue = typeof pct === "number" && Number.isFinite(pct);
  const value = hasValue ? pct : 0;
  const tier = value >= 80 ? "good" : value >= 60 ? "ok" : "poor";
  return (
    <span className="on-time-bar">
      <span className="on-time-bar__track">
        <span
          className={`on-time-bar__fill on-time-bar__fill--${tier}`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </span>
      <span>{hasValue ? formatPercent(value, 0) : "n/a"}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function StatusPage() {
  const { network, routes, alerts, scorecard, loading, refreshing, error, lastUpdatedAt } = useStatusData();
  const [searchTerm, setSearchTerm] = useState("");
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(getSelectedRouteId);

  const routeList = routes?.routes ?? [];
  const alertList = alerts?.alerts ?? [];
  const corridorList = scorecard?.corridors ?? [];
  const now = lastUpdatedAt ?? network?.generated_at ?? routes?.generated_at;

  useEffect(() => {
    const handler = () => setSelectedRouteId(getSelectedRouteId());
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);

  const selectedRoute = useMemo(
    () => routeList.find((route) => routeMatchesHash(route, selectedRouteId)) ?? null,
    [routeList, selectedRouteId],
  );

  const filteredRoutes = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return routeList.filter((route) => {
      const severityMatch = severityFilter === "all" || route.severity === severityFilter;
      if (!severityMatch) return false;
      if (!query) return true;
      return [route.label, route.route_id, route.headline, route.short_summary, route.agency_key]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    });
  }, [routeList, searchTerm, severityFilter]);

  const routeGroups = useMemo(() => groupRoutes(filteredRoutes), [filteredRoutes]);
  const severityCounts = useMemo(() => {
    const counts = new Map<SeverityFilter, number>([["all", routeList.length]]);
    routeList.forEach((route) => counts.set(route.severity, (counts.get(route.severity) ?? 0) + 1));
    return counts;
  }, [routeList]);

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
                <div className="network-banner__title">Loading service status...</div>
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
              <span className="status-section__count">
                {filteredRoutes.length} of {routeList.length} routes
              </span>
            </div>
            <div className="status-controls">
              <label className="status-search">
                <span>Find a route</span>
                <input
                  type="search"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  placeholder="Red, Green-B, 15"
                />
              </label>
              <div className="severity-filters" aria-label="Severity filters">
                {(["all", ...SEVERITY_ORDER] as SeverityFilter[]).map((severity) => (
                  <button
                    key={severity}
                    className={
                      severityFilter === severity
                        ? "severity-filter severity-filter--active"
                        : "severity-filter"
                    }
                    type="button"
                    onClick={() => setSeverityFilter(severity)}
                  >
                    {severity === "all" ? "All" : severity.replace(/^\w/, (letter) => letter.toUpperCase())}
                    <span>{severityCounts.get(severity) ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
            {selectedRoute && <RouteDrilldown route={selectedRoute} />}
            {routeGroups.length > 0 ? (
              <div className="route-groups">
                {routeGroups.map((group) => (
                  <section className="route-group" key={group.label}>
                    <div className="route-group__header">
                      <h3>{group.label}</h3>
                      <span>{group.routes.length} routes</span>
                    </div>
                    <div className="route-tiles">
                      {group.routes.map((route) => (
                        <RouteTile
                          key={route.entity_id}
                          route={route}
                          selected={selectedRoute ? routeMatchesHash(route, routeKey(selectedRoute)) : false}
                        />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ) : (
              <div className="status-empty">No routes match the current filters.</div>
            )}
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
                  <th>Stable</th>
                  <th>Avg delay</th>
                  <th>Incidents</th>
                </tr>
              </thead>
              <tbody>
                {corridorList.map((corridor) => (
                  <tr key={corridor.entity_id}>
                    <td className="scorecard-table__label">{corridor.label}</td>
                    <td>
                      <PercentBar pct={corridor.healthy_pct} />
                    </td>
                    <td>{formatDelay(corridor.avg_delay_seconds)}</td>
                    <td>{corridor.incident_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {scorecard?.network && (
              <div style={{ marginTop: 10, fontSize: "0.82rem", color: "var(--text-subtle)", textAlign: "right" }}>
                Network: {formatPercent(scorecard.network.healthy_pct, 1)} stable •{" "}
                {formatPercent(scorecard.network.unstable_pct, 1)} at risk •{" "}
                avg delay {formatDelay(scorecard.network.avg_delay_seconds)}
              </div>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="status-footer">
          <span>Transit Sentinel - Public Service Status</span>
          {now && <span>{refreshing ? "Refreshing" : "Updated"} {relativeTime(now)}</span>}
        </div>
      </div>
    </main>
  );
}
