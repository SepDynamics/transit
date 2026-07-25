import { lazy, Suspense } from "react";
import type { TransitMapResponse } from "../../../types/transit";

const TransitMap = lazy(() => import("../../../components/TransitMap"));

interface MapSectionProps {
  mapData: TransitMapResponse | null;
}

export default function MapSection({ mapData }: MapSectionProps) {
  return (
    <section className="section panel">
      <div className="section__header">
        <div>
          <h2 className="section__title">Live map</h2>
          <p className="section__hint">
            Vehicles are colored by route health. Click a vehicle for details.
          </p>
        </div>
      </div>
      <div className="map-container">
        <Suspense fallback={<div className="empty-state">Loading map...</div>}>
          <TransitMap
            mapData={mapData}
            defaultCenter={[-118.2437, 34.0522]}
            defaultZoom={11}
            style={{ width: "100%", height: "480px", borderRadius: 8, overflow: "hidden" }}
          />
        </Suspense>
      </div>
    </section>
  );
}
