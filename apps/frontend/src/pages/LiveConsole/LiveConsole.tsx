import type { LineCard } from "../../types/transit";
import { useTransitData } from "../../hooks/useTransitData";
import { compareOperationalPriority } from "../../utils/formatters";
import CorridorMemoryPanel from "./panels/CorridorMemoryPanel";
import CorridorOverview from "./panels/CorridorOverview";
import HeroPanel from "./panels/HeroPanel";
import MapSection from "./panels/MapSection";
import OverviewMetrics from "./panels/OverviewMetrics";
import ScorecardPanel from "./panels/ScorecardPanel";
import ToolbarPanel from "./panels/ToolbarPanel";
import TrendPanel from "./panels/TrendPanel";
import ValueAddPanel from "./panels/ValueAddPanel";
import VehicleInventory from "./panels/VehicleInventory";
import "./LiveConsole.css";

/** Minimal display shape shared between LineCard and HistoryEntity. */
type CorridorDisplay = Pick<
  LineCard,
  | "entity_id"
  | "label"
  | "avg_hazard"
  | "median_delay_seconds"
  | "vehicle_count"
  | "activity_status"
  | "activity_status_label"
  | "activity_reason"
  | "activity_reason_label"
  | "current_regime"
  | "current_regime_label"
  | "priority_score"
  | "priority_label"
>;

export default function LiveConsole() {
  const data = useTransitData();

  const {
    serviceState,
    error,
    transitHealth,
    entities,
    regimeResponse,
    incidentResponse,
    trendResponse,
    sourceResponse,
    scorecardResponse,
    mapData,
    vehicleHistory,
    corridorHistory,
    scope,
    selectedTraceId,
    selectedCorridorId,
    selectedEntityId,
    setScope,
    setSelectedTraceId,
    setSelectedEntityId,
    selectCorridor,
    replayTraces,
  } = data;

  const activeLines = [...(entities.active_lines ?? entities.lines)].sort(
    compareOperationalPriority,
  );
  const scheduledLaterLines = [...(entities.scheduled_later_lines ?? [])].sort(
    compareOperationalPriority,
  );

  const selectedCorridorFromLine = entities.lines.find(
    (line) => line.entity_id === selectedCorridorId,
  ) ?? null;
  const selectedCorridorFromHistory: CorridorDisplay | null = corridorHistory.entity
    ? {
        entity_id: corridorHistory.entity.entity_id,
        label: corridorHistory.entity.label ?? "Unknown corridor",
        avg_hazard: corridorHistory.entity.avg_hazard ?? 0,
        median_delay_seconds: corridorHistory.entity.median_delay_seconds ?? 0,
        vehicle_count: corridorHistory.entity.vehicle_count ?? 0,
        activity_status: corridorHistory.entity.activity_status,
        activity_status_label: corridorHistory.entity.activity_status_label,
        activity_reason: corridorHistory.entity.activity_reason,
        activity_reason_label: corridorHistory.entity.activity_reason_label,
        current_regime: corridorHistory.entity.current_regime,
        current_regime_label: corridorHistory.entity.current_regime_label,
        priority_score: corridorHistory.entity.priority_score,
        priority_label: corridorHistory.entity.priority_label,
      }
    : null;
  const selectedCorridor: CorridorDisplay | null =
    selectedCorridorFromLine ?? selectedCorridorFromHistory;

  return (
    <main className="sentinel">
      <div className="sentinel__shell">
        <HeroPanel serviceState={serviceState} transitHealth={transitHealth} />

        <ValueAddPanel
          transitHealth={transitHealth}
          sourceResponse={sourceResponse}
          scorecardResponse={scorecardResponse}
        />

        <ToolbarPanel
          sourceResponse={sourceResponse}
          replayTraces={replayTraces}
          scope={scope}
          selectedTraceId={selectedTraceId}
          onScopeChange={setScope}
          onTraceChange={setSelectedTraceId}
        />

        {error ? (
          <section className="panel error-banner">
            <strong>Dashboard degraded.</strong> <code>{error}</code>
          </section>
        ) : null}

        <OverviewMetrics transitHealth={transitHealth} />

        <MapSection mapData={mapData} />

        <ScorecardPanel
          scorecardResponse={scorecardResponse}
          selectedCorridorId={selectedCorridorId}
          onSelectCorridor={(id) => selectCorridor(id)}
        />

        <CorridorOverview
          activeLines={activeLines}
          scheduledLaterLines={scheduledLaterLines}
          selectedCorridorId={selectedCorridorId}
          onSelectCorridor={(id) => selectCorridor(id)}
        />

        <TrendPanel
          trendResponse={trendResponse}
          selectedCorridorId={selectedCorridorId}
          onSelectCorridor={(id) => selectCorridor(id)}
        />

        <CorridorMemoryPanel
          selectedCorridor={selectedCorridor}
          selectedCorridorId={selectedCorridorId}
          corridorHistory={corridorHistory}
          incidentResponse={incidentResponse}
          regimeResponse={regimeResponse}
          entities={entities}
          selectedEntityId={selectedEntityId}
          onSelectCorridor={selectCorridor}
        />

        <VehicleInventory
          entities={entities}
          vehicleHistory={vehicleHistory}
          regimeResponse={regimeResponse}
          selectedEntityId={selectedEntityId}
          onSelectVehicle={(corridorEntityId, vehicleEntityId) =>
            selectCorridor(corridorEntityId, vehicleEntityId)
          }
          onSelectEntityDirect={setSelectedEntityId}
        />
      </div>
    </main>
  );
}
