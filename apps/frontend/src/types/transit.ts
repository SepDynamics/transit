export interface TransitFeedStatus {
  feed_label?: string;
  updated_at?: string | null;
  agency_key?: string | null;
  vehicle_count?: number;
  trip_update_count?: number;
  alert_count?: number;
  collection_source?: string;
  status?: string;
}

export interface TransitEventOverlay {
  overlay_id: string;
  label?: string;
  event_key?: string | null;
  event_name?: string | null;
  city_key?: string | null;
  category?: string | null;
  agency_keys?: string[];
  route_ids?: string[];
  corridor_ids?: string[];
  starts_at?: string | null;
  ends_at?: string | null;
  note?: string | null;
  source?: string | null;
}

export interface ProvenanceFactor {
  factor: string;
  label?: string;
  score?: number;
  weight?: number;
  weighted_score?: number;
}

export interface ProvenancePayload {
  feature_coverage?: number;
  signal_agreement?: number;
  feed_freshness?: number;
  metrics?: {
    position_coverage?: number;
    trip_update_coverage?: number;
    feed_age_seconds?: number;
  };
  hazard_components?: Record<string, number>;
  top_factors?: ProvenanceFactor[];
}

export interface TransitRegimeMetrics {
  vehicle_count?: number;
  trip_update_count?: number;
  active_alert_count?: number;
  avg_delay_seconds?: number;
  median_delay_seconds?: number;
  p90_delay_seconds?: number;
  delay_spread_seconds?: number;
  scheduled_headway_seconds?: number | null;
  compressed_headway_share?: number;
  terminal_backlog_count?: number;
  dwell_overrun_share?: number;
  position_coverage?: number;
  trip_update_coverage?: number;
  feed_age_seconds?: number;
}

export interface TransitRegimePayload {
  timestamp_ms?: number;
  entity_id: string;
  entity_type?: string;
  label?: string;
  agency_key?: string | null;
  corridor_id?: string | null;
  route_id?: string | null;
  regime?: string;
  regime_label?: string;
  hazard?: number;
  action?: string;
  action_label?: string;
  scoring_backend?: string;
  confidence?: number;
  priority_score?: number;
  priority_label?: string;
  signature?: string;
  reasons?: string[];
  provenance?: ProvenancePayload;
  metrics?: TransitRegimeMetrics;
  source?: string;
  collection_source?: string;
  trace_id?: string | null;
  event_overlays?: TransitEventOverlay[];
}

export interface TransitHealthResponse {
  system_name?: string;
  generated_at?: string;
  status?: string;
  line_count?: number;
  active_line_count?: number;
  scheduled_later_line_count?: number;
  inactive_line_count?: number;
  visible_line_count?: number;
  vehicle_count?: number;
  incident_count?: number;
  critical_incidents?: number;
  avg_hazard?: number;
  avg_confidence?: number;
  max_hazard?: number;
  action_counts?: Record<string, number>;
  regime_counts?: Record<string, number>;
  feed_status?: TransitFeedStatus;
  worst_corridor?: TransitRegimePayload | null;
}

export interface TransitCorridorSnapshot {
  entity_id: string;
  agency_key?: string | null;
  corridor_id?: string | null;
  route_id?: string | null;
  direction_id?: number | null;
  label: string;
  geometry?: {
    type: "LineString";
    coordinates: [number, number][];
  } | null;
  vehicle_count: number;
  median_delay_seconds: number;
  scheduled_headway_seconds?: number | null;
  compressed_headway_share?: number;
  avg_delay_seconds: number;
  top_action: string;
  top_action_label?: string;
  avg_hazard: number;
  active_alert_count: number;
  current_regime?: string | null;
  current_regime_label?: string | null;
  priority_score?: number;
  priority_label?: string;
  activity_status?: string;
  activity_status_label?: string;
  activity_reason?: string;
  activity_reason_label?: string;
  route_mode?: string | null;
  source?: string;
  collection_source?: string;
  trace_id?: string | null;
  timestamp_ms?: number;
  event_overlays?: TransitEventOverlay[];
}

