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
        <div className="hero__eyebrow">Transit Sentinel for Boston</div>
        <h1 className="hero__title">Show leaders which bus routes need help first.</h1>
        <p className="hero__summary">
          Boston already wants shorter commutes and more reliable transit. This
          turns public MBTA data into plain answers: where buses are getting
          stuck, which fix to try, and how to prove it worked.
        </p>
        <div className="hero__chips">
          <span className="chip">Boston priority corridors</span>
          <span className="chip">Before and after proof</span>
          <span className="chip">No new hardware</span>
        </div>
      </div>
      <div className="hero__rail">
        <div className={`status-pill status-pill--${serviceTone(serviceState)}`}>
          <span className="status-pill__dot" />
          Data system {serviceState}
        </div>
        <div className="hero__meta">
          <span>Data source</span>
          <strong>{transitHealth?.feed_status?.collection_source ?? "awaiting feed"}</strong>
        </div>
        <div className="hero__meta">
          <span>Last update</span>
          <strong>{relativeTime(transitHealth?.feed_status?.updated_at)}</strong>
        </div>
        <div className="hero__meta">
          <span>Vehicles seen</span>
          <strong>{transitHealth?.feed_status?.vehicle_count ?? 0}</strong>
        </div>
        <div className="hero__meta">
          <span>Alerts seen</span>
          <strong>{transitHealth?.feed_status?.alert_count ?? 0}</strong>
        </div>
      </div>
    </section>
  );
}
