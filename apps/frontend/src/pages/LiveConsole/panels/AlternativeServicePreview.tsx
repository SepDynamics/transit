import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  AdvisoryApiErrorResponse,
  AdvisoryDirectionOption,
  AdvisoryEvidence,
  AdvisoryLeg,
  AdvisoryOptionsResponse,
  AdvisoryStopOption,
  AlternativeAdvisory,
  AlternativeAdvisoryResponse,
  LineCard,
} from "../../../types/transit";
import { compareOperationalPriority, humanizeToken } from "../../../utils/formatters";
import { fetchOperatorPreviewJson } from "../../../utils/operatorApi";

interface AlternativeServicePreviewProps {
  lines: LineCard[];
  scope: string;
  selectedCorridorId: string | null;
  onSelectCorridor: (entityId: string) => void;
}

type OptionsLoadState =
  | "idle"
  | "loading"
  | "ready"
  | "selection_required"
  | "unavailable"
  | "unauthorized"
  | "error";

type DecisionLoadState = "idle" | "loading" | "ready" | "unauthorized" | "error";

const REASON_LABELS: Record<string, string> = {
  no_materially_better_reliable_alternative:
    "No materially better reliable alternative was found.",
  no_fresh_realtime_predictions: "Fresh predicted arrivals are not available.",
  no_reliable_disrupted_route_baseline:
    "The disrupted route does not have a reliable live arrival baseline.",
  invalid_realtime_prediction_evidence: "The live prediction evidence could not be evaluated.",
  missing_disruption_health: "Current disruption health is missing for this route and direction.",
  invalid_disruption_health: "Current disruption health is not valid enough to evaluate.",
  stale_disruption_health: "The disruption health signal is stale.",
  disruption_below_threshold: "The selected corridor is below the disruption threshold.",
  disruption_regime_not_actionable: "The selected corridor is not in an actionable disruption state.",
  topology_not_configured: "The alternative-service topology is not configured.",
  topology_not_found: "The configured alternative-service topology could not be found.",
  topology_invalid: "The alternative-service topology could not be loaded.",
  topology_unavailable: "The alternative-service topology is unavailable.",
  no_valid_stop_pair: "No valid origin and downstream destination pair is available.",
  live_evidence_unavailable: "Live prediction and route-health evidence is unavailable.",
  invalid_live_evidence: "The available live evidence could not be evaluated.",
};

const reasonLabel = (reason?: string | null): string =>
  reason ? REASON_LABELS[reason] ?? `${humanizeToken(reason)}.` : "Preview unavailable.";

const stopLabel = (stop: AdvisoryStopOption): string => {
  const name = stop.stop_name?.trim();
  return name ? `${name} (${stop.stop_id})` : stop.stop_id;
};

const formatClockTime = (timestampMs?: number | null): string => {
  if (typeof timestampMs !== "number" || !Number.isFinite(timestampMs)) return "n/a";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestampMs));
};