export interface ObservationPayload {
  timestamp_ms?: number;
  route_id?: string | null;
  trip_id?: string | null;
  vehicle_id?: string;
  vehicle_label?: string | null;
  stop_id?: string | null;
  current_status?: string | null;
  occupancy_status?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  delay_seconds?: number | null;
  source?: string;
  collection_source?: string;
  trace_id?: string | null;
}

export interface TransitVehicleSnapshot {
  entity_id: string;
  label: string;
  vehicle_id: string;
  corridor_entity_id?: string | null;
  agency_key?: string | null;
  corridor_id?: string | null;
  route_id?: string | null;
  route_label?: string;
  trip_id?: string | null;
  direction_id?: number | null;
  stop_id?: string | null;
  status?: string | null;
  delay_seconds?: number | null;
  occupancy_status?: string | null;
  source?: string;
  collection_source?: string;
  regime?: TransitRegimePayload | null;
  observation?: ObservationPayload | null;
  event_overlays?: TransitEventOverlay[];
}

export interface TransitEntitiesResponse {
  generated_at?: string;
  agency_key?: string | null;
  lines: TransitCorridorSnapshot[];
  active_lines?: TransitCorridorSnapshot[];
  scheduled_later_lines?: TransitCorridorSnapshot[];
  inactive_lines?: TransitCorridorSnapshot[];
  vehicles: TransitVehicleSnapshot[];
  event_overlays?: TransitEventOverlay[];
}

export interface TransitIncidentRecord {
  incident_id: string;
  timestamp_ms?: number;
  entity_id: string;
  entity_type?: string;
  label: string;
  agency_key?: string | null;
  corridor_id?: string | null;
  route_id?: string | null;
  severity: string;
  action: string;
  action_label?: string;
  regime: string;
  regime_label?: string;
  hazard: number;
  confidence?: number;
  priority_score?: number;
  priority_label?: string;
  provenance?: ProvenancePayload;
  summary: string;
  recommended_action: string;
  reasons?: string[];
  source?: string;
  trace_id?: string | null;
  event_overlays?: TransitEventOverlay[];
}

export interface TransitIncidentResponse {
  generated_at?: string;
  incidents: TransitIncidentRecord[];
}

export interface SignaturePayload {
  signature: string;
  entity_count: number;
  hazard_max: number;
  regimes: string[];
  actions: string[];
}

export interface TransitRegimeResponse {
  generated_at?: string;
  regimes: TransitRegimePayload[];
  recurring_regimes: SignaturePayload[];
}

export interface SourceOption {
  id: string;
  label: string;
}

export interface TransitReplayTrace {
  trace_id: string;
  snapshot_count?: number;
  first_snapshot_path?: string | null;
  latest_snapshot_path?: string | null;
  latest_snapshot_timestamp_ms?: number | null;
  updated_at?: string | null;
  system_name?: string | null;
}

export interface TransitSourceResponse {
  scopes: SourceOption[];
  agency_key?: string | null;
  available?: {
    live?: boolean;
    replay?: boolean;
  };
  configured_feeds?: Record<string, boolean>;
  traces?: TransitReplayTrace[];
  trace_ids?: string[];
}

export interface CorridorObservation {
  timestamp_ms?: number;
  entity_id: string;
  route_id?: string | null;
  direction_id?: number | null;
  label?: string;
  vehicle_count?: number;
  median_delay_seconds?: number;
  scheduled_headway_seconds?: number | null;
  avg_delay_seconds?: number;
  top_action?: string;
  top_action_label?: string;
  avg_hazard?: number;
  active_alert_count?: number;
  current_regime?: string | null;
  current_regime_label?: string | null;
  priority_score?: number;
  priority_label?: string;
  activity_status?: string;
  activity_status_label?: string;
  activity_reason?: string;
  activity_reason_label?: string;
  source?: string;
  collection_source?: string;
  trace_id?: string | null;
}

