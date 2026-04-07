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
}
import {
  actionTone,
  formatDelay,
  formatHazard,
  formatPercent,
  humanizeToken,
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
      // silently ignore — ack is best-effort from the UI side
    } finally {
      setAckPending((prev) => ({ ...prev, [incidentId]: false }));
    }
  };
  const corridorTimelinePoints = corridorHistory.regimes.slice(-36);
  const corridorIncidents = corridorHistory.incidents.slice(-3).reverse();
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
            <h2 className="section__title">Selected corridor memory</h2>
            <p className="section__hint">
              {selectedCorridor
                ? `${selectedCorridor.label ?? "Unknown corridor"} with ${vehiclesOnSelectedCorridor.length} visible vehicles in this scope`
                : "Select a corridor above"}
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
                title={`${point.regime ?? "unknown"} ${formatHazard(point.hazard)}`}
              />
            ))}
          </div>
        </div>
        <div className="detail-grid">
          <div className="detail-card">
            <span>Regime</span>
            <strong>
              {humanizeToken(
                selectedCorridorRegime?.regime ?? selectedCorridor?.activity_status,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Hazard</span>
            <strong>
              {formatHazard(
                selectedCorridorRegime?.hazard ?? selectedCorridor?.avg_hazard,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Median delay</span>
            <strong>
              {formatDelay(
                selectedCorridorRegime?.metrics?.median_delay_seconds ??
                  selectedCorridor?.median_delay_seconds,
              )}
            </strong>
          </div>
          <div className="detail-card">
            <span>Recent incidents</span>
            <strong>{corridorHistory.incidents.length}</strong>
          </div>
          <div className="detail-card">
            <span>Visible vehicles</span>
            <strong>
              {vehiclesOnSelectedCorridor.length || selectedCorridor?.vehicle_count || 0}
            </strong>
          </div>
          <div className="detail-card">
            <span>Activity</span>
            <strong>{humanizeToken(selectedCorridor?.activity_status)}</strong>
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
              No visible vehicles on this corridor in the selected scope.
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
                <span>{humanizeToken(incident.regime)}</span>
                <span>{humanizeToken(incident.action)}</span>
                <span>{formatPercent((incident.confidence ?? 0) * 100)} confidence</span>
              </div>
            </article>
          ))}
          {!corridorIncidents.length ? (
            <div className="empty-state">
              No persisted incident memory yet for this corridor.
            </div>
          ) : null}
        </div>
      </article>

      <article className="panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Incident feed</h2>
            <p className="section__hint">
              Current operator actions across the network.
              {selectedCorridor
                ? ` Selected corridor: ${selectedCorridor.label ?? selectedCorridor.entity_id}.`
                : ""}
            </p>
          </div>
        </div>
        <div className="incident-feed">
          {incidentResponse.incidents.map((incident) => {
            const ack = acks[incident.incident_id];
            const pending = ackPending[incident.incident_id] ?? false;
            return (
              <article key={incident.incident_id} className={`incident-card${ack ? " incident-card--acked" : ""}`}>
                <div className="incident-card__header">
                  <strong>{incident.label}</strong>
                  <span className={`badge badge--${actionTone(incident.action)}`}>
                    {humanizeToken(incident.action)}
                  </span>
                </div>
                <p>{incident.summary}</p>
                <div className="incident-card__footer">{incident.recommended_action}</div>
                <div className="incident-card__meta">
                  <span>{humanizeToken(incident.regime)}</span>
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
                      {pending ? "Acknowledging…" : "Acknowledge"}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
          {!incidentResponse.incidents.length ? (
            <div className="empty-state">No active incidents.</div>
          ) : null}
        </div>
      </article>
    </section>
  );
}
