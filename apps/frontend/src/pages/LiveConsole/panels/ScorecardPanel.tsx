import type { ScorecardResponse } from "../../../types/transit";
import {
  actionTone,
  formatDelay,
  formatPercent,
  formatActionLabel,
  formatRegimeLabel,
  formatRiskWithScore,
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
            <h2 className="section__title">Reliability proof</h2>
            <p className="section__hint">
              A saved record of how service looked over recent public-data checks.
            </p>
          </div>
        </div>
        <div className="detail-grid detail-grid--expanded">
          <div className="detail-card">
            <span>Saved checks</span>
            <strong>{scorecardResponse?.window_snapshots ?? 0}</strong>
          </div>
          <div className="detail-card">
            <span>Routes tracked</span>
            <strong>{scorecardResponse?.corridor_count ?? 0}</strong>
          </div>
          <div className="detail-card">
            <span>Problems found</span>
            <strong>{scorecardResponse?.total_incidents ?? 0}</strong>
          </div>
          <div className="detail-card">
            <span>Network risk</span>
            <strong>{formatRiskWithScore(scorecardResponse?.network.avg_hazard)}</strong>
          </div>
          <div className="detail-card">
            <span>Average delay</span>
            <strong>{formatDelay(scorecardResponse?.network.avg_delay_seconds)}</strong>
          </div>
          <div className="detail-card">
            <span>At-risk checks</span>
            <strong>{formatPercent(scorecardResponse?.network.unstable_pct, 1)}</strong>
          </div>
        </div>
        <div className="signature-list">
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Action history</strong>
              <span>{Object.keys(scorecardResponse?.network.top_actions ?? {}).length} move types</span>
            </div>
            <p>
              {Object.entries(scorecardResponse?.network.top_actions ?? {})
                .map(([action, count]) => `${formatActionLabel(action)} ${count}`)
                .join(" • ") || "No saved action history yet."}
            </p>
          </article>
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Service pattern history</strong>
              <span>{scorecardResponse?.network.unstable_corridor_count ?? 0} routes at risk</span>
            </div>
            <p>
              {Object.entries(scorecardResponse?.network.top_regimes ?? {})
                .map(([regime, count]) => `${formatRegimeLabel(regime)} ${count}`)
                .join(" • ") || "No saved pattern history yet."}
            </p>
          </article>
        </div>
      </article>

      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Routes to watch</h2>
            <p className="section__hint">
              The routes with the most trouble in the saved checks.
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
                  <span>Average risk</span>
                  <strong>{formatRiskWithScore(corridor.avg_hazard)}</strong>
                </div>
                <div>
                  <span>Worst 10%</span>
                  <strong>{formatRiskWithScore(corridor.hazard_p90)}</strong>
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
                  <span>Problems</span>
                  <strong>{corridor.incident_count}</strong>
                </div>
                <div>
                  <span>Checks</span>
                  <strong>{corridor.snapshot_count}</strong>
                </div>
              </div>
              <div className="signature-card__meta">
                <span>{formatRegimeLabel(corridor.top_regime)}</span>
                <span>{formatPercent(corridor.healthy_pct, 1)} normal checks</span>
                <span>{formatPercent(corridor.on_time_pct, 1)} delay under 2m</span>
              </div>
            </button>
          ))}
          {!scorecardTopCorridors.length ? (
            <div className="empty-state">No saved reliability checks yet for this view.</div>
          ) : null}
        </div>
      </article>
    </section>
  );
}
