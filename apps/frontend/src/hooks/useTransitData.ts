/**
 * Central polling hook for the Transit Sentinel operations console.
 * Encapsulates all API calls and state; returns stable typed data to consumers.
 */
import { useEffect, useState } from "react";
import type {
  CorridorHistoryResponse,
  EntitiesResponse,
  IncidentResponse,
  RegimeResponse,
  ScorecardResponse,
  SourceResponse,
  TransitHealth,
  TransitMapResponse,
  TransitReplayTrace,
  TrendResponse,
  VehicleHistoryResponse,
} from "../types/transit";
import { buildTransitQuery, fetchJson } from "../utils/api";
import type { ServiceState } from "../utils/formatters";

const replayTraces = (payload: SourceResponse): TransitReplayTrace[] =>
  payload.traces?.length
    ? payload.traces
    : (payload.trace_ids ?? []).map((trace_id) => ({ trace_id }));

export interface TransitDataState {
  serviceState: ServiceState;
  error: string | null;
  transitHealth: TransitHealth | null;
  entities: EntitiesResponse;
  regimeResponse: RegimeResponse;
  incidentResponse: IncidentResponse;
  trendResponse: TrendResponse;
  sourceResponse: SourceResponse;
  scorecardResponse: ScorecardResponse | null;
  mapData: TransitMapResponse | null;
  vehicleHistory: VehicleHistoryResponse;
  corridorHistory: CorridorHistoryResponse;
  scope: string;
  selectedTraceId: string;
  selectedCorridorId: string | null;
  selectedEntityId: string | null;
  setScope: (scope: string) => void;
  setSelectedTraceId: (id: string) => void;
  setSelectedCorridorId: (id: string | null) => void;
  setSelectedEntityId: (id: string | null) => void;
  selectCorridor: (corridorEntityId: string, preferredVehicleId?: string | null) => void;
  replayTraces: TransitReplayTrace[];
}

