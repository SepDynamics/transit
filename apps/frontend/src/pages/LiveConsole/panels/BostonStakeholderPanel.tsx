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

interface BostonStakeholderPanelProps {
  transitHealth: TransitHealth | null;
  sourceResponse: SourceResponse;
  scorecardResponse: ScorecardResponse | null;
}

const sourceCards = [
  {
    title: "Boston wants shorter commutes.",
    body:
      "Go Boston 2030 names reliability, access, lower emissions, and more transit use as city goals.",
    sourceLabel: "Go Boston 2030",
    sourceUrl: "https://www.boston.gov/departments/transportation/go-boston-2030",
  },
  {
    title: "BTD already builds bus priority.",
    body:
      "Bus lanes, queue jumps, signal priority, bus bulbs, and curb changes need simple before-and-after proof.",
    sourceLabel: "Boston Bus Priority",
    sourceUrl: "https://www.boston.gov/departments/transportation/bus-priority",
  },
  {
    title: "MBTA has named the highest-impact corridors.",
    body:
      "The MBTA Transit Priority Vision focuses on 26 corridors that carry most bus riders.",
    sourceLabel: "MBTA Transit Priority",
    sourceUrl: "https://mbta.getanchor.io/projects/transit-priority-strategy.html",
  },
  {
    title: "The buying language is data and analytics.",
    body:
      "Boston recently solicited Mobility Data and Analytics Service work through its Supplier Portal.",
    sourceLabel: "Boston bid EV00016129",
    sourceUrl: "https://www.boston.gov/bid-listings/ev00016129",
  },
];

const countConfiguredFeeds = (sourceResponse: SourceResponse): number => {
  const configuredFeeds = sourceResponse.configured_feeds ?? {};
  const enabledCount = Object.values(configuredFeeds).filter(Boolean).length;
  if (enabledCount) return enabledCount;
  return sourceResponse.available?.live ? 1 : 0;
};

export default function BostonStakeholderPanel({
  transitHealth,
  sourceResponse,
  scorecardResponse,
}: BostonStakeholderPanelProps) {
  const vehicleCount =
    transitHealth?.feed_status?.vehicle_count ?? transitHealth?.vehicle_count ?? 0;
  const scoredRoutes =
    transitHealth?.visible_line_count ??
    transitHealth?.line_count ??
    scorecardResponse?.corridor_count ??
    0;
  const activeRoutes =
    transitHealth?.active_line_count ?? transitHealth?.visible_line_count ?? 0;
  const worstCorridor = transitHealth?.worst_corridor;
  const feedCount = countConfiguredFeeds(sourceResponse);
  const topMove = formatActionLabel(
    worstCorridor?.action,
    worstCorridor?.action_label,
  );

  return (
    <section className="section panel boston-panel" aria-labelledby="boston-title">
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">For Boston decision makers</span>
          <h2 id="boston-title" className="section__title">
            Make bus delays easy to explain, fund, and fix.
          </h2>
          <p className="section__hint">
            The pitch is simple: use the public data Boston and MBTA already
            publish, point staff to the routes that need attention, and keep a
            record strong enough for grant reports, council briefings, and pilot
            decisions.
          </p>
        </div>
        <div className="boston-panel__live-summary">
          <strong>{vehicleCount}</strong>
          <span>vehicles watched from {feedCount} public feed lane{feedCount === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="current-proof">
        <span>Current Boston signals</span>
        <p>
          <a
            href="https://www.itskrs.its.dot.gov/SRC-2025"
            target="_blank"
            rel="noreferrer"
          >
            Jan. 2025 MBTA/Boston TSP partnership
          </a>{" "}
          after the Brighton Avenue test, plus{" "}
          <a
            href="https://www.boston.gov/departments/emerging-technology/boston-curb-lab-using-ai-and-open-data-improve-curb-management"
            target="_blank"
            rel="noreferrer"
          >
            Feb. 2026 Curb Lab pilots
          </a>{" "}
          for curb data, real-time integrations, and street decision support.
        </p>
      </div>

      <div className="stakeholder-grid">
        <article className="stakeholder-card stakeholder-card--lead">
          <span>Plain-English answer</span>
          <strong>
            {worstCorridor?.label
              ? `${worstCorridor.label} needs the first look.`
              : "No urgent route has surfaced yet."}
          </strong>
          <p>
            {worstCorridor?.label
              ? `${formatPriorityLabel(
                  worstCorridor.priority_score,
                  worstCorridor.priority_label,
                )} priority. Suggested move: ${topMove}. Network risk is ${formatRiskWithScore(
                  transitHealth?.avg_hazard,
                )}.`
              : "When the feed flags a problem, staff get the top route and next move in plain language."}
          </p>
        </article>
        <article className="stakeholder-card">
          <span>Where are buses stuck?</span>
          <strong>{activeRoutes} live routes</strong>
          <p>
            Staff can start with active routes instead of scanning every line in
            the system.
          </p>
        </article>
        <article className="stakeholder-card">
          <span>Which fix goes first?</span>
          <strong>{scoredRoutes} routes ranked</strong>
          <p>
            Bus lanes, signal timing, and curb rules can be aimed at the places
            with the clearest evidence.
          </p>
        </article>
        <article className="stakeholder-card">
          <span>Can we prove it worked?</span>
          <strong>{scorecardResponse?.window_snapshots ?? 0} saved checks</strong>
          <p>
            Before-and-after records turn a pilot from a debate into a measured
            decision.
          </p>
        </article>
      </div>

      <div className="source-grid" aria-label="Boston and MBTA evidence">
        {sourceCards.map((card) => (
          <article className="source-card" key={card.title}>
            <h3>{card.title}</h3>
            <p>{card.body}</p>
            <a href={card.sourceUrl} target="_blank" rel="noreferrer">
              {card.sourceLabel}
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}
