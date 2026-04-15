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

interface PriorityCorridorSeed {
  name: string;
  area: string;
  routeIds: string[];
  keywords: string[];
  streetFix: string;
  value: string;
  treatmentStatus: "present" | "planned";
  treatmentLabel: string;
}

interface PriorityCorridorRow {
  seed: PriorityCorridorSeed;
  line: LineCard | null;
  scorecard: TransitScorecardCorridor | null;
  vehicleCount: number;
  alertCount: number;
  matchCount: number;
}

const PRIORITY_CORRIDORS: PriorityCorridorSeed[] = [
  {
    name: "Columbus Avenue",
    area: "Roxbury, South End, Ruggles, Nubian",
    routeIds: ["22", "29", "44", "45"],
    keywords: ["columbus", "ruggles", "nubian", "jackson square"],
    streetFix: "Bus lanes and signal priority",
    value: "A ready before-and-after story for one of Boston's best-known bus priority corridors.",
    treatmentStatus: "present",
    treatmentLabel: "TSP / bus lane present",
  },
  {
    name: "Blue Hill Avenue",
    area: "Mattapan, Dorchester, Grove Hall, Nubian",
    routeIds: ["22", "23", "28", "29", "31"],
    keywords: ["blue hill", "mattapan", "grove hall", "nubian"],
    streetFix: "Bus lane, curb, and signal monitoring",
    value: "A high-ridership corridor where small reliability gains are easy for riders to feel.",
    treatmentStatus: "planned",
    treatmentLabel: "Priority project listed",
  },
  {
    name: "Route 57 / Brighton Avenue",
    area: "Kenmore, Allston, Brighton, Watertown",
    routeIds: ["57", "64", "66"],
    keywords: ["brighton", "kenmore", "watertown", "allston"],
    streetFix: "Route 57 transit priority",
    value: "A focused pilot lane for proving whether priority treatments reduce bunching and gaps.",
    treatmentStatus: "present",
    treatmentLabel: "TSP / bus lane present",
  },
  {
    name: "Hyde Park Avenue",
    area: "Forest Hills, Roslindale, Hyde Park",
    routeIds: ["32", "33", "40", "50"],
    keywords: ["hyde park", "forest hills", "roslindale"],
    streetFix: "Queue, curb, and stop reliability checks",
    value: "A clear way to separate traffic delay from stop, curb, and terminal pressure.",
    treatmentStatus: "planned",
    treatmentLabel: "Priority project listed",
  },
  {
    name: "North Station to Seaport",
    area: "Downtown, South Station, Seaport",
    routeIds: ["4", "7", "749", "751", "SL4", "SL5"],
    keywords: ["north station", "south station", "seaport", "silver line"],
    streetFix: "Event-day and curb conflict watch",
    value: "A simple answer for major events: is transit still moving, or does staff need to intervene?",
    treatmentStatus: "planned",
    treatmentLabel: "Priority project listed",
  },
  {
    name: "Rutherford Avenue",
    area: "Charlestown, Sullivan Square, downtown approaches",
    routeIds: ["92", "93", "111"],
    keywords: ["rutherford", "sullivan", "charlestown", "haymarket"],
    streetFix: "Bus-priority and intersection delay proof",
    value: "A shared city-agency view of whether a street redesign helps bus riders in practice.",
    treatmentStatus: "planned",
    treatmentLabel: "Priority project listed",
  },
];

const normalize = (value?: string | null): string => value?.toLowerCase() ?? "";

const routeMatchesSeed = (
  seed: PriorityCorridorSeed,
  routeId?: string | null,
  label?: string | null,
): boolean => {
  const normalizedRouteId = normalize(routeId);
  const normalizedLabel = normalize(label);
  return (
    seed.routeIds.some((candidate) => normalize(candidate) === normalizedRouteId) ||
    seed.keywords.some((keyword) => normalizedLabel.includes(keyword))
  );
};

const buildPriorityRows = (
  lines: LineCard[],
  scorecardResponse: ScorecardResponse | null,
): PriorityCorridorRow[] =>
  PRIORITY_CORRIDORS.map((seed) => {
    const matchedLines = lines
      .filter((line) => routeMatchesSeed(seed, line.route_id, line.label))
      .sort(compareOperationalPriority);
    const scorecards =
      scorecardResponse?.corridors
        .filter((corridor) =>
          routeMatchesSeed(seed, corridor.route_id, corridor.label),
        )
        .sort(compareOperationalPriority) ?? [];

    return {
      seed,
      line: matchedLines[0] ?? null,
      scorecard: scorecards[0] ?? null,
      vehicleCount: matchedLines.reduce(
        (total, line) => total + (line.vehicle_count ?? 0),
        0,
      ),
      alertCount: matchedLines.reduce(
        (total, line) => total + (line.active_alert_count ?? 0),
        0,
      ),
      matchCount: Math.max(matchedLines.length, scorecards.length),
    };
  }).sort((left, right) => {
    const leftScore = left.line?.priority_score ?? left.scorecard?.avg_hazard ?? 0;
    const rightScore = right.line?.priority_score ?? right.scorecard?.avg_hazard ?? 0;
    return rightScore - leftScore || left.seed.name.localeCompare(right.seed.name);
  });

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
          <span className="section-eyebrow">Boston priority corridors</span>
          <h2 id="priority-corridors-title" className="section__title">
            Start with the routes Boston and MBTA already care about.
          </h2>
          <p className="section__hint">
            Start from the full network feed, then focus on a marketable Boston
            pilot: where buses are slowed, which street tool is likely involved,
            and what proof a stakeholder can use.
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
              key={row.seed.name}
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
                  <strong>{row.seed.name}</strong>
                  <span>{row.seed.area}</span>
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
              <p>{row.seed.value}</p>
              <div
                className={`treatment-badge treatment-badge--${row.seed.treatmentStatus}`}
              >
                <span aria-hidden="true">
                  {row.seed.treatmentStatus === "present" ? "✓" : "•"}
                </span>
                {row.seed.treatmentLabel}
              </div>
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
                  <span>Matched routes</span>
                  <strong>{row.matchCount}</strong>
                </div>
              </div>
              <div className="signature-card__meta">
                <span>{row.seed.streetFix}</span>
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