export function useTransitData(): TransitDataState {
  const [serviceState, setServiceState] = useState<ServiceState>("loading");
  const [transitHealth, setTransitHealth] = useState<TransitHealth | null>(null);
  const [entities, setEntities] = useState<EntitiesResponse>({
    lines: [],
    active_lines: [],
    scheduled_later_lines: [],
    inactive_lines: [],
    vehicles: [],
  });
  const [regimeResponse, setRegimeResponse] = useState<RegimeResponse>({
    regimes: [],
    recurring_regimes: [],
  });
  const [incidentResponse, setIncidentResponse] = useState<IncidentResponse>({
    incidents: [],
  });
  const [trendResponse, setTrendResponse] = useState<TrendResponse>({
    summary: {
      corridor_count: 0,
      unstable_corridor_count: 0,
      recent_incident_count: 0,
      recent_action_counts: {},
      recent_regime_counts: {},
    },
    corridors: [],
  });
  const [sourceResponse, setSourceResponse] = useState<SourceResponse>({
    scopes: [
      { id: "live", label: "Live feed" },
    ],
    traces: [],
  });
  const [scope, setScope] = useState("live");
  const [selectedTraceId, setSelectedTraceId] = useState("");
  const [selectedCorridorId, setSelectedCorridorId] = useState<string | null>(null);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [vehicleHistory, setVehicleHistory] = useState<VehicleHistoryResponse>({
    observations: [],
    regimes: [],
    incidents: [],
  });
  const [corridorHistory, setCorridorHistory] = useState<CorridorHistoryResponse>({
    observations: [],
    regimes: [],
    incidents: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [mapData, setMapData] = useState<TransitMapResponse | null>(null);
  const [scorecardResponse, setScorecardResponse] = useState<ScorecardResponse | null>(null);

  // Poll available sources / replay traces every 10 s
  useEffect(() => {
    let active = true;
    const loadSources = async () => {
      try {
        const payload = await fetchJson<SourceResponse>("/api/transit/sources");
        if (!active) return;
        setSourceResponse(payload);
      } catch {
        if (!active) return;
      }
    };
    loadSources();
    const timer = window.setInterval(loadSources, 10_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  // Keep selectedTraceId coherent when scope or traces change
  useEffect(() => {
    const traceIds = replayTraces(sourceResponse)
      .map((t) => t.trace_id)
      .filter(Boolean);
    if (scope === "live") {
      if (selectedTraceId) setSelectedTraceId("");
      return;
    }
    if (selectedTraceId && !traceIds.includes(selectedTraceId)) {
      setSelectedTraceId("");
    }
  }, [scope, selectedTraceId, sourceResponse.trace_ids, sourceResponse.traces]);

  // Main dashboard poll - health, entities, regimes, incidents, trends (5 s)
  useEffect(() => {
    let active = true;
    const loadDashboard = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const [healthPayload, entitiesPayload, regimePayload, incidentPayload, trendPayload] =
          await Promise.all([
            fetchJson<TransitHealth>(`/api/transit/health?${query}`),
            fetchJson<EntitiesResponse>(`/api/transit/entities?${query}`),
            fetchJson<RegimeResponse>(`/api/transit/regimes?${query}`),
            fetchJson<IncidentResponse>(`/api/transit/incidents?${query}`),
            fetchJson<TrendResponse>(`/api/transit/trends?${query}`),
          ]);
        if (!active) return;
        setTransitHealth(healthPayload);
        setEntities(entitiesPayload);
        setRegimeResponse(regimePayload);
        setIncidentResponse(incidentPayload);
        setTrendResponse(trendPayload);
        setServiceState("online");
        setError(null);
      } catch (loadError) {
        if (!active) return;
        setServiceState("offline");
        setError(
          loadError instanceof Error ? loadError.message : "transit api unavailable",
        );
      }
    };
    loadDashboard();
    const timer = window.setInterval(loadDashboard, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  // Scorecard poll - 10 s is sufficient
  useEffect(() => {
    let active = true;
    const loadScorecard = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<ScorecardResponse>(
          `/api/transit/scorecard?${query}&limit=720`,
        );
        if (!active) return;
        setScorecardResponse(payload);
      } catch {
        if (!active) return;
      }
    };
    loadScorecard();
    const timer = window.setInterval(loadScorecard, 10_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  // Map data poll - 5 s, best-effort (failures don't degrade main dashboard)
  useEffect(() => {
    let active = true;
    const loadMap = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<TransitMapResponse>(`/api/transit/map?${query}`);
        if (!active) return;
        setMapData(payload);
      } catch {
        if (!active) return;
      }
    };
    loadMap();
    const timer = window.setInterval(loadMap, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId]);

  // Auto-select first corridor when entity list changes
  useEffect(() => {
    if (!entities.lines.length) {
      setSelectedCorridorId(null);
      return;
    }
    const selectionExists = entities.lines.some(
      (line) => line.entity_id === selectedCorridorId,
    );
    if (!selectionExists) {
      setSelectedCorridorId(
        entities.active_lines?.[0]?.entity_id ?? entities.lines[0].entity_id,
      );
    }
  }, [entities.active_lines, entities.lines, selectedCorridorId]);

  // Auto-select first vehicle when vehicle list changes
  useEffect(() => {
    if (!entities.vehicles.length) {
      setSelectedEntityId(null);
      return;
    }
    const selectionExists = entities.vehicles.some(
      (v) => v.entity_id === selectedEntityId,
    );
    if (!selectionExists) {
      setSelectedEntityId(entities.vehicles[0].entity_id);
    }
  }, [entities.vehicles, selectedEntityId]);

  // Vehicle history poll
  useEffect(() => {
    if (!selectedEntityId) {
      setVehicleHistory({ observations: [], regimes: [], incidents: [] });
      return;
    }
    let active = true;
    const loadVehicleHistory = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<VehicleHistoryResponse>(
          `/api/transit/history?${query}&entity_id=${encodeURIComponent(selectedEntityId)}&limit=72`,
        );
        if (!active) return;
        setVehicleHistory(payload);
      } catch {
        if (!active) return;
      }
    };
    loadVehicleHistory();
    const timer = window.setInterval(loadVehicleHistory, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedTraceId, selectedEntityId]);

  // Corridor history poll
  useEffect(() => {
    if (!selectedCorridorId) {
      setCorridorHistory({ observations: [], regimes: [], incidents: [] });
      return;
    }
    let active = true;
    const loadCorridorHistory = async () => {
      const query = buildTransitQuery(scope, selectedTraceId || undefined);
      try {
        const payload = await fetchJson<CorridorHistoryResponse>(
          `/api/transit/history?${query}&entity_id=${encodeURIComponent(selectedCorridorId)}&limit=72`,
        );
        if (!active) return;
        setCorridorHistory(payload);
      } catch {
        if (!active) return;
      }
    };
    loadCorridorHistory();
    const timer = window.setInterval(loadCorridorHistory, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [scope, selectedCorridorId, selectedTraceId]);

  const selectCorridor = (
    corridorEntityId: string,
    preferredVehicleId?: string | null,
  ) => {
    if (!corridorEntityId) {
      if (preferredVehicleId) setSelectedEntityId(preferredVehicleId);
      return;
    }
    setSelectedCorridorId(corridorEntityId);
    if (preferredVehicleId) {
      setSelectedEntityId(preferredVehicleId);
      return;
    }
    const selectedVehicle = entities.vehicles.find(
      (v) => v.entity_id === selectedEntityId,
    );
    if (selectedVehicle?.corridor_entity_id === corridorEntityId) return;
    const vehicle = entities.vehicles.find(
      (v) => v.corridor_entity_id === corridorEntityId,
    );
    if (vehicle) setSelectedEntityId(vehicle.entity_id);
  };

  return {
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
    setSelectedCorridorId,
    setSelectedEntityId,
    selectCorridor,
    replayTraces: replayTraces(sourceResponse),
  };
}
