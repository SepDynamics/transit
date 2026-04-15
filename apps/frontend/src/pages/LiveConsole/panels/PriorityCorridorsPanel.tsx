import type {
  LineCard,
  ScorecardResponse,
  TransitScorecardCorridor,
} from "../../../types/transit";
import {
  compareOperationalPriority,
  formatActionLabel,
  formatPercent,
  formatPriorityLabel,
  formatRiskWithScore,
  priorityTone,
} from "../../../utils/formatters";

interface PriorityCorridorsPanelProps {
  lines: LineCard[];
  scorecardResponse: ScorecardResponse | null;
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

interface PriorityCorridorRow {
  line: LineCard | null;
  scorecard: TransitScorecardCorridor | null;
  vehicleCount: number;
  alertCount: number;
  snapshotCount: number;
}

const REGIME_SUMMARY: Record<string, string> = {
  healthy: "stable service",
  recovering: "recovering service",
  data_sparse: "limited telemetry",
  bunching_onset: "early bunching",
  corridor_unstable: "irregular service",
  headway_collapse: "severe bunching",
  service_degraded: "confirmed disruption",
  terminal_congestion: "terminal congestion",
  stop_dwell_instability: "extended dwell",
  feed_incoherent: "telemetry issue",
};

const buildPriorityRows = (
  lines: LineCard[],
  scorecardResponse: ScorecardResponse | null,
): PriorityCorridorRow[] => {
  const seen = new Set<string>();
  const rows: PriorityCorridorRow[] = [];

  for (const line of [...lines].sort(compareOperationalPriority)) {
    if (!line.entity_id || seen.has(line.entity_id)) continue;
    seen.add(line.entity_id);
    const scorecard =
      scorecardResponse?.corridors.find(
        (corridor) => corridor.entity_id === line.entity_id,
      ) ?? null;
    rows.push({
      line,
      scorecard,
      vehicleCount: line.vehicle_count ?? 0,
      alertCount: line.active_alert_count ?? 0,
      snapshotCount: scorecard?.snapshot_count ?? 0,
    });
    if (rows.length >= 6) return rows;
  }

  const scorecardRows =
    scorecardResponse?.corridors
      .filter((corridor) => !seen.has(corridor.entity_id))
      .sort(compareOperationalPriority)
      .slice(0, Math.max(0, 6 - rows.length)) ?? [];

  return [
    ...rows,
    ...scorecardRows.map((scorecard) => ({
      line: null,
      scorecard,
      vehicleCount: 0,
      alertCount: scorecard.incident_count ?? 0,
      snapshotCount: scorecard.snapshot_count ?? 0,
    })),
  ];
};

const rowLabel = (row: PriorityCorridorRow): string =>
  row.line?.label ?? row.scorecard?.label ?? "Unknown corridor";

const rowMeta = (row: PriorityCorridorRow): string => {
  const routeId = row.line?.route_id ?? row.scorecard?.route_id;
  const status = row.line?.activity_status_label ?? row.line?.activity_status;
  return [routeId ? `Route ${routeId}` : null, status].filter(Boolean).join(" / ");
};

const rowSummary = (row: PriorityCorridorRow): string => {
  const action = formatActionLabel(
    row.line?.top_action ?? row.scorecard?.top_action,
    row.line?.top_action_label,
  );
  const regime = row.line?.current_regime;
  if (regime) {
    return REGIME_SUMMARY[regime] ?? "active route signal";
  }
  if (row.scorecard) {
    return `${formatPercent(row.scorecard.unstable_pct, 0)} at-risk checks`;
  }
  return action;
};

export default function PriorityCorridorsPanel({
  lines,
  scorecardResponse,
  selectedCorridorId,
  onSelectCorridor,
}: PriorityCorridorsPanelProps) {
  const rows = buildPriorityRows(lines, scorecardResponse);

  return (
    <section
      className="section panel priority-panel"
      aria-labelledby="priority-corridors-title"
    >
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">Priority queue</span>
          <h2 id="priority-corridors-title" className="section__title">
            Routes ranked by live operating risk.
          </h2>
          <p className="section__hint">
            This list comes directly from the classifier and rolling scorecard,
            without a fixed city-specific corridor list.
          </p>
        </div>
      </div>

      <div className="priority-corridor-grid">
        {rows.map((row) => {
          const route = row.line ?? row.scorecard;
          const entityId = row.line?.entity_id ?? row.scorecard?.entity_id;
          const isActive = entityId === selectedCorridorId;
          const priorityLabel = row.line
            ? formatPriorityLabel(row.line.priority_score, row.line.priority_label)
            : row.scorecard?.unstable_pct
              ? `${formatPercent(row.scorecard.unstable_pct, 0)} at-risk checks`
              : "Ready to map";
          const risk = row.line?.avg_hazard ?? row.scorecard?.avg_hazard;

          return (
            <button
              key={entityId ?? rowLabel(row)}
              type="button"
              className={
                isActive
                  ? "priority-corridor-card is-active"
                  : "priority-corridor-card"
              }
              disabled={!entityId}
              onClick={() => {
                if (entityId) onSelectCorridor(entityId);
              }}
            >
              <div className="priority-corridor-card__header">
                <div>
                  <strong>{rowLabel(row)}</strong>
                  <span>{rowMeta(row) || "Scored corridor"}</span>
                </div>
                <span
                  className={`badge badge--${priorityTone(
                    row.line?.priority_score,
                    row.line?.priority_label,
                  )}`}
                >
                  {priorityLabel}
                </span>
              </div>
              <p>{rowSummary(row)}</p>
              <div className="priority-corridor-card__metrics">
                <div>
                  <span>Live vehicles</span>
                  <strong>{row.vehicleCount}</strong>
                </div>
                <div>
                  <span>Alerts</span>
                  <strong>{row.alertCount}</strong>
                </div>
                <div>
                  <span>Risk</span>
                  <strong>{formatRiskWithScore(risk)}</strong>
                </div>
                <div>
                  <span>Checks</span>
                  <strong>{row.snapshotCount}</strong>
                </div>
              </div>
              <div className="signature-card__meta">
                <span>{formatRiskWithScore(route?.avg_hazard ?? risk)}</span>
                <span>
                  {route
                    ? formatActionLabel(
                        row.line?.top_action ?? row.scorecard?.top_action,
                        row.line?.top_action_label,
                      )
                    : "Needs route mapping"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
