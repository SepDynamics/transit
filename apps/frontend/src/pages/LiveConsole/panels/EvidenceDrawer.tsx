import type {
  CorridorHistoryResponse,
  IncidentResponse,
  RegimeResponse,
  TransitIncidentRecord,
  TransitRegimePayload,
} from "../../../types/transit";
import {
  compareOperationalPriority,
  formatActionLabel,
  formatDelaySignal,
  formatHazard,
  formatPercent,
  formatPriorityLabel,
  formatRegimeLabel,
  formatRiskWithScore,
  formatSignalPercent,
  humanizeToken,
  relativeTimeFromMs,
  topFactorSummary,
} from "../../../utils/formatters";

interface CorridorDisplay {
  entity_id: string;
  label?: string;
  avg_hazard?: number;
  median_delay_seconds?: number;
  vehicle_count?: number;
  current_regime?: string | null;
  current_regime_label?: string | null;
  priority_score?: number;
  priority_label?: string;
}

interface EvidenceDrawerProps {
  selectedCorridor: CorridorDisplay | null;
  selectedCorridorId: string | null;
  corridorHistory: CorridorHistoryResponse;
  incidentResponse: IncidentResponse;
  regimeResponse: RegimeResponse;
}

interface EvidenceFactor {
  key: string;
  label: string;
  value: string;
}

const SIGNAL_LABELS: Record<string, string> = {
  healthy: "Service normal",
  recovering: "Recovering",
  data_sparse: "Telemetry degraded",
  bunching_onset: "Early bunching",
  headway_collapse: "Severe bunching / service gap",
  terminal_congestion: "Terminal congestion",
  terminal_blocked: "Terminal congestion",
  stop_dwell_instability: "Service irregularity",
  corridor_unstable: "Service irregularity",
  service_degraded: "Confirmed disruption",
  feed_incoherent: "Telemetry degraded",
  dispatch_relief: "Send extra service",
  short_turn: "Turn a vehicle early",
  inspect_terminal: "Check the terminal",
  hold: "Hold vehicles to even service",
  warn_riders: "Tell riders",
  mark_feed_degraded: "Flag bad data",
  monitor: "Watch",
  compressed_headway_share: "Compressed headways",
  terminal_backlog_count: "Terminal backlog",
  dwell_overrun_share: "Long stop dwell",
  delay_spread_seconds: "Delay spread",
  median_delay_seconds: "Median delay",
  position_coverage: "Vehicle position coverage",
  trip_update_coverage: "Trip update coverage",
  feed_age_seconds: "Feed age",
};

const operatorSignalLabel = (token?: string | null, fallback?: string | null): string => {
  if (!token) return fallback || "No signal";
  return SIGNAL_LABELS[token] ?? fallback ?? humanizeToken(token);
};

const formatEvidenceValue = (value?: number | null): string => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  if (value >= 0 && value <= 1) return formatSignalPercent(value);
  return formatPercent(value, 0);
};

const latestRegimeForSelection = (
  selectedCorridorId: string,
  corridorHistory: CorridorHistoryResponse,
  regimeResponse: RegimeResponse,
): TransitRegimePayload | null => {
  const historyRegime = corridorHistory.regimes[corridorHistory.regimes.length - 1];
  if (historyRegime?.entity_id === selectedCorridorId) return historyRegime;
  return regimeResponse.regimes.find((regime) => regime.entity_id === selectedCorridorId) ?? null;
};

const incidentMatchesSelection = (
  incident: TransitIncidentRecord,
  selectedCorridorId: string,
): boolean =>
  incident.entity_id === selectedCorridorId ||
  Boolean(incident.corridor_id && incident.corridor_id === selectedCorridorId);

const incidentsForSelection = (
  selectedCorridorId: string,
  corridorHistory: CorridorHistoryResponse,
  incidentResponse: IncidentResponse,
): TransitIncidentRecord[] => {
  const byId = new Map<string, TransitIncidentRecord>();
  for (const incident of [...incidentResponse.incidents, ...corridorHistory.incidents]) {
    if (!incidentMatchesSelection(incident, selectedCorridorId)) continue;
    byId.set(incident.incident_id, incident);
  }
  return [...byId.values()].sort(compareOperationalPriority);
};

const factorEvidence = (
  regime: TransitRegimePayload | null,
  incident: TransitIncidentRecord | null,
): EvidenceFactor[] => {
  const provenance = incident?.provenance ?? regime?.provenance;
  const topFactors = provenance?.top_factors ?? [];
  if (topFactors.length) {
    return topFactors.slice(0, 4).map((factor) => ({
      key: factor.factor,
      label: operatorSignalLabel(factor.factor, factor.label),
      value: formatEvidenceValue(factor.weighted_score ?? factor.score),
    }));
  }

  const components = Object.entries(provenance?.hazard_components ?? {})
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .sort(([, left], [, right]) => Number(right) - Number(left))
    .slice(0, 4);

  return components.map(([key, value]) => ({
    key,
    label: operatorSignalLabel(key),
    value: formatEvidenceValue(Number(value)),
  }));
};

