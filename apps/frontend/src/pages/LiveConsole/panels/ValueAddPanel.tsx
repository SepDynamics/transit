import type {
  ScorecardResponse,
  SourceResponse,
  TransitHealth,
} from "../../../types/transit";
import {
  formatActionLabel,
  formatHazard,
  formatPriorityLabel,
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

  return (
    <section className="value-add" aria-labelledby="value-add-title">
      <div className="value-add__intro">
        <span className="value-add__eyebrow">Value path</span>
        <h2 id="value-add-title">Public signals become prioritized action.</h2>
        <p>
          Schedule, vehicle, trip, and alert evidence is scored by corridor,
          ordered by operating risk, and kept replayable for review.
        </p>
      </div>
      <ol className="value-flow" aria-label="Transit Sentinel value path">
        <li className="value-flow__step">
          <span className="value-flow__number">1</span>
          <div>
            <strong>Public evidence</strong>
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
            <strong>Corridor risk</strong>
            <span>
              {scoredCorridors} corridors scored, average risk{" "}
              {formatHazard(transitHealth?.avg_hazard)}
            </span>
          </div>
        </li>
        <li className="value-flow__step">
          <span className="value-flow__number">3</span>
          <div>
            <strong>Action queue</strong>
            <span>
              {formatPriorityLabel(
                worstCorridor?.priority_score,
                worstCorridor?.priority_label,
              )}{" "}
              priority, {formatActionLabel(worstCorridor?.action, worstCorridor?.action_label)}
              {" "}top action, {actionKindCount} action types active
            </span>
          </div>
        </li>
        <li className="value-flow__step">
          <span className="value-flow__number">4</span>
          <div>
            <strong>Replay proof</strong>
            <span>
              {replayCount} replay trace{replayCount === 1 ? "" : "s"},{" "}
              {scorecardSnapshots} scorecard snapshots
            </span>
          </div>
        </li>
      </ol>
    </section>
  );
}
