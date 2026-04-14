import type { LineCard } from "../../../types/transit";
import {
  formatDelay,
  formatHazard,
  formatSignalPercent,
  formatActionLabel,
  formatActivityReasonLabel,
  formatPriorityLabel,
  formatRegimeLabel,
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
              <strong>{formatDelay(line.median_delay_seconds)}</strong>
            </div>
            <div>
              <span>Headway compression</span>
              <strong>{formatSignalPercent(line.compressed_headway_share)}</strong>
            </div>
            <div>
              <span>Risk score</span>
              <strong>{formatHazard(line.avg_hazard)}</strong>
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
          <h2 className="section__title">Corridor overview</h2>
          <p className="section__hint">
            Current route-level rollups split between live telemetry and later scheduled service.
          </p>
        </div>
      </div>
      <div className="section__header">
        <div>
          <h3 className="section__title">Active Now</h3>
          <p className="section__hint">
            Corridors with current vehicle or trip telemetry, ordered by operational priority.
          </p>
        </div>
      </div>
      <CorridorCards
        lines={activeLines}
        emptyLabel="No active corridors with live telemetry."
        selectedCorridorId={selectedCorridorId}
        onSelectCorridor={onSelectCorridor}
      />
      <div className="section__header">
        <div>
          <h3 className="section__title">Scheduled Later</h3>
          <p className="section__hint">
            Corridors without live telemetry right now that are expected back in service later.
          </p>
        </div>
      </div>
      <CorridorCards
        lines={scheduledLaterLines}
        emptyLabel="No scheduled-later corridors in this snapshot."
        selectedCorridorId={selectedCorridorId}
        onSelectCorridor={onSelectCorridor}
      />
    </section>
  );
}
