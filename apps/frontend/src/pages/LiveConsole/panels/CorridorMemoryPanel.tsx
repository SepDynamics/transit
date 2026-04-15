import { useState } from "react";
import type {
  CorridorHistoryResponse,
  EntitiesResponse,
  IncidentResponse,
  IncidentAckPayload,
  RegimeResponse,
} from "../../../types/transit";
import { postJson } from "../../../utils/api";

/** Minimal display shape accepted from either a LineCard or HistoryEntity. */
interface CorridorDisplay {
  entity_id: string;
  label?: string;
  avg_hazard?: number;
  median_delay_seconds?: number;
  vehicle_count?: number;
  activity_status?: string;
  activity_status_label?: string;
  activity_reason?: string;
  activity_reason_label?: string;
  current_regime?: string | null;
  current_regime_label?: string | null;
  priority_score?: number;
  priority_label?: string;
}
import {
  actionTone,
  compareOperationalPriority,
  formatDelaySignal,
  formatHazard,
  formatActionLabel,
  formatActivityStatusLabel,
  formatPercent,
  formatPriorityLabel,
  formatRegimeLabel,
  formatRiskWithScore,
  priorityTone,
  relativeTimeFromMs,
  topFactorSummary,
} from "../../../utils/formatters";

interface CorridorMemoryPanelProps {
  selectedCorridor: CorridorDisplay | null;
  selectedCorridorId: string | null;
  corridorHistory: CorridorHistoryResponse;
  incidentResponse: IncidentResponse;
  regimeResponse: RegimeResponse;
  entities: EntitiesResponse;
  selectedEntityId: string | null;
  onSelectCorridor: (corridorEntityId: string, preferredVehicleId?: string | null) => void;
}

