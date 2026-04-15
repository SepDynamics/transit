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
        <div className="hero__eyebrow">Transit Sentinel operations engine</div>
        <h1 className="hero__title">Turn live transit feeds into ranked decisions.</h1>
        <p className="hero__summary">
          A live data plane for ingesting GTFS and GTFS-realtime feeds,
          scoring route health, preserving the operating record, and serving
          decision-ready APIs.
        </p>
        <div className="hero__chips">
          <span className="chip">GTFS / GTFS-RT ingest</span>
          <span className="chip">Route risk scoring</span>
          <span className="chip">Bounded live API</span>
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
