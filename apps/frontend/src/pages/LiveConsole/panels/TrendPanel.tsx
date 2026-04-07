import type { TrendResponse } from "../../../types/transit";
import {
  actionTone,
  formatDelay,
  formatHazard,
  humanizeToken,
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
            <h2 className="section__title">Corridor trend watch</h2>
            <p className="section__hint">
              Rolling corridor memory from the persisted transit store, ordered by current
              instability.
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
                  {humanizeToken(corridor.latest_action)}
                </span>
              </div>
              <div className="trend-card__stats">
                <div>
                  <span>Latest hazard</span>
                  <strong>{formatHazard(corridor.latest_hazard)}</strong>
                </div>
                <div>
                  <span>Recent incidents</span>
                  <strong>{corridor.incident_count}</strong>
                </div>
                <div>
                  <span>Median delay</span>
                  <strong>{formatDelay(corridor.latest_delay_seconds)}</strong>
                </div>
                <div>
                  <span>Snapshots</span>
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
                <span>{humanizeToken(corridor.latest_regime)}</span>
                <span>{corridor.recent_actions.map(humanizeToken).join(" • ") || "steady action mix"}</span>
                <span>{humanizeToken(corridor.latest_activity_status)}</span>
              </div>
            </button>
          ))}
          {!trendResponse.corridors.length ? (
            <div className="empty-state">No corridor trend history yet.</div>
          ) : null}
        </div>
      </article>

      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Trend summary</h2>
            <p className="section__hint">
              Recent corridor and action mix over the rolling store window.
            </p>
          </div>
        </div>
        <div className="detail-grid detail-grid--expanded">
          <div className="detail-card">
            <span>Tracked corridors</span>
            <strong>{trendResponse.summary.corridor_count}</strong>
          </div>
          <div className="detail-card">
            <span>Unstable now</span>
            <strong>{trendResponse.summary.unstable_corridor_count}</strong>
          </div>
          <div className="detail-card">
            <span>Recent incidents</span>
            <strong>{trendResponse.summary.recent_incident_count}</strong>
          </div>
        </div>
        <div className="signature-list">
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Recent action mix</strong>
              <span>
                {Object.keys(trendResponse.summary.recent_action_counts).length} actions
              </span>
            </div>
            <p>
              {Object.entries(trendResponse.summary.recent_action_counts)
                .map(([action, count]) => `${humanizeToken(action)} ${count}`)
                .join(" • ") || "No recent action memory yet."}
            </p>
          </article>
          <article className="signature-card">
            <div className="signature-card__header">
              <strong>Recent regime mix</strong>
              <span>
                {Object.keys(trendResponse.summary.recent_regime_counts).length} regimes
              </span>
            </div>
            <p>
              {Object.entries(trendResponse.summary.recent_regime_counts)
                .map(([regime, count]) => `${humanizeToken(regime)} ${count}`)
                .join(" • ") || "No recent regime memory yet."}
            </p>
          </article>
        </div>
      </article>
    </section>
  );
}
