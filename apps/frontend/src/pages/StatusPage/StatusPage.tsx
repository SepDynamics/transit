/**
 * Public Service Status Page
 *
 * Rider-facing view that shows:
 *   - Network-level severity banner
 *   - Feed-quality and live-triage summaries
 *   - Per-route status tiles with plain-language severity
 *   - Priority alerts feed
 *   - Reliability scorecard table
 *
 * Consumes /api/status/* endpoints only.
 * No internal scoring vocabulary is surfaced to the user.
 */
import { useEffect, useMemo, useState } from "react";
import type {
  PublicStatusAlertsResponse,
  PublicStatusFeedQualityResponse,
  PublicStatusNetworkResponse,
  PublicStatusRoutesResponse,
  PublicStatusScorecardResponse,
  PublicStatusTriageResponse,
  PublicTriageRoute,
  RouteStatus,
} from "../../types/transit";
import { fetchCachedJson } from "../../utils/api";
import {
  formatDelay,
  formatDelaySignal,
  formatPercent,
  hasDelaySignal,
  relativeTime,
  relativeTimeFromMs,
} from "../../utils/formatters";
import "./StatusPage.css";

const STATUS_REFRESH_MS = 30_000;
const STATUS_HIDDEN_REFRESH_MS = 60_000;
const STATUS_SCORECARD_LIMIT = 60;
const STATUS_TRIAGE_LIMIT = 8;
const SEVERITY_ORDER = ["severe", "disruption", "delay", "advisory", "good", "unknown"] as const;
const ROUTE_GROUP_ORDER = ["Rapid Transit", "Bus", "Commuter Rail", "Ferry", "Other Routes"] as const;
type SeverityFilter = "all" | RouteStatus["severity"];

