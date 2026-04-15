import type { ScorecardResponse, TransitHealth } from "../../../types/transit";
import {
  formatActionLabel,
  formatDelay,
  formatPriorityLabel,
  formatRiskWithScore,
} from "../../../utils/formatters";

interface WhatThisMeansPanelProps {
  transitHealth: TransitHealth | null;
  scorecardResponse: ScorecardResponse | null;
}

export default function WhatThisMeansPanel({
  transitHealth,
  scorecardResponse,
}: WhatThisMeansPanelProps) {
  const worstCorridor = transitHealth?.worst_corridor;
  const incidentCount =
    transitHealth?.incident_count ?? scorecardResponse?.total_incidents ?? 0;
  const activeRoutes =
    transitHealth?.active_line_count ?? transitHealth?.visible_line_count ?? 0;
  const vehicleCount =
    transitHealth?.feed_status?.vehicle_count ?? transitHealth?.vehicle_count ?? 0;

  return (
    <section className="section panel meaning-panel" aria-labelledby="meaning-title">
      <div className="section__header">
        <div>
          <span className="section-eyebrow">Operational readout</span>
          <h2 id="meaning-title" className="section__title">
            The current state emitted by the scoring layer.
          </h2>
          <p className="section__hint">
            Start with the top route, then inspect the supporting signals.
          </p>
        </div>
      </div>
      <div className="meaning-grid">
        <article className="meaning-card">
          <span>Top queue item</span>
          <strong>
            {worstCorridor?.label
              ? `${worstCorridor.label} is the first route to check.`
              : "Service has no clear top concern yet."}
          </strong>
          <p>
            {worstCorridor?.label
              ? `${formatPriorityLabel(
                  worstCorridor.priority_score,
                  worstCorridor.priority_label,
                )} priority, ${formatRiskWithScore(worstCorridor.hazard)}, with ${formatActionLabel(
                  worstCorridor.action,
                  worstCorridor.action_label,
                ).toLowerCase()} as the next move.`
              : "The ranking will name the route, risk level, and next move when the feed provides enough evidence."}
          </p>
        </article>
        <article className="meaning-card">
          <span>Open signals</span>
          <strong>{incidentCount} problem signal{incidentCount === 1 ? "" : "s"}</strong>
          <p>
            Each signal is a reason to look closer, not a raw alert someone must
            interpret from scratch.
          </p>
        </article>
        <article className="meaning-card">
          <span>Coverage</span>
          <strong>
            {activeRoutes} routes, {vehicleCount} vehicles
          </strong>
          <p>
            The system can watch the network while staff focus on the short list.
          </p>
        </article>
        <article className="meaning-card">
          <span>Rolling baseline</span>
          <strong>
            {formatDelay(scorecardResponse?.network.avg_delay_seconds)} average saved
            delay
          </strong>
          <p>
            Saved checks create a baseline for any downstream analysis or
            integration.
          </p>
        </article>
      </div>
    </section>
  );
}
