import type { TrendResponse } from "../../../types/transit";
import {
  actionTone,
  formatDelay,
  formatActionLabel,
  formatActivityStatusLabel,
  formatRegimeLabel,
  formatRiskWithScore,
} from "../../../utils/formatters";

interface TrendPanelProps {
  trendResponse: TrendResponse;
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

export default function TrendPanel({
  trendResponse,
  selectedCorridorId,
  onSelectCorridor,
}: TrendPanelProps) {
  return (
    <section className="section split-grid">
      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Risk getting better or worse</h2>
            <p className="section__hint">
              Recent saved checks, sorted by the routes that need attention now.
            </p>
          </div>
        </div>
        <div className="signature-list">
          {trendResponse.corridors.map((corridor) => (
            <button
              key={corridor.entity_id}
              type="button"
              className={
                corridor.entity_id === selectedCorridorId
                  ? "trend-card is-active"
                  : "trend-card"
              }
              onClick={() => onSelectCorridor(corridor.entity_id)}
            >
              <div className="signature-card__header">
                <strong>{corridor.label}</strong>
                <span className={`badge badge--${actionTone(corridor.latest_action)}`}>
                  {formatActionLabel(corridor.latest_action)}
                </span>
              </div>
              <div className="trend-card__stats">
                <div>
                  <span>Latest risk</span>
                  <strong>{formatRiskWithScore(corridor.latest_hazard)}</strong>
                </div>
                <div>
                  <span>Recent problems</span>
                  <strong>{corridor.incident_count}</strong>
                </div>
                <div>
                  <span>Median delay</span>
                  <strong>{formatDelay(corridor.latest_delay_seconds)}</strong>
                </div>
                <div>
                  <span>Checks</span>
                  <strong>{corridor.snapshot_count}</strong>
                </div>
              </div>
              <div className="trend-sparkline">
                {corridor.hazard_series.map((value, index) => (
                  <span
                    key={`${corridor.entity_id}-${index}`}
                    className={`trend-sparkline__bar trend-sparkline__bar--${
                      value >= 0.75 ? "danger" : value >= 0.45 ? "warning" : "calm"
                    }`}
                    style={{ height: `${16 + Math.max(0, value) * 84}%` }}
                  />
                ))}
              </div>
              <div className="signature-card__meta">
                <span>{formatRegimeLabel(corridor.latest_regime)}</span>
                <span>
                  {corridor.recent_actions.map((action) => formatActionLabel(action)).join(" • ") ||
                    "steady action mix"}
                </span>
                <span>{formatActivityStatusLabel(corridor.latest_activity_status)}</span>
              </div>
            </button>
          ))}
          {!trendResponse.corridors.length ? (
            <div className="empty-state">No saved route history yet.</div>
          ) : null}
        </div>
      </article>

      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Recent pattern summary</h2>
            <p className="section__hint">
              A plain count of routes, problems, and repeated recommended moves.
            </p>
          </div>
        </div>
        <div className="detail-grid detail-grid--expanded">
          <div className="detail-card">
            <span>Routes checked</span>
            <strong>{trendResponse.summary.corridor_count}</strong>
          </div>
          <div className="detail-card">
            <span>At risk now</span>
            <strong>{trendResponse.summary.unstable_corridor_count}</strong>
          </div>
          <div className="detail-card">
            <span>Recent problems</span>
            <strong>{trendResponse.summary.recent_incident_count}</strong>
          </div>
        </div>
        <div className="signature-list">
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Recommended moves</strong>
              <span>{Object.keys(trendResponse.summary.recent_action_counts).length} types</span>
            </div>
            <p>
              {Object.entries(trendResponse.summary.recent_action_counts)
                .map(([action, count]) => `${formatActionLabel(action)} ${count}`)
                .join(" • ") || "No recent move history yet."}
            </p>
          </article>
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Service patterns</strong>
              <span>
                {Object.keys(trendResponse.summary.recent_regime_counts).length} states
              </span>
            </div>
            <p>
              {Object.entries(trendResponse.summary.recent_regime_counts)
                .map(([regime, count]) => `${formatRegimeLabel(regime)} ${count}`)
                .join(" • ") || "No recent service pattern yet."}
            </p>
          </article>
        </div>
      </article>
    </section>
  );
}
