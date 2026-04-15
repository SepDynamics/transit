import type { LineCard } from "../../../types/transit";
import {
  formatDelaySignal,
  formatSignalPercent,
  formatActionLabel,
  formatActivityReasonLabel,
  formatPriorityLabel,
  formatRegimeLabel,
  formatRiskWithScore,
  priorityTone,
} from "../../../utils/formatters";

interface CorridorOverviewProps {
  activeLines: LineCard[];
  scheduledLaterLines: LineCard[];
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

interface CorridorCardsProps {
  lines: LineCard[];
  emptyLabel: string;
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

function CorridorCards({
  lines,
  emptyLabel,
  selectedCorridorId,
  onSelectCorridor,
}: CorridorCardsProps) {
  return (
    <div className="node-grid">
      {lines.map((line) => (
        <button
          key={line.entity_id}
          type="button"
          className={line.entity_id === selectedCorridorId ? "node-card is-active" : "node-card"}
          onClick={() => onSelectCorridor(line.entity_id)}
        >
          <div className="node-card__header">
            <strong>{line.label}</strong>
            <span
              className={`badge badge--${priorityTone(
                line.priority_score,
                line.priority_label,
              )}`}
            >
              {`${formatPriorityLabel(line.priority_score, line.priority_label)} • ${formatActionLabel(
                line.top_action,
                line.top_action_label,
              )}`}
            </span>
          </div>
          <div className="node-card__stats">
            <div>
              <span>Vehicles</span>
              <strong>{line.vehicle_count}</strong>
            </div>
            <div>
              <span>Median delay</span>
              <strong>{formatDelaySignal(line.median_delay_seconds)}</strong>
            </div>
            <div>
              <span>Vehicles bunching</span>
              <strong>{formatSignalPercent(line.compressed_headway_share)}</strong>
            </div>
            <div>
              <span>Risk</span>
              <strong>{formatRiskWithScore(line.avg_hazard)}</strong>
            </div>
            <div>
              <span>Alerts</span>
              <strong>{line.active_alert_count}</strong>
            </div>
          </div>
          <div className="signature-card__meta">
            <span>{formatRegimeLabel(line.current_regime, line.current_regime_label)}</span>
            <span>
              {formatActivityReasonLabel(
                line.activity_reason,
                line.activity_reason_label,
              )}
            </span>
          </div>
        </button>
      ))}
      {!lines.length ? <div className="empty-state">{emptyLabel}</div> : null}
    </div>
  );
}

export default function CorridorOverview({
  activeLines,
  scheduledLaterLines,
  selectedCorridorId,
  onSelectCorridor,
}: CorridorOverviewProps) {
  return (
    <section className="section panel">
      <div className="section__header">
        <div>
          <h2 className="section__title">Route overview</h2>
          <p className="section__hint">
            Routes with live vehicle data come first, sorted by what needs attention.
          </p>
        </div>
      </div>
      <div className="section__header">
        <div>
          <h3 className="section__title">Active Now</h3>
          <p className="section__hint">
            Routes with vehicles or trip updates reporting right now.
          </p>
        </div>
      </div>
      <CorridorCards
        lines={activeLines}
        emptyLabel="No active routes with live data."
        selectedCorridorId={selectedCorridorId}
        onSelectCorridor={onSelectCorridor}
      />
      <div className="section__header">
        <div>
          <h3 className="section__title">Scheduled Later</h3>
          <p className="section__hint">
            Routes expected to return later, but not reporting vehicles right now.
          </p>
        </div>
      </div>
      <CorridorCards
        lines={scheduledLaterLines}
        emptyLabel="No later routes in this check."
        selectedCorridorId={selectedCorridorId}
        onSelectCorridor={onSelectCorridor}
      />
    </section>
  );
}
