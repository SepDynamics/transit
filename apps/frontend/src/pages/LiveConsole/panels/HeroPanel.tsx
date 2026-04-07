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
        <h1 className="hero__title">Detect corridor instability before service fully collapses.</h1>
        <p className="hero__summary">
          GTFS schedule and GTFS-RT feeds are normalized into route, corridor, and vehicle signals,
          then translated into operator-facing actions.
        </p>
        <div className="hero__chips">
          <span className="chip">GTFS static schedule</span>
          <span className="chip">GTFS-RT vehicle feeds</span>
          <span className="chip">Operator actions</span>
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
