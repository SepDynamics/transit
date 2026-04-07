/**
 * TransitMap — Geographic map view for Transit Sentinel.
 *
 * Uses MapLibre GL (open-source, no API key required) to render:
 *  - Vehicle positions as colored circles keyed by hazard regime
 *  - Direction arrows (bearing) on vehicles
 *  - Corridor incident markers
 *  - A compact popup on vehicle click
 *
 * The map uses the free OpenStreetMap-backed Protomaps basemap tile style.
 * This can be swapped for any MapLibre-compatible style URL.
 */
import { useEffect, useRef, useCallback } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type {
  TransitMapResponse,
  VehicleMapFeature,
  CorridorMapFeature,
} from "../types/transit";
import { formatRegimeLabel } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Regime → color mapping (matches the LiveConsole CSS badge colors)
// ---------------------------------------------------------------------------
const REGIME_COLORS: Record<string, string> = {
  healthy: "#22c55e",
  bunching_onset: "#f59e0b",
  headway_collapse: "#ef4444",
  terminal_congestion: "#f97316",
  stop_dwell_instability: "#a855f7",
  corridor_unstable: "#ec4899",
  service_degraded: "#dc2626",
  feed_incoherent: "#6b7280",
  unknown: "#3b82f6",
};

function regimeColor(regime: string | null | undefined): string {
  return REGIME_COLORS[regime ?? ""] ?? REGIME_COLORS.unknown;
}

// ---------------------------------------------------------------------------
// Free basemap tile style (OSM via Protomaps tiles.openfreemap.org)
// No API key required. Swap with your own MapLibre style if needed.
// ---------------------------------------------------------------------------
const BASEMAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [
        "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: "osm-tiles",
      type: "raster",
      source: "osm",
      minzoom: 0,
      maxzoom: 22,
    },
  ],
};

// ---------------------------------------------------------------------------
// GeoJSON helpers
// ---------------------------------------------------------------------------

function buildVehicleGeoJSON(
  features: VehicleMapFeature[]
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.map((f) => ({
      ...f,
      properties: {
        ...f.properties,
        _color: regimeColor(f.properties.regime),
        _radius: 6,
      },
    })),
  };
}

function buildCorridorGeoJSON(
  features: CorridorMapFeature[]
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.map((feature) => ({
      ...feature,
      properties: {
        ...feature.properties,
        _color:
          feature.properties._color ?? regimeColor(feature.properties.regime),
      },
    })),
  };
}

function extendBoundsWithGeometry(
  bounds: maplibregl.LngLatBounds,
  geometry: GeoJSON.Geometry | null | undefined
): boolean {
  if (!geometry) return false;
  if (geometry.type === "Point") {
    const [lng, lat] = geometry.coordinates;
    bounds.extend([lng, lat]);
    return true;
  }
  if (geometry.type === "LineString") {
    geometry.coordinates.forEach(([lng, lat]) => bounds.extend([lng, lat]));
    return geometry.coordinates.length > 0;
  }
  return false;
}

