import type {
  ScorecardResponse,
  SourceResponse,
  TransitHealth,
} from "../../../types/transit";
import {
  formatActionLabel,
  formatPriorityLabel,
  formatRiskWithScore,
} from "../../../utils/formatters";

interface TechnicalStackPanelProps {
  transitHealth: TransitHealth | null;
  sourceResponse: SourceResponse;
  scorecardResponse: ScorecardResponse | null;
}

const enabledFeedLabels = (sourceResponse: SourceResponse): string[] => {
  const configuredFeeds = sourceResponse.configured_feeds ?? {};
  const labels = Object.entries(configuredFeeds)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name.replace(/_/g, " "));
  return labels.length ? labels : sourceResponse.available?.live ? ["live feed"] : [];
};

const countActionKinds = (transitHealth: TransitHealth | null): number =>
  Object.values(transitHealth?.action_counts ?? {}).filter((count) => count > 0).length;

export default function TechnicalStackPanel({
  transitHealth,
  sourceResponse,
  scorecardResponse,
}: TechnicalStackPanelProps) {
  const feeds = enabledFeedLabels(sourceResponse);
  const worstCorridor = transitHealth?.worst_corridor;
  const replayCount =
    sourceResponse.traces?.length ?? sourceResponse.trace_ids?.length ?? 0;
  const routeCount =
    transitHealth?.visible_line_count ??
    transitHealth?.line_count ??
    scorecardResponse?.corridor_count ??
    0;
  const vehicleCount =
    transitHealth?.feed_status?.vehicle_count ?? transitHealth?.vehicle_count ?? 0;

  return (
    <section className="section panel tech-panel" aria-labelledby="tech-title">
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">Technical stack</span>
          <h2 id="tech-title" className="section__title">
            Built as a live transit intelligence service.
          </h2>
          <p className="section__hint">
            The frontend now surfaces the product as infrastructure: data
            adapters, scoring, persistence, API delivery, and operating limits.
          </p>
        </div>
        <div className="tech-panel__stat">
          <strong>{feeds.length}</strong>
          <span>configured feed lane{feeds.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="tech-grid">
        <article className="tech-card tech-card--lead">
          <span>Runtime graph</span>
          <strong>{vehicleCount} vehicles across {routeCount} scored corridors</strong>
          <p>
            Current state is assembled from static route metadata, vehicle
            positions, trip updates, alerts, and optional overlays.
          </p>
          <div className="signature-card__meta">
            {feeds.map((feed) => (
              <span key={feed}>{feed}</span>
            ))}
          </div>
        </article>

        <article className="tech-card">
          <span>Scoring layer</span>
          <strong>
            {worstCorridor?.label
              ? `${worstCorridor.label} is highest priority`
              : "No priority corridor yet"}
          </strong>
          <p>
            {worstCorridor?.label
              ? `${formatPriorityLabel(
                  worstCorridor.priority_score,
                  worstCorridor.priority_label,
                )}, ${formatRiskWithScore(worstCorridor.hazard)}, ${formatActionLabel(
                  worstCorridor.action,
                  worstCorridor.action_label,
                ).toLowerCase()}.`
              : "The classifier emits corridor regimes, confidence, risk, and recommended actions."}
          </p>
        </article>

        <article className="tech-card">
          <span>State store</span>
          <strong>{scorecardResponse?.window_snapshots ?? 0} retained checks</strong>
          <p>
            Valkey keeps latest payloads, rolling corridor history, vehicle
            history, replay traces, and public status snapshots.
          </p>
        </article>

        <article className="tech-card">
          <span>API surface</span>
          <strong>Dashboard, map, history, scorecard, status</strong>
          <p>
            The browser consumes a consolidated dashboard endpoint, while public
            status and map endpoints remain separately cacheable.
          </p>
        </article>

        <article className="tech-card">
          <span>Hardening</span>
          <strong>{countActionKinds(transitHealth)} active action type{countActionKinds(transitHealth) === 1 ? "" : "s"}</strong>
          <p>
            Live deployment uses bounded API concurrency, cache limits, rate
            limiting, container memory caps, and swap-backed host protection.
          </p>
        </article>
      </div>
    </section>
  );
}