export interface HistoryEntity {
  entity_id: string;
  label?: string;
  vehicle_count?: number;
  median_delay_seconds?: number;
  avg_hazard?: number;
  top_action?: string;
  top_action_label?: string;
  current_regime?: string | null;
  current_regime_label?: string | null;
  priority_score?: number;
  priority_label?: string;
  activity_status?: string;
  activity_status_label?: string;
  activity_reason?: string;
  activity_reason_label?: string;
}

export interface TransitVehicleHistoryResponse {
  entity?: HistoryEntity | null;
  observations: ObservationPayload[];
  regimes: TransitRegimePayload[];
  incidents: TransitIncidentRecord[];
}

export interface TransitCorridorHistoryResponse {
  entity?: HistoryEntity | null;
  observations: CorridorObservation[];
  regimes: TransitRegimePayload[];
  incidents: TransitIncidentRecord[];
}

export interface CorridorTrend {
  entity_id: string;
  label: string;
  route_id?: string | null;
  snapshot_count: number;
  incident_count: number;
  avg_hazard: number;
  max_hazard: number;
  latest_hazard: number;
  latest_action: string;
  latest_regime: string;
  latest_delay_seconds?: number | null;
  latest_activity_status?: string;
  hazard_series: number[];
  delay_series: number[];
  recent_actions: string[];
}

export interface TrendSummary {
  corridor_count: number;
  unstable_corridor_count: number;
  recent_incident_count: number;
  recent_action_counts: Record<string, number>;
  recent_regime_counts: Record<string, number>;
}

export interface TransitTrendResponse {
  generated_at?: string;
  summary: TrendSummary;
  corridors: CorridorTrend[];
}

export interface TransitDashboardResponse {
  generated_at?: string;
  scope?: string;
  trace_id?: string | null;
  health: TransitHealthResponse;
  entities: TransitEntitiesResponse;
  regimes: TransitRegimeResponse;
  incidents: TransitIncidentResponse;
  trends: TransitTrendResponse;
}

export type TransitHealth = TransitHealthResponse;
export type LineCard = TransitCorridorSnapshot;
export type VehicleCard = TransitVehicleSnapshot;
export type EntitiesResponse = TransitEntitiesResponse;
export type IncidentPayload = TransitIncidentRecord;
export type IncidentResponse = TransitIncidentResponse;
export type RegimeResponse = TransitRegimeResponse;
export type SourceResponse = TransitSourceResponse;
export type VehicleHistoryResponse = TransitVehicleHistoryResponse;
export type CorridorHistoryResponse = TransitCorridorHistoryResponse;
export type TrendResponse = TransitTrendResponse;
export type DashboardResponse = TransitDashboardResponse;

// ---------------------------------------------------------------------------
// Protected alternative-service operator preview
// ---------------------------------------------------------------------------

export interface AdvisoryProductBoundary {
  advisory_only: boolean;
  infers_cause: boolean;
  guarantees_arrival: boolean;
  issues_dispatch_instructions: boolean;
  statement: string;
}

export interface AdvisoryStopOption {
  stop_id: string;
  stop_name: string;
  sequence: number;
  downstream_stop_ids: string[];
}

export interface AdvisoryDirectionOption {
  direction_id: number | null;
  label: string;
}

export interface AdvisoryOptionsResponse {
  status: "available" | "selection_required" | "unavailable";
  generated_at_ms: number;
  release_stage: "operator_preview";
  disrupted_route_id: string;
  route_label: string | null;
  resolved_direction_id: number | null;
  directions: AdvisoryDirectionOption[];
  stops: AdvisoryStopOption[];
  suppression_reasons: string[];
  product_boundary: AdvisoryProductBoundary;
}

export interface AdvisoryLeg {
  kind: "ride" | "walk" | "transfer";
  from_stop_id: string;
  to_stop_id: string;
  departure_time_ms: number | null;
  arrival_time_ms: number | null;
  duration_seconds: number;
  route_id: string | null;
  trip_id: string | null;
  direction_id: number | null;
  realtime_coverage: number | null;
  transfer_source: string | null;
}

export interface AdvisoryEvidence {
  kind: string;
  details: Record<string, unknown>;
}