function buildDataBounds(
  mapData: TransitMapResponse | null
): maplibregl.LngLatBounds | null {
  if (!mapData) return null;
  const bounds = new maplibregl.LngLatBounds();
  let hasCoordinates = false;
  (mapData.corridor_features ?? []).forEach((feature) => {
    hasCoordinates =
      extendBoundsWithGeometry(bounds, feature.geometry) || hasCoordinates;
  });
  (mapData.vehicle_features ?? []).forEach((feature) => {
    hasCoordinates =
      extendBoundsWithGeometry(bounds, feature.geometry) || hasCoordinates;
  });
  return hasCoordinates ? bounds : null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TransitMapProps {
  mapData: TransitMapResponse | null;
  /** Boston or LA default center */
  defaultCenter?: [number, number];
  defaultZoom?: number;
  className?: string;
  style?: React.CSSProperties;
}

export default function TransitMap({
  mapData,
  defaultCenter = [-71.0589, 42.3601], // Boston (MBTA)
  defaultZoom = 11,
  className,
  style,
}: TransitMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const initializedRef = useRef(false);
  const lastAutoFitKeyRef = useRef<string>("");

  // ---------------------------------------------------------------------------
  // Initialise map once
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || initializedRef.current) return;
    initializedRef.current = true;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      center: defaultCenter,
      zoom: defaultZoom,
      attributionControl: { compact: true },
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "imperial" as const }), "bottom-left");

    map.on("load", () => {
      map.addSource("corridors", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "corridors-outline",
        type: "line",
        source: "corridors",
        layout: {
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": "rgba(15,23,42,0.28)",
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            8,
            4,
            12,
            7,
            16,
            11,
          ],
          "line-opacity": 0.55,
        },
      });

      map.addLayer({
        id: "corridors-line",
        type: "line",
        source: "corridors",
        layout: {
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": ["get", "_color"],
          "line-width": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "hazard_score"], 0],
            0,
            2,
            0.45,
            4,
            0.8,
            6,
          ],
          "line-opacity": [
            "case",
            ["==", ["get", "activity_status"], "inactive"],
            0.22,
            ["==", ["get", "activity_status"], "scheduled_later"],
            0.4,
            0.72,
          ],
        },
      });

      // Vehicle positions source
      map.addSource("vehicles", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      // Vehicle circles
      map.addLayer({
        id: "vehicles-circle",
        type: "circle",
        source: "vehicles",
        paint: {
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            8, 4,
            12, 8,
            16, 14,
          ],
          "circle-color": ["get", "_color"],
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "rgba(0,0,0,0.4)",
          "circle-opacity": 0.92,
        },
      });

      // Vehicle labels (route short name / vehicle ID)
      map.addLayer({
        id: "vehicles-label",
        type: "symbol",
        source: "vehicles",
        minzoom: 12,
        layout: {
          "text-field": ["coalesce", ["get", "label"], ["get", "route_id"], ""],
          "text-size": 10,
          "text-offset": [0, 1.4],
          "text-anchor": "top",
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": "#fff",
          "text-halo-color": "rgba(0,0,0,0.7)",
          "text-halo-width": 1,
        },
      });

      mapRef.current = map;
    });

    // ---------------------------------------------------------------------------
    // Click popup on vehicles
    // ---------------------------------------------------------------------------
    map.on("click", "vehicles-circle", (e) => {
      if (!e.features || e.features.length === 0) return;
      const feat = e.features[0];
      const props = feat.properties as Record<string, unknown>;
      const coords = (feat.geometry as GeoJSON.Point).coordinates.slice() as [
        number,
        number
      ];

      const delayS = typeof props.delay_seconds === "number" ? props.delay_seconds : null;
      const delayStr =
        delayS === null
          ? "n/a"
          : delayS >= 0
          ? `+${Math.round(delayS)}s`
          : `${Math.round(delayS)}s`;
      const regime = typeof props.regime === "string" ? props.regime : "unknown";
      const regimeLabel = formatRegimeLabel(regime);
      const color = regimeColor(regime);
      const hazard =
        typeof props.hazard_score === "number"
          ? props.hazard_score.toFixed(2)
          : "n/a";

      const html = `
        <div style="font-size:12px;line-height:1.6;min-width:160px;">
          <div style="font-weight:700;font-size:13px;margin-bottom:4px;">
            ${props.label ?? props.route_id ?? props.vehicle_id ?? "Vehicle"}
          </div>
          <div>Vehicle: <b>${props.vehicle_id ?? "—"}</b></div>
          <div>Route: <b>${props.route_id ?? "—"}</b></div>
          <div>Delay: <b>${delayStr}</b></div>
          <div>
            Service state:&nbsp;
            <span style="background:${color};color:#fff;padding:1px 5px;border-radius:3px;font-size:11px;">
              ${regimeLabel}
            </span>
          </div>
          <div>Risk score: <b>${hazard}</b></div>
          <div>Status: <b>${props.current_status ?? "—"}</b></div>
        </div>
      `;

      if (popupRef.current) popupRef.current.remove();
      popupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" })
        .setLngLat(coords)
        .setHTML(html)
        .addTo(map);
    });

    map.on("mouseenter", "vehicles-circle", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "vehicles-circle", () => {
      map.getCanvas().style.cursor = "";
    });

    return () => {
      map.remove();
      mapRef.current = null;
      initializedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Update vehicle GeoJSON when mapData changes
  // ---------------------------------------------------------------------------
  const updateData = useCallback(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const vehicleSource = map.getSource("vehicles") as
      | maplibregl.GeoJSONSource
      | undefined;
    const corridorSource = map.getSource("corridors") as
      | maplibregl.GeoJSONSource
      | undefined;
    if (!vehicleSource || !corridorSource) return;
    const vehicleFeatures = mapData?.vehicle_features ?? [];
    const corridorFeatures = mapData?.corridor_features ?? [];
    corridorSource.setData(buildCorridorGeoJSON(corridorFeatures));
    vehicleSource.setData(buildVehicleGeoJSON(vehicleFeatures));

    const viewportKey = `${mapData?.scope ?? "all"}:${mapData?.trace_id ?? "live"}`;
    const bounds = buildDataBounds(mapData);
    if (bounds && lastAutoFitKeyRef.current !== viewportKey) {
      map.fitBounds(bounds, {
        padding: 56,
        duration: 900,
        maxZoom: 13,
      });
      lastAutoFitKeyRef.current = viewportKey;
    }
  }, [mapData]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.isStyleLoaded()) {
      updateData();
    } else {
      map.once("load", updateData);
    }
  }, [mapData, updateData]);

  // ---------------------------------------------------------------------------
  // Legend
  // ---------------------------------------------------------------------------
  const legendItems = Object.entries(REGIME_COLORS).filter(
    ([key]) => key !== "unknown"
  );

  return (
    <div
      className={className}
      style={{ position: "relative", ...(style ?? {}) }}
    >
      {/* Map container */}
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      {/* Stats overlay */}
      {mapData && (
        <div
          style={{
            position: "absolute",
            top: 10,
            left: 10,
            background: "rgba(15,23,42,0.85)",
            color: "#e2e8f0",
            padding: "6px 10px",
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.7,
            pointerEvents: "none",
          }}
        >
          <div>
            <b>{mapData.vehicle_count}</b> vehicles
          </div>
          <div>
            <b>{mapData.corridor_count}</b> corridors
          </div>
        </div>
      )}

      {/* Legend */}
      <div
        style={{
          position: "absolute",
          bottom: 32,
          right: 10,
          background: "rgba(15,23,42,0.85)",
          color: "#e2e8f0",
          padding: "8px 10px",
          borderRadius: 6,
          fontSize: 11,
          lineHeight: 2,
          pointerEvents: "none",
          maxWidth: 170,
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 2, fontSize: 12 }}>
          Regime
        </div>
        {legendItems.map(([regime, color]) => (
          <div key={regime} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: color,
                flexShrink: 0,
              }}
            />
            <span>{regime.replace(/_/g, " ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