export default function CorridorMemoryPanel({
  selectedCorridor,
  selectedCorridorId,
  corridorHistory,
  incidentResponse,
  regimeResponse,
  entities,
  selectedEntityId,
  onSelectCorridor,
}: CorridorMemoryPanelProps) {
  const [acks, setAcks] = useState<Record<string, IncidentAckPayload>>({});
  const [ackPending, setAckPending] = useState<Record<string, boolean>>({});

  const handleAck = async (incidentId: string) => {
    if (acks[incidentId] || ackPending[incidentId]) return;
    setAckPending((prev) => ({ ...prev, [incidentId]: true }));
    try {
      const result = await postJson<IncidentAckPayload>(
        "/api/transit/incidents/ack",
        { incident_id: incidentId },
      );
      setAcks((prev) => ({ ...prev, [incidentId]: result }));
    } catch {
      // silently ignore - ack is best-effort from the UI side
    } finally {
      setAckPending((prev) => ({ ...prev, [incidentId]: false }));
    }
  };
  const corridorTimelinePoints = corridorHistory.regimes.slice(-36);
  const corridorIncidents = corridorHistory.incidents.slice(-3).reverse();
  const orderedIncidents = [...incidentResponse.incidents].sort(compareOperationalPriority);
  const vehiclesOnSelectedCorridor = selectedCorridorId
    ? entities.vehicles.filter((v) => v.corridor_entity_id === selectedCorridorId)
    : [];

  const selectedCorridorRegime =
    corridorHistory.regimes[corridorHistory.regimes.length - 1] ||
    regimeResponse.regimes.find((r) => r.entity_id === selectedCorridorId) ||
    null;

  return (
    <section className="section split-grid">
      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Selected route evidence</h2>
            <p className="section__hint">
              {selectedCorridor
                ? `${selectedCorridor.label ?? "Unknown route"} has ${vehiclesOnSelectedCorridor.length} visible vehicles in this view.`
                : "Select a route above."}
            </p>
          </div>
        </div>
        <div className="timeline">
          <div className="timeline__bars">
            {corridorTimelinePoints.map((point, index) => (
              <div
                key={`${point.signature ?? "point"}-${index}`}
                className={`timeline__bar timeline__bar--${actionTone(point.action)}`}
                style={{ height: `${12 + Math.max(0, Number(point.hazard ?? 0)) * 88}%` }}
                title={`${formatRegimeLabel(point.regime, point.regime_label)} ${formatHazard(
                  point.hazard,
                )}`}
              />
            ))}
          </div>
        </div>
        <div className="detail-grid">
          <div className="detail-card">
            <span>Service label</span>
            <strong>
              {formatRegimeLabel(
                selectedCorridorRegime?.regime ?? selectedCorridor?.current_regime,
                selectedCorridorRegime?.regime_label ??
                  selectedCorridor?.current_regime_label,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Risk</span>
            <strong>
              {formatRiskWithScore(
                selectedCorridorRegime?.hazard ?? selectedCorridor?.avg_hazard,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Median delay</span>
            <strong>
              {formatDelaySignal(
                selectedCorridorRegime?.metrics?.median_delay_seconds ??
                  selectedCorridor?.median_delay_seconds,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Recent problems</span>
            <strong>{corridorHistory.incidents.length}</strong>
          </div>
          <div className="detail-card">
            <span>Visible vehicles</span>
            <strong>
              {vehiclesOnSelectedCorridor.length || selectedCorridor?.vehicle_count || 0}
            </strong>
          </div>
          <div className="detail-card">
            <span>Priority</span>
            <strong>
              {formatPriorityLabel(
                selectedCorridorRegime?.priority_score ?? selectedCorridor?.priority_score,
                selectedCorridorRegime?.priority_label ?? selectedCorridor?.priority_label,
              )}
            </strong>
          </div>
        </div>
        <div className="corridor-vehicle-strip">
          {vehiclesOnSelectedCorridor.map((vehicle) => (
            <button
              key={vehicle.entity_id}
              type="button"
              className={
                vehicle.entity_id === selectedEntityId
                  ? "chip chip--small corridor-vehicle-chip is-active"
                  : "chip chip--small corridor-vehicle-chip"
              }
              onClick={() =>
                onSelectCorridor(vehicle.corridor_entity_id ?? "", vehicle.entity_id)
              }
              disabled={!vehicle.corridor_entity_id}
            >
              {vehicle.label}
            </button>
          ))}
              {!vehiclesOnSelectedCorridor.length ? (
            <div className="empty-state">
              No visible vehicles on this route in the selected view.
            </div>
          ) : null}
        </div>
        <div className="signature-list">
          {corridorIncidents.map((incident) => (
            <article key={incident.incident_id} className="signature-card">
              <div className="signature-card__header">
                <strong>{incident.label}</strong>
                <span>{relativeTimeFromMs(incident.timestamp_ms)}</span>
              </div>
              <p>{incident.summary}</p>
              <div className="signature-card__meta">
                <span>{formatRegimeLabel(incident.regime, incident.regime_label)}</span>
                <span>{formatActionLabel(incident.action, incident.action_label)}</span>
                <span>{formatPriorityLabel(incident.priority_score, incident.priority_label)}</span>
                <span>{formatPercent((incident.confidence ?? 0) * 100)} confidence</span>
              </div>
            </article>
          ))}
          {!corridorIncidents.length ? (
            <div className="empty-state">
              No saved problem history yet for this route.
            </div>
          ) : null}
        </div>
      </article>

      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Priority list</h2>
            <p className="section__hint">
              The current network problems, ordered by what should be handled first.
              {selectedCorridor
                ? ` Selected route: ${selectedCorridor.label ?? selectedCorridor.entity_id} (${formatActivityStatusLabel(
                    selectedCorridor.activity_status,
                    selectedCorridor.activity_status_label,
                  )}).`
                : ""}
            </p>
          </div>
        </div>
        <div className="incident-feed">
          {orderedIncidents.map((incident) => {
            const ack = acks[incident.incident_id];
            const pending = ackPending[incident.incident_id] ?? false;
            return (
              <article key={incident.incident_id} className={`incident-card${ack ? " incident-card--acked" : ""}`}>
                <div className="incident-card__header">
                  <strong>{incident.label}</strong>
                  <span
                    className={`badge badge--${priorityTone(
                      incident.priority_score,
                      incident.priority_label,
                    )}`}
                  >
                    {`${formatPriorityLabel(incident.priority_score, incident.priority_label)} • ${formatActionLabel(
                      incident.action,
                      incident.action_label,
                    )}`}
                  </span>
                </div>
                <p>{incident.summary}</p>
                <div className="incident-card__footer">{incident.recommended_action}</div>
                <div className="incident-card__meta">
                  <span>{formatRegimeLabel(incident.regime, incident.regime_label)}</span>
                  <span>{formatPercent((incident.confidence ?? 0) * 100)} confidence</span>
                  <span>{topFactorSummary(incident.provenance)}</span>
                </div>
                <div className="incident-card__ack">
                  {ack ? (
                    <span className="incident-ack-badge">Acknowledged</span>
                  ) : (
                    <button
                      type="button"
                      className="incident-ack-btn"
                      onClick={() => handleAck(incident.incident_id)}
                      disabled={pending}
                    >
                      {pending ? "Acknowledging..." : "Acknowledge"}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
          {!incidentResponse.incidents.length ? (
            <div className="empty-state">No active problems.</div>
          ) : null}
        </div>
      </article>
    </section>
  );
}