const reasonEvidence = (
  regime: TransitRegimePayload | null,
  incident: TransitIncidentRecord | null,
): string[] => {
  const reasons = [...(incident?.reasons ?? []), ...(regime?.reasons ?? [])];
  return [...new Set(reasons)].slice(0, 5).map((reason) => operatorSignalLabel(reason));
};

export default function EvidenceDrawer({
  selectedCorridor,
  selectedCorridorId,
  corridorHistory,
  incidentResponse,
  regimeResponse,
}: EvidenceDrawerProps) {
  if (!selectedCorridorId) return null;

  const selectedRegime = latestRegimeForSelection(
    selectedCorridorId,
    corridorHistory,
    regimeResponse,
  );
  const selectedIncidents = incidentsForSelection(
    selectedCorridorId,
    corridorHistory,
    incidentResponse,
  );
  const topIncident = selectedIncidents[0] ?? null;
  const factors = factorEvidence(selectedRegime, topIncident);
  const reasons = reasonEvidence(selectedRegime, topIncident);
  const regime = topIncident?.regime ?? selectedRegime?.regime ?? selectedCorridor?.current_regime;
  const regimeLabel = operatorSignalLabel(
    regime,
    topIncident?.regime_label ??
      selectedRegime?.regime_label ??
      selectedCorridor?.current_regime_label ??
      formatRegimeLabel(regime),
  );
  const action = topIncident?.action ?? selectedRegime?.action;
  const actionLabel = formatActionLabel(
    action,
    topIncident?.action_label ?? selectedRegime?.action_label,
  );
  const regimeReasonSummary =
    selectedRegime?.reasons?.map((reason) => operatorSignalLabel(reason)).join(", ") ||
    null;
  const priorityScore =
    topIncident?.priority_score ??
    selectedRegime?.priority_score ??
    selectedCorridor?.priority_score;
  const priorityLabel = formatPriorityLabel(
    priorityScore,
    topIncident?.priority_label ??
      selectedRegime?.priority_label ??
      selectedCorridor?.priority_label,
  );
  const risk = topIncident?.hazard ?? selectedRegime?.hazard ?? selectedCorridor?.avg_hazard;
  const delay =
    selectedRegime?.metrics?.median_delay_seconds ??
    selectedCorridor?.median_delay_seconds;

  return (
    <section
      className="panel evidence-drawer"
      aria-labelledby="evidence-drawer-title"
      aria-live="polite"
    >
      <div className="evidence-drawer__layout">
        <div className="evidence-drawer__summary">
          <span className="section-eyebrow">Selected route evidence</span>
          <h2 id="evidence-drawer-title" className="evidence-drawer__title">
            {selectedCorridor?.label ?? selectedRegime?.label ?? selectedCorridorId}
          </h2>
          <p className="evidence-drawer__condition">{regimeLabel}</p>
          <div className="evidence-drawer__meta">
            <span>{priorityLabel} priority</span>
            <span>{formatRiskWithScore(risk)}</span>
            <span>Median delay: {formatDelaySignal(delay)}</span>
          </div>
        </div>

        <div className="evidence-drawer__body">
          <div className="evidence-drawer__section">
            <div>
              <span className="evidence-drawer__label">Why ranked here</span>
              <strong>{actionLabel}</strong>
            </div>
            <p>
              {topIncident?.summary ||
                regimeReasonSummary ||
                "The route is selected from the live priority queue."}
            </p>
          </div>

          <div className="evidence-metric-grid">
            <div className="evidence-metric">
              <span>Priority score</span>
              <strong>
                {typeof priorityScore === "number" && Number.isFinite(priorityScore)
                  ? Math.round(priorityScore)
                  : "n/a"}
              </strong>
            </div>
            <div className="evidence-metric">
              <span>Hazard</span>
              <strong>{formatHazard(risk)}</strong>
            </div>
            <div className="evidence-metric">
              <span>Confidence</span>
              <strong>{formatEvidenceValue(topIncident?.confidence ?? selectedRegime?.confidence)}</strong>
            </div>
            <div className="evidence-metric">
              <span>Incidents</span>
              <strong>{selectedIncidents.length}</strong>
            </div>
          </div>

          <div className="evidence-drawer__columns">
            <div className="evidence-drawer__section">
              <span className="evidence-drawer__label">Top signals</span>
              {factors.length ? (
                <div className="evidence-factor-list">
                  {factors.map((factor) => (
                    <div className="evidence-factor" key={factor.key}>
                      <span>{factor.label}</span>
                      <strong>{factor.value}</strong>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-state">No dominant signal published yet.</div>
              )}
            </div>

            <div className="evidence-drawer__section">
              <span className="evidence-drawer__label">Classifier evidence</span>
              {reasons.length ? (
                <div className="evidence-chip-list">
                  {reasons.map((reason) => (
                    <span className="chip chip--small" key={reason}>
                      {reason}
                    </span>
                  ))}
                </div>
              ) : (
                <p>{topFactorSummary(topIncident?.provenance ?? selectedRegime?.provenance)}</p>
              )}
              {topIncident?.timestamp_ms ? (
                <span className="evidence-drawer__timestamp">
                  Last incident {relativeTimeFromMs(topIncident.timestamp_ms)}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