type StatusDataState = {
  network: PublicStatusNetworkResponse | null;
  routes: PublicStatusRoutesResponse | null;
  alerts: PublicStatusAlertsResponse | null;
  scorecard: PublicStatusScorecardResponse | null;
  feedQuality: PublicStatusFeedQualityResponse | null;
  triage: PublicStatusTriageResponse | null;
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

const normalizeSearch = (value: string): string =>
  value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

const compactSearch = (value: string): string => normalizeSearch(value).replace(/\s+/g, "");

const severityRank = (severity: RouteStatus["severity"]): number => {
  const rank = SEVERITY_ORDER.indexOf(severity);
  return rank === -1 ? SEVERITY_ORDER.length : rank;
};

const getSelectedRouteId = (): string | null => {
  if (typeof window === "undefined") return null;
  const match = window.location.hash.match(/^#status\/route\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
};

const inferRouteGroup = (route: RouteStatus): string => {
  const routeId = String(route.route_id ?? "");
  const label = String(route.label ?? "");
  const normalized = normalizeSearch(`${routeId} ${label}`);
  const compact = compactSearch(`${routeId} ${label}`);

  if (
    /^cr[-_]?/i.test(routeId) ||
    /\bcommuter rail\b/.test(normalized) ||
    /(fairmount|framingham|worcester|lowell|fitchburg|haverhill|kingston|greenbush|newburyport|rockport|providence|stoughton|needham|middleborough|newbedford)/.test(compact)
  ) {
    return "Commuter Rail";
  }
  if (/\b(ferry|boat)\b/.test(normalized) || /^boat[-_]?/i.test(routeId)) return "Ferry";
  if (
    /^(red|orange|blue|green|mattapan)/i.test(routeId) ||
    /(redline|orangeline|blueline|greenline|greenb|greenc|greend|greene|mattapanline|rapidtransit)/.test(compact)
  ) {
    return "Rapid Transit";
  }
  if (/^\s*\d/.test(routeId || label)) return "Bus";
  return "Other Routes";
};

const groupRoutes = (routes: RouteStatus[]): RouteGroup[] => {
  const groups = new Map<string, RouteStatus[]>();
  routes.forEach((route) => {
    const label = inferRouteGroup(route);
    groups.set(label, [...(groups.get(label) ?? []), route]);
  });
  return [...groups.entries()]
    .map(([label, groupedRoutes]) => ({
      label,
      routes: [...groupedRoutes].sort((left, right) => {
        const severityDelta = severityRank(left.severity) - severityRank(right.severity);
        return severityDelta || left.label.localeCompare(right.label);
      }),
    }))
    .sort((left, right) => {
      const leftRank = ROUTE_GROUP_ORDER.indexOf(left.label as (typeof ROUTE_GROUP_ORDER)[number]);
      const rightRank = ROUTE_GROUP_ORDER.indexOf(right.label as (typeof ROUTE_GROUP_ORDER)[number]);
      const normalizedLeftRank = leftRank === -1 ? ROUTE_GROUP_ORDER.length : leftRank;
      const normalizedRightRank = rightRank === -1 ? ROUTE_GROUP_ORDER.length : rightRank;
      return normalizedLeftRank - normalizedRightRank || left.label.localeCompare(right.label);
    });
};

const routeMatchesSearch = (route: RouteStatus, rawQuery: string): boolean => {
  const query = normalizeSearch(rawQuery);
  if (!query) return true;
  const compactQuery = compactSearch(rawQuery);
  const group = inferRouteGroup(route);
  const searchText = normalizeSearch(
    [
      route.label,
      route.route_id,
      route.entity_id,
      route.headline,
      route.short_summary,
      route.agency_key,
      group,
      route.route_id ? `route ${route.route_id}` : null,
    ]
      .filter(Boolean)
      .join(" "),
  );
  const compactText = searchText.replace(/\s+/g, "");
  if (compactQuery && compactText.includes(compactQuery)) return true;
  return query.split(/\s+/).every((token) => searchText.includes(token) || compactText.includes(token));
};

const FEED_SOURCE_LABELS: Record<string, string> = {
  gtfs_rt_alerts: "alerts",
  gtfs_rt_trip_updates: "trip updates",
  gtfs_rt_vehicle_positions: "vehicle positions",
};

const describeFeedSource = (
  feedStatus: PublicStatusNetworkResponse["feed_status"],
): { label: string; summary: string } => {
  const feedLabel = feedStatus?.feed_label?.trim() || "Transit";
  const sourceParts = (feedStatus?.collection_source ?? "")
    .split("+")
    .map((part) => FEED_SOURCE_LABELS[part] ?? part.replace(/_/g, " "))
    .filter(Boolean);

  return {
    label: `${feedLabel} live feeds`,
    summary: sourceParts.length ? sourceParts.join(" + ") : "awaiting data",
  };
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
    feedQuality: null,
    triage: null,
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
        schedule(STATUS_HIDDEN_REFRESH_MS);
        return;
      }
      controller?.abort();
      controller = new AbortController();
      setRefreshing(true);
      try {
        const [
          networkPayload,
          routesPayload,
          alertsPayload,
          scorecardPayload,
          feedQualityPayload,
          triagePayload,
        ] =
          await Promise.all([
            fetchCachedJson<PublicStatusNetworkResponse>("/api/status/network", { signal: controller.signal }),
            fetchCachedJson<PublicStatusRoutesResponse>("/api/status/routes", { signal: controller.signal }),
            fetchCachedJson<PublicStatusAlertsResponse>("/api/status/alerts", { signal: controller.signal }),
            fetchCachedJson<PublicStatusScorecardResponse>(`/api/status/scorecard?limit=${STATUS_SCORECARD_LIMIT}`, { signal: controller.signal }),
            fetchCachedJson<PublicStatusFeedQualityResponse>("/api/status/feed-quality", { signal: controller.signal }).catch(() => null),
            fetchCachedJson<PublicStatusTriageResponse>(`/api/status/triage?limit=${STATUS_TRIAGE_LIMIT}`, { signal: controller.signal }).catch(() => null),
          ]);
        if (!active) return;
        setData({
          network: networkPayload,
          routes: routesPayload,
          alerts: alertsPayload,
          scorecard: scorecardPayload,
          feedQuality: feedQualityPayload,
          triage: triagePayload,
        });
        setLastUpdatedAt(networkPayload.generated_at ?? routesPayload.generated_at ?? new Date().toISOString());
        setLoading(false);
        setRefreshing(false);
        setError(null);
        schedule(STATUS_REFRESH_MS);
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
  const feedSource = describeFeedSource(network.feed_status);
  const rawAlertCount = network.feed_status?.alert_count;
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
          <span>Priority alerts</span>
          <strong>{network.incident_count}</strong>
          {typeof rawAlertCount === "number" && (
            <small>{rawAlertCount} MBTA alerts read</small>
          )}
        </div>
        <div className="network-banner__stat">
          <span>Source</span>
          <strong>{feedSource.label}</strong>
          <small>{feedSource.summary}</small>
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
        {hasDelaySignal(route.median_delay_seconds) && (
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
          <strong>{formatDelaySignal(route.median_delay_seconds)}</strong>
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

function formatFeedAge(ageSeconds?: number | null): string {
  if (typeof ageSeconds !== "number" || !Number.isFinite(ageSeconds)) return "n/a";
  if (ageSeconds < 60) return `${Math.max(0, Math.round(ageSeconds))}s`;
  return `${Math.round(ageSeconds / 60)}m`;
}

function FeedQualityPanel({ feedQuality }: { feedQuality: PublicStatusFeedQualityResponse }) {
  const status = feedQuality.status || "unknown";
  return (
    <section className={`feed-quality-panel feed-quality-panel--${status}`} aria-labelledby="feed-quality-title">
      <div className="feed-quality-panel__summary">
        <span className="status-section__title" id="feed-quality-title">Feed quality</span>
        <strong>{feedQuality.status_label}</strong>
        <span>Latest sample {formatFeedAge(feedQuality.age_seconds)} old</span>
      </div>
      <div className="feed-quality-checks">
        {feedQuality.checks.map((check) => (
          <div className="feed-quality-check" key={check.check_id}>
            <span className={`feed-quality-check__dot severity-dot--${check.status}`} />
            <div>
              <strong>{check.label}</strong>
              <span>{check.detail}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function TriagePanel({ triage }: { triage: PublicStatusTriageResponse }) {
  if (!triage.routes.length) {
    return (
      <section className="status-section">
        <div className="status-section__header">
          <h2 className="status-section__title">Live triage</h2>
          <span className="status-section__count">0 routes</span>
        </div>
        <div className="status-empty">No elevated routes in the current live status sample.</div>
      </section>
    );
  }

  return (
    <section className="status-section">
      <div className="status-section__header">
        <h2 className="status-section__title">Live triage</h2>
        <span className="status-section__count">{triage.triage_count} route{triage.triage_count !== 1 ? "s" : ""}</span>
      </div>
      <div className="triage-list">
        {triage.routes.map((route) => (
          <TriageCard route={route} key={route.entity_id} />
        ))}
      </div>
    </section>
  );
}

function TriageCard({ route }: { route: PublicTriageRoute }) {
  const sev = route.severity || "unknown";
  return (
    <a className={`triage-card triage-card--${sev}`} href={`#status/route/${encodeURIComponent(route.entity_id)}`}>
      <div className="triage-card__rank">#{route.rank}</div>
      <div className="triage-card__body">
        <div className="triage-card__header">
          <strong>{route.label}</strong>
          <span className={`route-tile__severity-badge severity-badge--${sev}`}>
            {route.short_summary || route.severity_label}
          </span>
        </div>
        <p>{route.recommended_action}</p>
        <div className="triage-card__evidence">
          {route.evidence.map((item, index) => (
            <span key={`${route.entity_id}-${index}`}>{item}</span>
          ))}
        </div>
      </div>
    </a>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function StatusPage() {
  const { network, routes, alerts, scorecard, feedQuality, triage, loading, refreshing, error, lastUpdatedAt } = useStatusData();
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

  const searchMatchedRoutes = useMemo(
    () => routeList.filter((route) => routeMatchesSearch(route, searchTerm)),
    [routeList, searchTerm],
  );

  const filteredRoutes = useMemo(
    () =>
      searchMatchedRoutes.filter(
        (route) => severityFilter === "all" || route.severity === severityFilter,
      ),
    [searchMatchedRoutes, severityFilter],
  );

  const routeGroups = useMemo(() => groupRoutes(filteredRoutes), [filteredRoutes]);
  const severityCounts = useMemo(() => {
    const counts = new Map<SeverityFilter, number>([["all", searchMatchedRoutes.length]]);
    searchMatchedRoutes.forEach((route) => counts.set(route.severity, (counts.get(route.severity) ?? 0) + 1));
    return counts;
  }, [searchMatchedRoutes]);

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

        {feedQuality && <FeedQualityPanel feedQuality={feedQuality} />}

        {triage && <TriagePanel triage={triage} />}

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
                  placeholder="Red Line, Redline, Green-B, 15"
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
              <h2 className="status-section__title">Priority alerts</h2>
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
                    <td>{formatDelaySignal(corridor.avg_delay_seconds)}</td>
                    <td>{corridor.incident_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {scorecard?.network && (
              <div style={{ marginTop: 10, fontSize: "0.82rem", color: "var(--text-subtle)", textAlign: "right" }}>
                Network: {formatPercent(scorecard.network.healthy_pct, 1)} stable •{" "}
                {formatPercent(scorecard.network.unstable_pct, 1)} at risk •{" "}
                avg delay {formatDelaySignal(scorecard.network.avg_delay_seconds)}
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
