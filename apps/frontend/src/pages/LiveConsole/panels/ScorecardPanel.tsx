import type { ScorecardResponse } from "../../../types/transit";
import {
  actionTone,
  formatDelay,
  formatHazard,
  formatPercent,
  formatActionLabel,
  formatRegimeLabel,
} from "../../../utils/formatters";

interface ScorecardPanelProps {
  scorecardResponse: ScorecardResponse | null;
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

export default function ScorecardPanel({
  scorecardResponse,
  selectedCorridorId,
  onSelectCorridor,
}: ScorecardPanelProps) {
  const scorecardTopCorridors = scorecardResponse?.corridors.slice(0, 4) ?? [];

  return (
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
            <span>Average risk</span>
            <strong>{formatHazard(scorecardResponse?.network.avg_hazard)}</strong>
          </div>
          <div className="detail-card">
            <span>Average delay</span>
            <strong>{formatDelay(scorecardResponse?.network.avg_delay_seconds)}</strong>
          </div>
          <div className="detail-card">
            <span>At-risk snapshots</span>
            <strong>{formatPercent(scorecardResponse?.network.unstable_pct, 1)}</strong>
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
                .map(([action, count]) => `${formatActionLabel(action)} ${count}`)
                .join(" • ") || "No rolling action history yet."}
            </p>
          </article>
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Network service-state mix</strong>
              <span>
                {scorecardResponse?.network.unstable_corridor_count ?? 0} at-risk corridors
              </span>
            </div>
            <p>
              {Object.entries(scorecardResponse?.network.top_regimes ?? {})
                .map(([regime, count]) => `${formatRegimeLabel(regime)} ${count}`)
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
              className={
                corridor.entity_id === selectedCorridorId
                  ? "scorecard-card is-active"
                  : "scorecard-card"
              }
              onClick={() => onSelectCorridor(corridor.entity_id)}
            >
              <div className="scorecard-card__header">
                <strong>{corridor.label}</strong>
                <span className={`badge badge--${actionTone(corridor.top_action)}`}>
                  {formatActionLabel(corridor.top_action)}
                </span>
              </div>
              <div className="scorecard-card__stats">
                <div>
                  <span>Avg risk</span>
                  <strong>{formatHazard(corridor.avg_hazard)}</strong>
                </div>
                <div>
                  <span>P90 risk</span>
                  <strong>{formatHazard(corridor.hazard_p90)}</strong>
                </div>
                <div>
                  <span>Avg delay</span>
                  <strong>{formatDelay(corridor.avg_delay_seconds)}</strong>
                </div>
                <div>
                  <span>At risk</span>
                  <strong>{formatPercent(corridor.unstable_pct, 1)}</strong>
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
                <span>{formatRegimeLabel(corridor.top_regime)}</span>
                <span>{formatPercent(corridor.healthy_pct, 1)} stable snapshots</span>
                <span>{formatPercent(corridor.on_time_pct, 1)} delay under 2m</span>
              </div>
            </button>
          ))}
          {!scorecardTopCorridors.length ? (
            <div className="empty-state">No scorecard history yet for the selected scope.</div>
          ) : null}
        </div>
      </article>
    </section>
  );
}
