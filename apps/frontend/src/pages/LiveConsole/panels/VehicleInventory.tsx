import type {
  EntitiesResponse,
  RegimeResponse,
  VehicleHistoryResponse,
} from "../../../types/transit";
import {
  actionTone,
  formatDelay,
  formatPercent,
  formatActionLabel,
  formatRegimeLabel,
  formatRiskWithScore,
  humanizeToken,
  topFactorSummary,
} from "../../../utils/formatters";

interface VehicleInventoryProps {
  entities: EntitiesResponse;
  vehicleHistory: VehicleHistoryResponse;
  regimeResponse: RegimeResponse;
  selectedEntityId: string | null;
  onSelectVehicle: (corridorEntityId: string, vehicleEntityId: string) => void;
  onSelectEntityDirect: (entityId: string) => void;
}

export default function VehicleInventory({
  entities,
  vehicleHistory,
  regimeResponse,
  selectedEntityId,
  onSelectVehicle,
  onSelectEntityDirect,
}: VehicleInventoryProps) {
  const selectedVehicle =
    entities.vehicles.find((v) => v.entity_id === selectedEntityId) || null;
  const selectedRegime = selectedVehicle?.regime ?? null;
  const observationPoints = vehicleHistory.observations.slice(-12);

  return (
    <>
      <section className="section panel">
        <div className="section__header">
          <div>
            <h2 className="section__title">Vehicles currently visible</h2>
            <p className="section__hint">
              Each vehicle from the public feed, with its route, delay, and current route health.
            </p>
          </div>
        </div>
        <div className="vehicle-grid">
          {entities.vehicles.map((vehicle) => {
            const active = vehicle.entity_id === selectedEntityId;
            return (
              <button
                key={vehicle.entity_id}
                type="button"
                className={active ? "vehicle-card is-active" : "vehicle-card"}
                onClick={() => {
                  if (vehicle.corridor_entity_id) {
                    onSelectVehicle(vehicle.corridor_entity_id, vehicle.entity_id);
                    return;
                  }
                  onSelectEntityDirect(vehicle.entity_id);
                }}
              >
                <div className="vehicle-card__header">
                  <div>
                    <strong>{vehicle.label}</strong>
                    <span>{vehicle.route_label ?? "Unknown route"}</span>
                  </div>
                  <span className={`badge badge--${actionTone(vehicle.regime?.action)}`}>
                    {formatActionLabel(
                      vehicle.regime?.action,
                      vehicle.regime?.action_label,
                    )}
                  </span>
                </div>
                <div className="vehicle-card__metrics">
                  <div>
                    <span>Delay</span>
                    <strong>{formatDelay(vehicle.delay_seconds)}</strong>
                  </div>
                  <div>
                    <span>Status</span>
                    <strong>{humanizeToken(vehicle.status)}</strong>
                  </div>
                  <div>
                    <span>Occupancy</span>
                    <strong>{humanizeToken(vehicle.occupancy_status)}</strong>
                  </div>
                  <div>
                    <span>Risk</span>
                    <strong>{formatRiskWithScore(vehicle.regime?.hazard)}</strong>
                  </div>
                </div>
                <div className="vehicle-card__footer">
                  <span>
                    {formatRegimeLabel(
                      vehicle.regime?.regime ?? "healthy",
                      vehicle.regime?.regime_label,
                    )}
                  </span>
                  <span>{vehicle.collection_source ?? vehicle.source ?? "unknown source"}</span>
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="section split-grid">
        <article className="panel">
          <div className="section__header">
            <div>
              <h2 className="section__title">Selected vehicle details</h2>
              <p className="section__hint">The latest public-feed facts for this vehicle.</p>
            </div>
          </div>
          <div className="detail-grid detail-grid--expanded">
            <div className="detail-card">
              <span>Route</span>
              <strong>{selectedVehicle?.route_label ?? "n/a"}</strong>
            </div>
            <div className="detail-card">
              <span>Trip</span>
              <strong>{selectedVehicle?.trip_id ?? "n/a"}</strong>
            </div>
            <div className="detail-card">
              <span>Next stop</span>
              <strong>{selectedVehicle?.stop_id ?? "n/a"}</strong>
            </div>
            <div className="detail-card">
              <span>Feed age</span>
              <strong>{formatDelay(selectedRegime?.metrics?.feed_age_seconds)}</strong>
            </div>
            <div className="detail-card">
              <span>Signal confidence</span>
              <strong>{formatPercent((selectedRegime?.confidence ?? 0) * 100)}</strong>
            </div>
            <div className="detail-card">
              <span>Top factors</span>
              <strong>{topFactorSummary(selectedRegime?.provenance)}</strong>
            </div>
          </div>
          <div className="micro-strip">
            {observationPoints.map((point, index) => (
              <span
                key={`${point.timestamp_ms ?? index}`}
                className="micro-strip__cell"
                style={{ height: `${16 + Math.min(84, Math.abs(point.delay_seconds ?? 0) / 6)}%` }}
              />
            ))}
          </div>
        </article>
        <article className="panel">
          <div className="section__header">
            <div>
              <h2 className="section__title">Recurring signatures</h2>
              <p className="section__hint">
                Repeated service patterns found in the current check.
              </p>
            </div>
          </div>
          <div className="signature-list">
            {regimeResponse.recurring_regimes.map((signature) => (
              <article key={signature.signature} className="signature-card">
                <div className="signature-card__header">
                  <strong>{signature.signature}</strong>
                  <span>{signature.entity_count} routes</span>
                </div>
                <p>
                  {signature.regimes.map((regime) => formatRegimeLabel(regime)).join(", ")} •{" "}
                  {signature.actions.map((action) => formatActionLabel(action)).join(", ")}
                </p>
              </article>
            ))}
            {!regimeResponse.recurring_regimes.length ? (
              <div className="empty-state">No repeated patterns yet.</div>
            ) : null}
          </div>
        </article>
      </section>
    </>
  );
}
