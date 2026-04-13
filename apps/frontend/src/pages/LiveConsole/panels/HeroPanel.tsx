import type { TransitHealth } from "../../../types/transit";
import { relativeTime, serviceTone, type ServiceState } from "../../../utils/formatters";

interface HeroPanelProps {
  serviceState: ServiceState;
  transitHealth: TransitHealth | null;
}

export default function HeroPanel({ serviceState, transitHealth }: HeroPanelProps) {
  return (
    <section className="hero panel">
      <div className="hero__copy">
        <div className="hero__eyebrow">Transit Sentinel</div>
        <h1 className="hero__title">Public feed signals into operator-ready priorities.</h1>
        <p className="hero__summary">
          GTFS schedules, vehicle positions, trip updates, and alerts become corridor
          risk, evidence, replay traces, and recommended control actions.
        </p>
        <div className="hero__chips">
          <span className="chip">Public-data proof</span>
          <span className="chip">Replayable incidents</span>
          <span className="chip">Operator action queue</span>
        </div>
      </div>
      <div className="hero__rail">
        <div className={`status-pill status-pill--${serviceTone(serviceState)}`}>
          <span className="status-pill__dot" />
          API {serviceState}
        </div>
        <div className="hero__meta">
          <span>Feed source</span>
          <strong>{transitHealth?.feed_status?.collection_source ?? "awaiting feed"}</strong>
        </div>
        <div className="hero__meta">
          <span>Last feed tick</span>
          <strong>{relativeTime(transitHealth?.feed_status?.updated_at)}</strong>
        </div>
        <div className="hero__meta">
          <span>Visible vehicles</span>
          <strong>{transitHealth?.feed_status?.vehicle_count ?? 0}</strong>
        </div>
        <div className="hero__meta">
          <span>Active alerts</span>
          <strong>{transitHealth?.feed_status?.alert_count ?? 0}</strong>
        </div>
      </div>
    </section>
  );
}
