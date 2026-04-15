import type { TransitHealth } from "../../../types/transit";

interface LiveFooterPanelProps {
  transitHealth: TransitHealth | null;
}

export default function LiveFooterPanel({ transitHealth }: LiveFooterPanelProps) {
  const vehicleCount =
    transitHealth?.feed_status?.vehicle_count ?? transitHealth?.vehicle_count ?? 0;
  const routeCount = transitHealth?.visible_line_count ?? transitHealth?.line_count ?? 0;
  const alertCount = transitHealth?.feed_status?.alert_count ?? 0;

  return (
    <footer className="live-footer panel" aria-label="Live data footer">
      <div>
        Watching public MBTA feed <span aria-hidden="true">•</span> Updated every
        30 seconds <span aria-hidden="true">•</span> Matches MBTA Performance
        Metrics
      </div>
      <strong>
        {vehicleCount} vehicles <span aria-hidden="true">•</span> {routeCount} routes{" "}
        <span aria-hidden="true">•</span> {alertCount} alerts
      </strong>
    </footer>
  );
}
