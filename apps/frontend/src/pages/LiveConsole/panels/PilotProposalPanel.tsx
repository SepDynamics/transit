import type { ScorecardResponse, SourceResponse } from "../../../types/transit";

interface PilotProposalPanelProps {
  sourceResponse: SourceResponse;
  scorecardResponse: ScorecardResponse | null;
}

const countConfiguredFeeds = (sourceResponse: SourceResponse): number =>
  Object.values(sourceResponse.configured_feeds ?? {}).filter(Boolean).length ||
  (sourceResponse.available?.live ? 1 : 0);

export default function PilotProposalPanel({
  sourceResponse,
  scorecardResponse,
}: PilotProposalPanelProps) {
  const feedCount = countConfiguredFeeds(sourceResponse);

  return (
    <section className="section panel pilot-panel" aria-labelledby="pilot-title">
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">30-day pilot</span>
          <h2 id="pilot-title" className="section__title">
            Boston Bus Reliability Intelligence Pilot
          </h2>
          <p className="section__hint">
            A low-lift pilot for 5 to 10 priority corridors using public MBTA
            data. The goal is not another map. The goal is a weekly answer:
            where should Boston act first, and did the action help riders?
          </p>
        </div>
        <div className="pilot-panel__stat">
          <strong>{feedCount}</strong>
          <span>public feed lane{feedCount === 1 ? "" : "s"} ready</span>
        </div>
      </div>

      <div className="pilot-grid">
        <article className="pilot-card pilot-card--scope">
          <span>Scope</span>
          <strong>5 to 10 MBTA/BTD priority corridors</strong>
          <p>
            Start with Columbus Avenue, Blue Hill Avenue, Route 57, Hyde Park
            Avenue, and a downtown event corridor. Use public GTFS, GTFS-realtime,
            and MBTA API data first.
          </p>
        </article>
        <article className="pilot-card">
          <span>Deliverables</span>
          <strong>Weekly decision report</strong>
          <p>
            Top route concerns, bunching and gap evidence, active alert links,
            replayable incident proof, and a ranked list for bus lane, signal,
            stop, or curb follow-up.
          </p>
        </article>
        <article className="pilot-card">
          <span>Proof</span>
          <strong>{scorecardResponse?.window_snapshots ?? 0} checks already saved</strong>
          <p>
            The same record can support public updates, grant reporting, and
            before-and-after pilot evaluation.
          </p>
        </article>
        <article className="pilot-card">
          <span>Why now</span>
          <strong>No hardware or paid data needed to start</strong>
          <p>
            Add curb, enforcement, event, or signal feeds later. The first pilot
            can run on public transit data and a clear corridor list.
          </p>
        </article>
      </div>
    </section>
  );
}
