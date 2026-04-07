import type { TransitHealth } from "../../../types/transit";
import {
  formatHazard,
  formatPercent,
  formatPriorityLabel,
  formatRegimeLabel,
} from "../../../utils/formatters";

interface OverviewMetricsProps {
  transitHealth: TransitHealth | null;
}

export default function OverviewMetrics({ transitHealth }: OverviewMetricsProps) {
  return (
    <section className="overview-grid">
      <article className="metric-card panel">
        <span className="metric-card__label">Active now</span>
        <strong className="metric-card__value">
          {transitHealth?.active_line_count ?? transitHealth?.line_count ?? 0}
        </strong>
        <span className="metric-card__meta">{transitHealth?.vehicle_count ?? 0} visible vehicles</span>
      </article>
      <article className="metric-card panel">
        <span className="metric-card__label">Scheduled later</span>
        <strong className="metric-card__value">
          {transitHealth?.scheduled_later_line_count ?? 0}
        </strong>
        <span className="metric-card__meta">
          {transitHealth?.visible_line_count ?? 0} surfaced corridors
        </span>
      </article>
      <article className="metric-card panel">
        <span className="metric-card__label">Average risk</span>
        <strong className="metric-card__value">{formatHazard(transitHealth?.avg_hazard)}</strong>
        <span className="metric-card__meta">max {formatHazard(transitHealth?.max_hazard)}</span>
      </article>
      <article className="metric-card panel">
        <span className="metric-card__label">Open incidents</span>
        <strong className="metric-card__value">{transitHealth?.incident_count ?? 0}</strong>
        <span className="metric-card__meta">{transitHealth?.critical_incidents ?? 0} critical</span>
      </article>
      <article className="metric-card panel">
        <span className="metric-card__label">Score confidence</span>
        <strong className="metric-card__value">
          {formatPercent((transitHealth?.avg_confidence ?? 0) * 100)}
        </strong>
        <span className="metric-card__meta">
          {Object.entries(transitHealth?.regime_counts ?? {})
            .map(([regime, count]) => `${formatRegimeLabel(regime)} ${count}`)
            .join(" • ") || "no corridor mix yet"}
        </span>
      </article>
      <article className="metric-card panel">
        <span className="metric-card__label">Most urgent corridor</span>
        <strong className="metric-card__value">
          {transitHealth?.worst_corridor?.label ?? "n/a"}
        </strong>
        <span className="metric-card__meta">
          {`${formatPriorityLabel(
            transitHealth?.worst_corridor?.priority_score,
            transitHealth?.worst_corridor?.priority_label,
          )} • ${formatRegimeLabel(
            transitHealth?.worst_corridor?.regime,
            transitHealth?.worst_corridor?.regime_label,
          )}`}
        </span>
      </article>
    </section>
  );
}