export interface AlternativeAdvisory {
  disrupted_route_id: string;
  origin_stop_id: string;
  destination_stop_id: string;
  route_ids: string[];
  estimated_arrival_time_ms: number;
  baseline_arrival_time_ms: number;
  expected_time_saved_seconds: number;
  total_walking_seconds: number;
  total_walking_meters: number | null;
  total_transfer_seconds: number;
  confidence: number;
  confidence_label: "low" | "medium" | "high";
  expires_at_ms: number;
  summary: string;
  explanation: string;
  legs: AdvisoryLeg[];
  evidence: AdvisoryEvidence[];
}

export interface AlternativeAdvisoryResponse {
  status: "published" | "suppressed" | "unavailable";
  generated_at_ms: number;
  origin_stop_id: string;
  destination_stop_id: string;
  disrupted_route_id: string;
  advisories: AlternativeAdvisory[];
  suppression_reasons: string[];
  evaluated_candidate_count: number;
  baseline_arrival_time_ms: number | null;
  release_stage: "operator_preview";
  resolved_direction_id: number | null;
  product_boundary: AdvisoryProductBoundary;
}

export interface AdvisoryApiErrorResponse {
  status: "invalid_request";
  error: string;
  message: string;
  release_stage: "operator_preview";
  product_boundary: AdvisoryProductBoundary;
  required_role?: string;
  authentication_required?: boolean;
  missing_parameters?: string[];
}

// ---------------------------------------------------------------------------
// Map endpoint types (/api/transit/map)
// ---------------------------------------------------------------------------

export interface VehicleMapFeatureProperties {
  entity_id?: string | null;
  vehicle_id?: string | null;
  route_id?: string | null;
  route_label?: string | null;
  direction_id?: number | null;
  corridor_entity_id?: string | null;
  corridor_id?: string | null;
  delay_seconds?: number | null;
  current_status?: string | null;
  occupancy_status?: string | null;
  bearing?: number | null;
  hazard_score?: number | null;
  regime?: string | null;
  label?: string | null;
  timestamp_ms?: number | null;
}

export interface VehicleMapFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: VehicleMapFeatureProperties;
}

export interface CorridorMapSummary {
  entity_id: string;
  route_id?: string | null;
  direction_id?: number | null;
  corridor_id?: string | null;
  route_mode?: string | null;
  activity_status?: string | null;
  label?: string | null;
  regime?: string | null;
  hazard_score?: number | null;
  active_vehicles?: number | null;
  incident_count?: number;
  top_action?: string | null;
  timestamp_ms?: number | null;
}

export interface CorridorMapFeatureProperties extends CorridorMapSummary {
  _color?: string;
}

export interface CorridorMapFeature {
  type: "Feature";
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: CorridorMapFeatureProperties;
}

export interface TransitMapResponse {
  type: "FeatureCollection";
  scope?: string;
  trace_id?: string | null;
  timestamp?: string;
  vehicle_features?: VehicleMapFeature[];
  corridor_features?: CorridorMapFeature[];
  corridor_summaries?: CorridorMapSummary[];
  vehicle_count: number;
  corridor_count: number;
}

export interface TransitScorecardNetwork {
  avg_hazard: number;
  avg_delay_seconds: number;
  on_time_pct: number;
  healthy_pct?: number;
  unstable_pct?: number;
  unstable_corridor_count: number;
  top_regimes: Record<string, number>;
  top_actions: Record<string, number>;
}

export interface TransitScorecardCorridor {
  entity_id: string;
  label: string;
  route_id?: string | null;
  snapshot_count: number;
  incident_count: number;
  avg_hazard: number;
  max_hazard: number;
  hazard_p90: number;
  avg_delay_seconds: number;
  max_delay_seconds: number;
  on_time_pct: number;
  healthy_pct: number;
  unstable_pct: number;
  top_regime: string;
  top_action: string;
  regime_counts: Record<string, number>;
  action_counts: Record<string, number>;
}

export interface TransitScorecardResponse {
  generated_at?: string;
  scope?: string;
  trace_id?: string | null;
  window_snapshots: number;
  corridor_count: number;
  total_incidents: number;
  network: TransitScorecardNetwork;
  corridors: TransitScorecardCorridor[];
}

