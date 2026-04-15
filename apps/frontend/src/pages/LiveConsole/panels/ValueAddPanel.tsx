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

interface ValueAddPanelProps {
  transitHealth: TransitHealth | null;
  sourceResponse: SourceResponse;
  scorecardResponse: ScorecardResponse | null;
}

const countConfiguredFeeds = (sourceResponse: SourceResponse): number => {
  const configuredFeeds = sourceResponse.configured_feeds ?? {};
  const enabledCount = Object.values(configuredFeeds).filter(Boolean).length;
  if (enabledCount) return enabledCount;
  return sourceResponse.available?.live ? 1 : 0;
};

const countActionKinds = (transitHealth: TransitHealth | null): number =>
  Object.values(transitHealth?.action_counts ?? {}).filter((count) => count > 0).length;

export default function ValueAddPanel({
  transitHealth,
  sourceResponse,
  scorecardResponse,
}: ValueAddPanelProps) {
  const feedCount = countConfiguredFeeds(sourceResponse);
  const replayCount =
    sourceResponse.traces?.length ?? sourceResponse.trace_ids?.length ?? 0;
  const actionKindCount = countActionKinds(transitHealth);
  const worstCorridor = transitHealth?.worst_corridor;
  const vehicleCount = transitHealth?.feed_status?.vehicle_count ?? transitHealth?.vehicle_count ?? 0;
  const tripUpdateCount = transitHealth?.feed_status?.trip_update_count ?? 0;
  const alertCount = transitHealth?.feed_status?.alert_count ?? 0;
  const scoredCorridors =
    transitHealth?.visible_line_count ??
    transitHealth?.line_count ??
    scorecardResponse?.corridor_count ??
    0;
  const scorecardSnapshots = scorecardResponse?.window_snapshots ?? 0;
  const feedLaneLabel = `${feedCount} feed lane${feedCount === 1 ? "" : "s"}`;
  const topConcern = worstCorridor?.label ?? "no urgent route yet";

  return (
    <section className="value-add" aria-labelledby="value-add-title">
      <div className="value-add__intro">
        <span className="value-add__eyebrow">What it does</span>
        <h2 id="value-add-title">From public data to a clear next move.</h2>
        <p>
          Instead of asking people to read raw feeds, Transit Sentinel explains
          the issue, ranks the response, and saves the evidence.
        </p>
      </div>
      <ol className="value-flow" aria-label="Transit Sentinel value path">
        <li className="value-flow__step">
          <span className="value-flow__number">1</span>
          <div>
            <strong>Reads the public feed</strong>
            <span>
              {feedLaneLabel}, {vehicleCount} vehicles, {tripUpdateCount} trip updates,
              {" "}
              {alertCount} alerts
            </span>
          </div>
        </li>
        <li className="value-flow__step">
          <span className="value-flow__number">2</span>
          <div>
            <strong>Finds route trouble</strong>
            <span>
              {scoredCorridors} routes checked. Network risk is{" "}
              {formatRiskWithScore(transitHealth?.avg_hazard)}
            </span>
          </div>
        </li>
        <li className="value-flow__step">
          <span className="value-flow__number">3</span>
          <div>
            <strong>Ranks the response</strong>
            <span>
              Top concern: {topConcern}.{" "}
              {formatPriorityLabel(
                worstCorridor?.priority_score,
                worstCorridor?.priority_label,
              )}{" "}
              priority. Suggested move:{" "}
              {formatActionLabel(worstCorridor?.action, worstCorridor?.action_label)}.
              {" "}
              {actionKindCount} move type{actionKindCount === 1 ? "" : "s"} active
            </span>
          </div>
        </li>
        <li className="value-flow__step">
          <span className="value-flow__number">4</span>
          <div>
            <strong>Keeps the receipt</strong>
            <span>
              {replayCount} replay trace{replayCount === 1 ? "" : "s"},{" "}
              {scorecardSnapshots} saved check{scorecardSnapshots === 1 ? "" : "s"}
            </span>
          </div>
        </li>
      </ol>
    </section>
  );
}