const formatDuration = (seconds?: number | null): string => {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "n/a";
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded}s`;
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
};

const formatExpiryDistance = (expiresAtMs: number, nowMs: number): string => {
  const remainingSeconds = Math.ceil((expiresAtMs - nowMs) / 1000);
  if (remainingSeconds <= 0) return "Expired";
  return `In ${formatDuration(remainingSeconds)}`;
};

const formatEvidenceValue = (key: string, value: unknown): string => {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (key.endsWith("_ms") && (key.includes("time") || key.includes("observed"))) {
      return formatClockTime(value);
    }
    if (key.endsWith("_seconds")) return formatDuration(value);
    if (key.endsWith("_meters")) return `${Math.round(value)} m`;
    if (
      value >= 0 &&
      value <= 1 &&
      (key.includes("confidence") || key.includes("coverage") || key.includes("score"))
    ) {
      return `${Math.round(value * 100)}%`;
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const directionValue = (directionId: number | null): string =>
  directionId === null ? "none" : String(directionId);

const parseDirectionValue = (value: string): number | null =>
  value === "none" ? null : Number.parseInt(value, 10);

const uniqueStops = (stops: AdvisoryStopOption[]): AdvisoryStopOption[] => {
  const byId = new Map<string, AdvisoryStopOption>();
  for (const stop of stops) {
    if (!stop.stop_id || byId.has(stop.stop_id)) continue;
    byId.set(stop.stop_id, stop);
  }
  return [...byId.values()].sort(
    (left, right) =>
      (left.sequence ?? Number.MAX_SAFE_INTEGER) -
        (right.sequence ?? Number.MAX_SAFE_INTEGER) ||
      stopLabel(left).localeCompare(stopLabel(right)),
  );
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const isInteger = (value: unknown): value is number =>
  isFiniteNumber(value) && Number.isInteger(value);

const isNonNegativeInteger = (value: unknown): value is number =>
  isInteger(value) && value >= 0;

const isPositiveInteger = (value: unknown): value is number =>
  isInteger(value) && value > 0;

const isUnitInterval = (value: unknown): value is number =>
  isFiniteNumber(value) && value >= 0 && value <= 1;

const hasExpectedProductBoundary = (value: unknown): boolean =>
  isRecord(value) &&
  value.advisory_only === true &&
  value.infers_cause === false &&
  value.guarantees_arrival === false &&
  value.issues_dispatch_instructions === false &&
  typeof value.statement === "string";

const isOptionsResponse = (payload: unknown): payload is AdvisoryOptionsResponse => {
  if (!isRecord(payload)) return false;
  const status = payload.status;
  const generatedAtMs = payload.generated_at_ms;
  return (
    (status === "available" || status === "selection_required" || status === "unavailable") &&
    payload.release_stage === "operator_preview" &&
    hasExpectedProductBoundary(payload.product_boundary) &&
    isPositiveInteger(generatedAtMs) &&
    typeof payload.disrupted_route_id === "string" &&
    (payload.route_label === null || typeof payload.route_label === "string") &&
    (payload.resolved_direction_id === null || isInteger(payload.resolved_direction_id)) &&
    Array.isArray(payload.directions) &&
    payload.directions.every(
      (direction) =>
        isRecord(direction) &&
        (direction.direction_id === null || isInteger(direction.direction_id)) &&
        typeof direction.label === "string",
    ) &&
    Array.isArray(payload.stops) &&
    payload.stops.every(
      (stop) =>
        isRecord(stop) &&
        typeof stop.stop_id === "string" &&
        typeof stop.stop_name === "string" &&
        isNonNegativeInteger(stop.sequence) &&
        Array.isArray(stop.downstream_stop_ids) &&
        stop.downstream_stop_ids.every((stopId) => typeof stopId === "string"),
    ) &&
    Array.isArray(payload.suppression_reasons) &&
    payload.suppression_reasons.every((reason) => typeof reason === "string")
  );
};

const isAdvisoryLeg = (value: unknown): value is AdvisoryLeg =>
  isRecord(value) &&
  (value.kind === "ride" || value.kind === "walk" || value.kind === "transfer") &&
  typeof value.from_stop_id === "string" &&
  typeof value.to_stop_id === "string" &&
  isNonNegativeInteger(value.duration_seconds) &&
  (value.departure_time_ms === null || isPositiveInteger(value.departure_time_ms)) &&
  (value.arrival_time_ms === null || isPositiveInteger(value.arrival_time_ms)) &&
  (value.route_id === null || typeof value.route_id === "string") &&
  (value.trip_id === null || typeof value.trip_id === "string") &&
  (value.direction_id === null || isInteger(value.direction_id)) &&
  (value.realtime_coverage === null || isUnitInterval(value.realtime_coverage)) &&
  (value.transfer_source === null || typeof value.transfer_source === "string");

const isAdvisoryEvidence = (value: unknown): value is AdvisoryEvidence =>
  isRecord(value) && typeof value.kind === "string" && isRecord(value.details);

const isAlternativeAdvisory = (value: unknown): value is AlternativeAdvisory =>
  isRecord(value) &&
  typeof value.disrupted_route_id === "string" &&
  typeof value.origin_stop_id === "string" &&
  typeof value.destination_stop_id === "string" &&
  Array.isArray(value.route_ids) &&
  value.route_ids.every((routeId) => typeof routeId === "string") &&
  isPositiveInteger(value.estimated_arrival_time_ms) &&
  isPositiveInteger(value.baseline_arrival_time_ms) &&
  isPositiveInteger(value.expected_time_saved_seconds) &&
  isNonNegativeInteger(value.total_walking_seconds) &&
  (value.total_walking_meters === null ||
    (isFiniteNumber(value.total_walking_meters) && value.total_walking_meters >= 0)) &&
  isNonNegativeInteger(value.total_transfer_seconds) &&
  isUnitInterval(value.confidence) &&
  (value.confidence_label === "low" ||
    value.confidence_label === "medium" ||
    value.confidence_label === "high") &&
  isPositiveInteger(value.expires_at_ms) &&
  typeof value.summary === "string" &&
  typeof value.explanation === "string" &&
  Array.isArray(value.legs) &&
  value.legs.every(isAdvisoryLeg) &&
  Array.isArray(value.evidence) &&
  value.evidence.every(isAdvisoryEvidence);

const isDecisionResponse = (payload: unknown): payload is AlternativeAdvisoryResponse => {
  if (!isRecord(payload)) return false;
  const status = payload.status;
  const generatedAtMs = payload.generated_at_ms;
  return (
    (status === "published" || status === "suppressed" || status === "unavailable") &&
    payload.release_stage === "operator_preview" &&
    hasExpectedProductBoundary(payload.product_boundary) &&
    isPositiveInteger(generatedAtMs) &&
    typeof payload.origin_stop_id === "string" &&
    typeof payload.destination_stop_id === "string" &&
    typeof payload.disrupted_route_id === "string" &&
    Array.isArray(payload.advisories) &&
    payload.advisories.every(isAlternativeAdvisory) &&
    (status !== "published" || payload.advisories.length > 0) &&
    payload.advisories.every(
      (advisory) => advisory.expires_at_ms > generatedAtMs,
    ) &&
    Array.isArray(payload.suppression_reasons) &&
    payload.suppression_reasons.every((reason) => typeof reason === "string") &&
    isNonNegativeInteger(payload.evaluated_candidate_count) &&
    (payload.baseline_arrival_time_ms === null ||
      isPositiveInteger(payload.baseline_arrival_time_ms)) &&
    (payload.resolved_direction_id === null || isInteger(payload.resolved_direction_id))
  );
};

const responseMessage = (payload: unknown): string | null =>
  isRecord(payload) && typeof payload.message === "string" ? payload.message : null;

const optionsStatusMatchesHttp = (
  payload: AdvisoryOptionsResponse,
  httpStatus: number,
  ok: boolean,
): boolean =>
  payload.status === "unavailable"
    ? httpStatus === 503 && !ok
    : httpStatus === 200 && ok;

const decisionStatusMatchesHttp = (
  payload: AlternativeAdvisoryResponse,
  httpStatus: number,
  ok: boolean,
): boolean =>
  payload.status === "unavailable"
    ? httpStatus === 503 && !ok
    : httpStatus === 200 && ok;

const optionsMatchRequest = (
  payload: AdvisoryOptionsResponse,
  disruptedRouteId: string,
  requestedDirectionId: number | null,
): boolean =>
  payload.disrupted_route_id === disruptedRouteId &&
  (requestedDirectionId === null ||
    payload.resolved_direction_id === requestedDirectionId);

const decisionMatchesRequest = (
  payload: AlternativeAdvisoryResponse,
  request: {
    originStopId: string;
    destinationStopId: string;
    disruptedRouteId: string;
    directionId: number | null;
  },
): boolean =>
  payload.origin_stop_id === request.originStopId &&
  payload.destination_stop_id === request.destinationStopId &&
  payload.disrupted_route_id === request.disruptedRouteId &&
  (request.directionId === null || payload.resolved_direction_id === request.directionId) &&
  payload.advisories.every(
    (advisory) =>
      advisory.origin_stop_id === request.originStopId &&
      advisory.destination_stop_id === request.destinationStopId &&
      advisory.disrupted_route_id === request.disruptedRouteId,
  );

interface SearchableStopSelectProps {
  id: string;
  label: string;
  stops: AdvisoryStopOption[];
  value: string | null;
  excludedStopId?: string | null;
  disabled?: boolean;
  onChange: (stopId: string | null) => void;
}

function SearchableStopSelect({
  id,
  label,
  stops,
  value,
  excludedStopId,
  disabled = false,
  onChange,
}: SearchableStopSelectProps) {
  const listboxId = useId();
  const validationId = `${id}-validation`;
  const wrapperRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedStop = stops.find((stop) => stop.stop_id === value) ?? null;
  const [query, setQuery] = useState(selectedStop ? stopLabel(selectedStop) : "");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  useEffect(() => {
    setQuery(selectedStop ? stopLabel(selectedStop) : "");
  }, [selectedStop?.stop_id, selectedStop?.stop_name]);

  const matches = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const available = stops.filter((stop) => stop.stop_id !== excludedStopId);
    if (selectedStop) {
      return [selectedStop, ...available.filter((stop) => stop.stop_id !== selectedStop.stop_id)].slice(
        0,
        20,
      );
    }
    if (!needle) return available.slice(0, 20);
    return available
      .filter((stop) => stopLabel(stop).toLocaleLowerCase().includes(needle))
      .slice(0, 20);
  }, [excludedStopId, query, selectedStop, stops]);

  useEffect(() => {
    if (!open || !matches.length) {
      setActiveIndex(-1);
      return;
    }
    setActiveIndex((current) =>
      current >= 0 && current < matches.length ? current : 0,
    );
  }, [matches, open]);

  useEffect(() => {
    if (open && activeIndex >= 0) {
      optionRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex, open]);

  const choose = (stop: AdvisoryStopOption) => {
    onChange(stop.stop_id);
    setQuery(stopLabel(stop));
    setOpen(false);
    setActiveIndex(-1);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "Enter" && open && activeIndex >= 0 && matches[activeIndex]) {
      event.preventDefault();
      choose(matches[activeIndex]);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => {
        if (!matches.length) return -1;
        if (event.key === "ArrowDown") {
          return current < 0 ? 0 : (current + 1) % matches.length;
        }
        return current <= 0 ? matches.length - 1 : current - 1;
      });
      return;
    }
    if (event.key === "Home" && open && matches.length) {
      event.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (event.key === "End" && open && matches.length) {
      event.preventDefault();
      setActiveIndex(matches.length - 1);
    }
  };

  const invalidSelection = Boolean(query && !selectedStop);

  return (
    <div
      ref={wrapperRef}
      className="advisory-stop-picker"
      onBlur={(event) => {
        if (!wrapperRef.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="search"
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-expanded={open}
        aria-activedescendant={
          open && activeIndex >= 0 && matches[activeIndex]
            ? `${listboxId}-option-${activeIndex}`
            : undefined
        }
        aria-describedby={invalidSelection ? validationId : undefined}
        aria-invalid={invalidSelection}
        placeholder={stops.length ? "Search by stop name or ID" : "No stops available"}
        value={query}
        disabled={disabled || !stops.length}
        onFocus={() => {
          setOpen(true);
          setActiveIndex((current) => (current >= 0 ? current : 0));
        }}
        onKeyDown={handleKeyDown}
        onChange={(event) => {
          setQuery(event.target.value);
          onChange(null);
          setOpen(true);
          setActiveIndex(0);
        }}
      />
      {open && !disabled ? (
        <div id={listboxId} className="advisory-stop-picker__menu" role="listbox">
          {matches.map((stop, index) => (
            <button
              key={stop.stop_id}
              id={`${listboxId}-option-${index}`}
              ref={(element) => {
                optionRefs.current[index] = element;
              }}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={stop.stop_id === value}
              className={index === activeIndex ? "is-selected" : undefined}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(stop)}
            >
              <strong>{stop.stop_name || stop.stop_id}</strong>
              <span>{stop.stop_id}</span>
            </button>
          ))}
          {!matches.length ? (
            <span className="advisory-stop-picker__empty">No matching route stops.</span>
          ) : null}
        </div>
      ) : null}
      {invalidSelection ? (
        <span id={validationId} className="advisory-stop-picker__validation">
          Choose a stop from the list.
        </span>
      ) : null}
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: AdvisoryEvidence[] }) {
  return (
    <div className="advisory-evidence-list">
      {evidence.map((item, index) => (
        <details key={`${item.kind}-${index}`}>
          <summary>{humanizeToken(item.kind)}</summary>
          <dl>
            {Object.entries(item.details).map(([key, value]) => (
              <div key={key}>
                <dt>{humanizeToken(key)}</dt>
                <dd>{formatEvidenceValue(key, value)}</dd>
              </div>
            ))}
          </dl>
        </details>
      ))}
    </div>
  );
}

function PublishedAdvisory({
  advisory,
  stopNames,
  nowMs,
}: {
  advisory: AlternativeAdvisory;
  stopNames: Map<string, string>;
  nowMs: number;
}) {
  const displayStop = (stopId: string) => stopNames.get(stopId) ?? stopId;

  return (
    <article className="advisory-result-card">
      <div className="advisory-result-card__header">
        <div>
          <span className="section-eyebrow">Published preview</span>
          <h3>{advisory.route_ids.join(" → ")}</h3>
        </div>
        <span className="badge badge--calm">
          {Math.round(advisory.confidence * 100)}% · {advisory.confidence_label}
        </span>
      </div>
      <p className="advisory-result-card__summary">{advisory.summary}</p>
      <p className="advisory-result-card__explanation">{advisory.explanation}</p>

      <div className="advisory-result-card__metrics">
        <div>
          <span>Expected saving</span>
          <strong>{formatDuration(advisory.expected_time_saved_seconds)}</strong>
        </div>
        <div>
          <span>Estimated arrival</span>
          <strong>{formatClockTime(advisory.estimated_arrival_time_ms)}</strong>
        </div>
        <div>
          <span>Walking</span>
          <strong>{formatDuration(advisory.total_walking_seconds)}</strong>
          {typeof advisory.total_walking_meters === "number" ? (
            <small>{Math.round(advisory.total_walking_meters)} m</small>
          ) : null}
        </div>
        <div>
          <span>Transfer allowance</span>
          <strong>{formatDuration(advisory.total_transfer_seconds)}</strong>
        </div>
        <div>
          <span>Expires</span>
          <strong>{formatClockTime(advisory.expires_at_ms)}</strong>
          <small>{formatExpiryDistance(advisory.expires_at_ms, nowMs)}</small>
        </div>
      </div>

      <div className="advisory-result-card__section">
        <h4>Journey legs</h4>
        <ol className="advisory-leg-list">
          {advisory.legs.map((leg, index) => (
            <li key={`${leg.kind}-${leg.from_stop_id}-${leg.to_stop_id}-${index}`}>
              <div className="advisory-leg-list__marker">{index + 1}</div>
              <div className="advisory-leg-list__body">
                <div className="advisory-leg-list__title">
                  <strong>
                    {leg.kind === "ride" && leg.route_id
                      ? `Ride ${leg.route_id}`
                      : humanizeToken(leg.kind)}
                  </strong>
                  <span>{formatDuration(leg.duration_seconds)}</span>
                </div>
                <p>
                  {displayStop(leg.from_stop_id)} → {displayStop(leg.to_stop_id)}
                </p>
                <div className="advisory-leg-list__meta">
                  {leg.departure_time_ms ? <span>Depart {formatClockTime(leg.departure_time_ms)}</span> : null}
                  {leg.arrival_time_ms ? <span>Arrive {formatClockTime(leg.arrival_time_ms)}</span> : null}
                  {typeof leg.realtime_coverage === "number" ? (
                    <span>{Math.round(leg.realtime_coverage * 100)}% live coverage</span>
                  ) : null}
                  {leg.transfer_source ? <span>{humanizeToken(leg.transfer_source)}</span> : null}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>

      <div className="advisory-result-card__section">
        <h4>Evidence used</h4>
        <EvidenceList evidence={advisory.evidence} />
      </div>
    </article>
  );
}

const corridorOptionLabel = (line: LineCard): string => {
  const direction =
    typeof line.direction_id === "number" ? `direction ${line.direction_id}` : "direction not resolved";
  return `${line.label || line.route_id || line.entity_id} · ${direction}`;
};

const normalizeDirections = (
  directions: AdvisoryDirectionOption[],
  selectedDirectionId: number | null,
): AdvisoryDirectionOption[] => {
  const byValue = new Map<string, AdvisoryDirectionOption>();
  for (const direction of directions) byValue.set(directionValue(direction.direction_id), direction);
  if (selectedDirectionId !== null && !byValue.has(directionValue(selectedDirectionId))) {
    byValue.set(directionValue(selectedDirectionId), {
      direction_id: selectedDirectionId,
      label: `Direction ${selectedDirectionId}`,
    });
  }
  return [...byValue.values()].sort((left, right) =>
    directionValue(left.direction_id).localeCompare(directionValue(right.direction_id)),
  );
};

export default function AlternativeServicePreview({
  lines,
  scope,
  selectedCorridorId,
  onSelectCorridor,
}: AlternativeServicePreviewProps) {
  const corridors = useMemo(
    () => [...lines].filter((line) => Boolean(line.route_id)).sort(compareOperationalPriority),
    [lines],
  );
  const selectedCorridor =
    corridors.find((line) => line.entity_id === selectedCorridorId) ?? null;
  const routeId = selectedCorridor?.route_id?.trim() || "";

  const [directionId, setDirectionId] = useState<number | null>(null);
  const [optionsState, setOptionsState] = useState<OptionsLoadState>("idle");
  const [options, setOptions] = useState<AdvisoryOptionsResponse | null>(null);
  const [optionsMessage, setOptionsMessage] = useState<string | null>(null);
  const [originStopId, setOriginStopId] = useState<string | null>(null);
  const [destinationStopId, setDestinationStopId] = useState<string | null>(null);
  const [decisionState, setDecisionState] = useState<DecisionLoadState>("idle");
  const [decision, setDecision] = useState<AlternativeAdvisoryResponse | null>(null);
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const evaluationController = useRef<AbortController | null>(null);
  const serverClockOffsetMs = useRef(0);

  useEffect(() => {
    evaluationController.current?.abort();
    setOptions(null);
    setOptionsState(selectedCorridor?.route_id && scope === "live" ? "loading" : "idle");
    setOptionsMessage(null);
    setDirectionId(
      typeof selectedCorridor?.direction_id === "number" ? selectedCorridor.direction_id : null,
    );
    setOriginStopId(null);
    setDestinationStopId(null);
    setDecision(null);
    setDecisionState("idle");
    setDecisionMessage(null);
  }, [scope, selectedCorridor?.entity_id, selectedCorridor?.route_id]);

  useEffect(() => {
    evaluationController.current?.abort();
    if (scope !== "live" || !routeId) {
      setOptions(null);
      setOptionsState("idle");
      setOptionsMessage(null);
      setOriginStopId(null);
      setDestinationStopId(null);
      setDecision(null);
      setDecisionState("idle");
      setDecisionMessage(null);
      return;
    }

    const controller = new AbortController();
    const params = new URLSearchParams({ disrupted_route_id: routeId });
    if (directionId !== null) params.set("direction_id", String(directionId));

    setOptions(null);
    setOptionsState("loading");
    setOptionsMessage(null);
    setOriginStopId(null);
    setDestinationStopId(null);
    setDecision(null);
    setDecisionState("idle");
    setDecisionMessage(null);

    fetchOperatorPreviewJson<AdvisoryOptionsResponse | AdvisoryApiErrorResponse>(
      `/api/transit/alternative-advisories/options?${params.toString()}`,
      controller.signal,
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.status === 401 || result.status === 403) {
          setOptionsState("unauthorized");
          setOptionsMessage(
            "Open this panel through the authenticated operations hostname or VPN proxy.",
          );
          return;
        }

        const payload = result.payload;
        if (isOptionsResponse(payload)) {
          const advisoryOptions = payload;
          if (
            !optionsStatusMatchesHttp(advisoryOptions, result.status, result.ok) ||
            !optionsMatchRequest(advisoryOptions, routeId, directionId)
          ) {
            setOptionsState("error");
            setOptionsMessage(
              "The stop-options response did not match the active request and safety contract.",
            );
            return;
          }
          setOptions(advisoryOptions);
          if (advisoryOptions.status === "selection_required") {
            setOptionsState("selection_required");
            setOptionsMessage("Select a direction to load its ordered route stops.");
            return;
          }
          if (advisoryOptions.status === "unavailable" || !result.ok) {
            setOptionsState("unavailable");
            setOptionsMessage(reasonLabel(advisoryOptions.suppression_reasons?.[0]));
            return;
          }
          if (!advisoryOptions.stops?.length) {
            setOptionsState("unavailable");
            setOptionsMessage("No ordered stop options are available for this corridor.");
            return;
          }
          setOptionsState("ready");
          if (
            directionId === null &&
            typeof advisoryOptions.resolved_direction_id === "number"
          ) {
            setDirectionId(advisoryOptions.resolved_direction_id);
          }
          return;
        }

        setOptionsState(result.status === 503 ? "unavailable" : "error");
        setOptionsMessage(
          responseMessage(payload) ||
            (result.status === 404
              ? "The protected stop-options endpoint is not available."
              : payload
                ? "The stop-options response did not satisfy the operator-preview safety contract."
                : `Stop options request failed (HTTP ${result.status}).`),
        );
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setOptionsState("error");
        setOptionsMessage(error instanceof Error ? error.message : "Stop options request failed.");
      });

    return () => controller.abort();
  }, [directionId, routeId, scope]);

  useEffect(
    () => () => {
      evaluationController.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (decision?.status !== "published") return;
    const nextExpiry = decision.advisories
      .map((advisory) => advisory.expires_at_ms)
      .filter((expiresAtMs) => expiresAtMs > nowMs)
      .sort((left, right) => left - right)[0];
    if (nextExpiry === undefined) return;
    const localNowMs = Date.now();
    const effectiveNowMs = Math.max(
      localNowMs,
      localNowMs + serverClockOffsetMs.current,
    );
    const timer = window.setTimeout(
      () => {
        const localTimestampMs = Date.now();
        setNowMs(
          Math.max(
            localTimestampMs,
            localTimestampMs + serverClockOffsetMs.current,
          ),
        );
      },
      Math.min(
        Math.max(nextExpiry - effectiveNowMs + 50, 50),
        2_147_000_000,
      ),
    );
    return () => window.clearTimeout(timer);
  }, [decision, nowMs]);

  const stops = useMemo(() => uniqueStops(options?.stops ?? []), [options?.stops]);
  const destinationStopIds = useMemo(
    () => new Set(stops.flatMap((stop) => stop.downstream_stop_ids)),
    [stops],
  );
  const selectedOriginStop =
    stops.find((stop) => stop.stop_id === originStopId) ?? null;
  const originStops = destinationStopId
    ? stops.filter((stop) => stop.downstream_stop_ids.includes(destinationStopId))
    : stops.filter((stop) => stop.downstream_stop_ids.length > 0);
  const destinationStops = selectedOriginStop
    ? stops.filter((stop) => selectedOriginStop.downstream_stop_ids.includes(stop.stop_id))
    : stops.filter((stop) => destinationStopIds.has(stop.stop_id));
  const stopNames = useMemo(
    () => new Map(stops.map((stop) => [stop.stop_id, stop.stop_name || stop.stop_id])),
    [stops],
  );
  const directions = normalizeDirections(options?.directions ?? [], directionId);
  const freshAdvisories =
    decision?.status === "published"
      ? decision.advisories.filter((advisory) => advisory.expires_at_ms > nowMs)
      : [];
  const expiredAdvisoryCount =
    decision?.status === "published"
      ? decision.advisories.length - freshAdvisories.length
      : 0;
  const canEvaluate =
    scope === "live" &&
    optionsState === "ready" &&
    Boolean(routeId && originStopId && destinationStopId && originStopId !== destinationStopId);

  const evaluate = async (event: FormEvent) => {
    event.preventDefault();
    if (!canEvaluate || !originStopId || !destinationStopId) return;

    evaluationController.current?.abort();
    const controller = new AbortController();
    evaluationController.current = controller;
    const params = new URLSearchParams({
      origin_stop_id: originStopId,
      destination_stop_id: destinationStopId,
      disrupted_route_id: routeId,
    });
    if (directionId !== null) params.set("direction_id", String(directionId));

    setDecision(null);
    setDecisionState("loading");
    setDecisionMessage(null);
    const requestStartedAtMs = Date.now();
    try {
      const result = await fetchOperatorPreviewJson<
        AlternativeAdvisoryResponse | AdvisoryApiErrorResponse
      >(`/api/transit/alternative-advisories?${params.toString()}`, controller.signal);
      if (controller.signal.aborted) return;
      if (result.status === 401 || result.status === 403) {
        setDecisionState("unauthorized");
        setDecisionMessage(
          "Operator authentication is required. Use the protected operations hostname or VPN proxy.",
        );
        return;
      }
      if (isDecisionResponse(result.payload)) {
        if (
          !decisionStatusMatchesHttp(result.payload, result.status, result.ok) ||
          !decisionMatchesRequest(result.payload, {
            originStopId,
            destinationStopId,
            disruptedRouteId: routeId,
            directionId,
          })
        ) {
          setDecisionState("error");
          setDecisionMessage(
            "The response did not match the active request and operator-preview safety contract.",
          );
          return;
        }
        setDecision(result.payload);
        const receivedAtMs = Date.now();
        const estimatedServerNowMs =
          result.payload.generated_at_ms + (receivedAtMs - requestStartedAtMs);
        serverClockOffsetMs.current = estimatedServerNowMs - receivedAtMs;
        setNowMs(Math.max(receivedAtMs, estimatedServerNowMs));
        setDecisionState("ready");
        return;
      }
      setDecisionState("error");
      setDecisionMessage(
        responseMessage(result.payload) ||
          (result.payload
            ? "The response did not satisfy the operator-preview safety contract."
            : `Alternative preview request failed (HTTP ${result.status}).`),
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setDecisionState("error");
      setDecisionMessage(error instanceof Error ? error.message : "Alternative preview request failed.");
    }
  };

  return (
    <section className="section panel advisory-preview" aria-labelledby="advisory-preview-title">
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">Operator preview</span>
          <h2 id="advisory-preview-title" className="section__title">
            Alternative service preview
          </h2>
          <p className="section__hint">
            Evaluate a stop-scoped alternative only after selecting the affected journey.
          </p>
        </div>
      </div>

      <div className="advisory-preview__boundary" role="note">
        <strong>Operator preview — advisory only</strong>
        <span>No arrival guarantee, causal claim, or dispatch instruction.</span>
      </div>

      <form className="advisory-preview__form" onSubmit={evaluate}>
        <div className="advisory-preview__selector-grid">
          <div className="advisory-field">
            <label htmlFor="advisory-corridor">Disrupted corridor</label>
            <select
              id="advisory-corridor"
              value={selectedCorridor?.entity_id ?? ""}
              onChange={(event) => {
                if (event.target.value) {
                  evaluationController.current?.abort();
                  onSelectCorridor(event.target.value);
                }
              }}
            >
              <option value="" disabled>
                Select a scored corridor
              </option>
              {corridors.map((line) => (
                <option key={line.entity_id} value={line.entity_id}>
                  {corridorOptionLabel(line)}
                </option>
              ))}
            </select>
          </div>

          <div className="advisory-field">
            <label htmlFor="advisory-direction">Direction</label>
            <select
              id="advisory-direction"
              value={directionValue(directionId)}
              disabled={!routeId || optionsState === "loading" || (!directions.length && directionId === null)}
              onChange={(event) => {
                evaluationController.current?.abort();
                setDirectionId(parseDirectionValue(event.target.value));
                setOriginStopId(null);
                setDestinationStopId(null);
              }}
            >
              <option value="none">Infer when unambiguous</option>
              {directions
                .filter((direction) => direction.direction_id !== null)
                .map((direction) => (
                  <option key={directionValue(direction.direction_id)} value={directionValue(direction.direction_id)}>
                    {direction.label}
                  </option>
                ))}
            </select>
          </div>

          <SearchableStopSelect
            id="advisory-origin"
            label="Origin stop"
            stops={originStops}
            value={originStopId}
            excludedStopId={destinationStopId}
            disabled={optionsState !== "ready"}
            onChange={(stopId) => {
              evaluationController.current?.abort();
              setOriginStopId(stopId);
              if (
                stopId &&
                destinationStopId &&
                !stops
                  .find((stop) => stop.stop_id === stopId)
                  ?.downstream_stop_ids.includes(destinationStopId)
              ) {
                setDestinationStopId(null);
              }
              setDecision(null);
              setDecisionState("idle");
            }}
          />

          <SearchableStopSelect
            id="advisory-destination"
            label="Destination stop"
            stops={destinationStops}
            value={destinationStopId}
            excludedStopId={originStopId}
            disabled={optionsState !== "ready"}
            onChange={(stopId) => {
              evaluationController.current?.abort();
              setDestinationStopId(stopId);
              setDecision(null);
              setDecisionState("idle");
            }}
          />
        </div>

        <div className="advisory-preview__actions">
          <button type="submit" className="advisory-preview__submit" disabled={!canEvaluate || decisionState === "loading"}>
            {decisionState === "loading" ? "Evaluating live evidence…" : "Preview alternatives"}
          </button>
          <span>Uses current live predictions and direction-scoped route health.</span>
        </div>
      </form>

      <div className="advisory-preview__status" aria-live="polite">
        {scope !== "live" ? (
          <div className="advisory-state advisory-state--notice">
            <strong>Live source required</strong>
            <p>Switch the console source to Live feed before requesting an operator preview.</p>
          </div>
        ) : null}
        {scope === "live" && optionsState === "loading" ? (
          <div className="advisory-state advisory-state--loading">
            <strong>Loading ordered route stops…</strong>
          </div>
        ) : null}
        {scope === "live" && optionsState === "selection_required" ? (
          <div className="advisory-state advisory-state--notice">
            <strong>Direction required</strong>
            <p>{optionsMessage}</p>
          </div>
        ) : null}
        {scope === "live" && optionsState === "unauthorized" ? (
          <div className="advisory-state advisory-state--warning">
            <strong>Protected operator access required</strong>
            <p>{optionsMessage}</p>
          </div>
        ) : null}
        {scope === "live" && (optionsState === "unavailable" || optionsState === "error") ? (
          <div className="advisory-state advisory-state--warning">
            <strong>{optionsState === "unavailable" ? "Preview unavailable" : "Stop options could not be loaded"}</strong>
            <p>{optionsMessage}</p>
          </div>
        ) : null}
        {decisionState === "loading" ? (
          <div className="advisory-state advisory-state--loading">
            <strong>Comparing reliable alternatives…</strong>
          </div>
        ) : null}
        {decisionState === "unauthorized" || decisionState === "error" ? (
          <div className="advisory-state advisory-state--warning">
            <strong>{decisionState === "unauthorized" ? "Protected operator access required" : "Preview request failed"}</strong>
            <p>{decisionMessage}</p>
          </div>
        ) : null}
        {decision?.status === "suppressed" ? (
          <div className="advisory-state advisory-state--suppressed">
            <strong>No alternative published</strong>
            <ul>
              {decision.suppression_reasons.map((reason) => (
                <li key={reason}>{reasonLabel(reason)}</li>
              ))}
            </ul>
            <small>{decision.evaluated_candidate_count} candidates evaluated.</small>
          </div>
        ) : null}
        {decision?.status === "unavailable" ? (
          <div className="advisory-state advisory-state--warning">
            <strong>Preview unavailable</strong>
            <ul>
              {decision.suppression_reasons.map((reason) => (
                <li key={reason}>{reasonLabel(reason)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {decision?.status === "published" ? (
          <div className="advisory-result-list">
            {expiredAdvisoryCount ? (
              <div className="advisory-state advisory-state--warning">
                <strong>
                  {freshAdvisories.length
                    ? `${expiredAdvisoryCount} preview alternative${expiredAdvisoryCount === 1 ? " has" : "s have"} expired`
                    : "Preview expired"}
                </strong>
                <p>Run the evaluation again before using this advisory.</p>
              </div>
            ) : null}
            {freshAdvisories.map((advisory, index) => (
              <PublishedAdvisory
                key={`${advisory.route_ids.join("-")}-${advisory.estimated_arrival_time_ms}-${index}`}
                advisory={advisory}
                stopNames={stopNames}
                nowMs={nowMs}
              />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