export type ScorecardResponse = TransitScorecardResponse;

// ---------------------------------------------------------------------------
// Public service-status API types (/api/status/*)
// ---------------------------------------------------------------------------

export interface RouteStatus {
  entity_id: string;
  route_id?: string | null;
  direction_id?: number | null;
  label: string;
  severity: "good" | "advisory" | "delay" | "disruption" | "severe" | "unknown";
  severity_label: string;
  severity_color: string;
  headline: string;
  body: string;
  short_summary: string;
  hazard_score?: number | null;
  regime?: string | null;
  action?: string | null;
  active_alert_count: number;
  median_delay_seconds?: number | null;
  agency_key?: string | null;
  timestamp_ms?: number | null;
  advisories: string[];
}

export interface PublicStatusRoutesResponse {
  generated_at?: string;
  scope?: string;
  route_count: number;
  routes: RouteStatus[];
}

export interface DisruptedRoute {
  entity_id?: string;
  label?: string;
  severity?: string;
}

export interface PublicStatusNetworkResponse {
  generated_at?: string;
  scope?: string;
  severity: string;
  severity_label: string;
  severity_color: string;
  active_route_count: number;
  incident_count: number;
  critical_incident_count: number;
  disrupted_route_count: number;
  disrupted_routes: DisruptedRoute[];
  feed_status?: TransitFeedStatus;
}

export interface PublicFeedQualityCheck {
  check_id: string;
  label: string;
  status: RouteStatus["severity"];
  status_label: string;
  detail: string;
}

export interface PublicStatusFeedQualityResponse {
  generated_at?: string;
  scope?: string;
  status: RouteStatus["severity"];
  status_label: string;
  status_color: string;
  updated_at?: string | null;
  age_seconds?: number | null;
  checks: PublicFeedQualityCheck[];
  feed_status: TransitFeedStatus;
}

export interface PublicTriageRoute {
  rank: number;
  entity_id: string;
  route_id?: string | null;
  label: string;
  severity: RouteStatus["severity"];
  severity_label: string;
  headline: string;
  short_summary: string;
  hazard_score?: number | null;
  active_alert_count: number;
  median_delay_seconds?: number | null;
  updated_at_ms?: number | null;
  evidence: string[];
  recommended_action: string;
}

export interface PublicStatusTriageResponse {
  generated_at?: string;
  scope?: string;
  triage_count: number;
  routes: PublicTriageRoute[];
}

export interface PublicStatusAlert {
  alert_id?: string | null;
  entity_id?: string;
  route_label?: string;
  severity: string;
  severity_label: string;
  severity_color: string;
  headline: string;
  recommended_action?: string;
  timestamp_ms?: number | null;
}

export interface PublicStatusAlertsResponse {
  generated_at?: string;
  scope?: string;
  alert_count: number;
  alerts: PublicStatusAlert[];
}

export interface PublicScorecardCorridor {
  entity_id?: string;
  label?: string;
  route_id?: string | null;
  on_time_pct?: number;
  avg_delay_seconds?: number;
  incident_count?: number;
  snapshot_count?: number;
  healthy_pct?: number;
  unstable_pct?: number;
}

export interface PublicScorecardNetwork {
  on_time_pct?: number;
  healthy_pct?: number;
  unstable_pct?: number;
  avg_delay_seconds?: number;
  unstable_corridor_count?: number;
}

export interface PublicStatusScorecardResponse {
  generated_at?: string;
  scope?: string;
  window_snapshots?: number;
  corridor_count?: number;
  total_incidents?: number;
  network: PublicScorecardNetwork;
  corridors: PublicScorecardCorridor[];
}

// ---------------------------------------------------------------------------
// Incident acknowledgement
// ---------------------------------------------------------------------------

export interface IncidentAckPayload {
  acknowledged: boolean;
  incident_id: string;
  acknowledged_at?: string;
  acknowledged_by?: string;
  note?: string;
  error?: string;
}
