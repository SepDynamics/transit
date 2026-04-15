import type { SourceResponse, TransitReplayTrace } from "../../../types/transit";
import { relativeTimeFromMs } from "../../../utils/formatters";

interface ToolbarPanelProps {
  sourceResponse: SourceResponse;
  replayTraces: TransitReplayTrace[];
  scope: string;
  selectedTraceId: string;
  onScopeChange: (scope: string) => void;
  onTraceChange: (traceId: string) => void;
}

const traceOptionLabel = (trace: TransitReplayTrace): string => {
  const parts = [trace.trace_id];
  if (typeof trace.snapshot_count === "number" && trace.snapshot_count > 0) {
    parts.push(`${trace.snapshot_count} saved checks`);
  }
  if (trace.latest_snapshot_timestamp_ms) {
    parts.push(relativeTimeFromMs(trace.latest_snapshot_timestamp_ms));
  }
  return parts.join(" • ");
};

export default function ToolbarPanel({
  sourceResponse,
  replayTraces,
  scope,
  selectedTraceId,
  onScopeChange,
  onTraceChange,
}: ToolbarPanelProps) {
  return (
    <section className="toolbar panel">
      <div>
        <h2 className="section__title">Choose the data view</h2>
        <p className="section__hint">Use live data for today, or replay saved proof from an earlier event.</p>
      </div>
      <div className="toolbar__controls">
        {replayTraces.length ? (
          <label className="trace-picker">
            <span>Saved replay</span>
            <select
              value={selectedTraceId}
              onChange={(event) => onTraceChange(event.target.value)}
              disabled={scope === "live"}
            >
              <option value="">All traces</option>
              {replayTraces.map((trace) => (
                <option key={trace.trace_id} value={trace.trace_id}>
                  {traceOptionLabel(trace)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="toggle-strip">
          {sourceResponse.scopes.map((option) => (
            <button
              key={option.id}
              type="button"
              className={option.id === scope ? "toggle-strip__button is-active" : "toggle-strip__button"}
              onClick={() => onScopeChange(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
