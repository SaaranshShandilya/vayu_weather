import { useEffect, useMemo, useState } from "react";
import type { ResultData } from "./types";
import { chartAxisTitle } from "./chartMetadata";

type Position = [number, number];
type Geometry = {
  type: "Polygon" | "MultiPolygon";
  coordinates: Position[][] | Position[][][];
};
type Feature = {
  type: "Feature";
  properties: { state: string };
  geometry: Geometry;
};
type FeatureCollection = {
  type: "FeatureCollection";
  features: Feature[];
};

const WIDTH = 760;
const HEIGHT = 620;
const PADDING = 18;

function project([longitude, latitude]: Position): Position {
  return [
    PADDING + ((longitude - 67.5) / 30.5) * (WIDTH - 2 * PADDING),
    PADDING + ((37.5 - latitude) / 31.5) * (HEIGHT - 2 * PADDING),
  ];
}

function ringPath(ring: Position[]) {
  return ring
    .map((point, index) => {
      const [x, y] = project(point);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ") + " Z";
}

function geometryPath(geometry: Geometry) {
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates as Position[][]]
      : (geometry.coordinates as Position[][][]);
  return polygons
    .flatMap((polygon) => polygon.map(ringPath))
    .join(" ");
}

function canonicalState(value: unknown) {
  const normalized = String(value ?? "").trim().toLocaleLowerCase();
  const aliases: Record<string, string> = {
    "andaman and nicobar": "andaman and nicobar islands",
    "jammu & kashmir": "jammu and kashmir",
    orissa: "odisha",
    uttaranchal: "uttarakhand",
    "nct of delhi": "delhi",
  };
  return aliases[normalized] ?? normalized;
}

function numeric(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function colorFor(value: number | null, minimum: number, maximum: number) {
  if (value === null) return "#ecebe6";
  const span = Math.max(maximum - minimum, 1e-9);
  const fraction = Math.max(0, Math.min(1, (value - minimum) / span));
  const start = [224, 232, 246];
  const end = [39, 70, 135];
  const channel = (index: number) =>
    Math.round(start[index]! + (end[index]! - start[index]!) * fraction);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function format(value: number) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
}

export function GeoMap({ result }: { result: ResultData }) {
  const [geography, setGeography] = useState<FeatureCollection | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    fetch("/india-states.geojson")
      .then((response) => {
        if (!response.ok) throw new Error("Boundary file unavailable");
        return response.json() as Promise<FeatureCollection>;
      })
      .then((payload) => {
        if (active) setGeography(payload);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const valueKey = result.y_keys.find((key) => key !== "latitude") ?? "value";
  const stateValues = useMemo(() => {
    const pairs = new Map<string, number>();
    result.rows.forEach((row) => {
      const value = numeric(row[valueKey]);
      if (value !== null) pairs.set(canonicalState(row[result.x_key]), value);
    });
    return pairs;
  }, [result.rows, result.x_key, valueKey]);
  const values = [...stateValues.values()];
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 1;
  const mappedValueKey = result.y_keys.find(
    (key) => key !== "latitude" && key !== "longitude",
  );
  const legendTitle = mappedValueKey
    ? chartAxisTitle(mappedValueKey)
    : "Monitoring stations";

  if (failed) {
    return <p className="map-error">The geographic boundary layer could not be loaded.</p>;
  }
  if (!geography) {
    return <div className="map-loading" aria-label="Loading map" />;
  }

  const isPoints = result.visualization === "map_points";
  return (
    <figure className="analysis-chart geo-map" aria-label={result.title || "Map"}>
      {result.title ? <figcaption>{result.title}</figcaption> : null}
      <div className="geo-map__frame">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img">
          <g className="geo-map__states">
            {geography.features.map((feature) => {
              const value = stateValues.get(canonicalState(feature.properties.state)) ?? null;
              return (
                <path
                  key={feature.properties.state}
                  d={geometryPath(feature.geometry)}
                  fill={
                    isPoints
                      ? "#f2f0ea"
                      : colorFor(value, minimum, maximum)
                  }
                >
                  <title>
                    {feature.properties.state}
                    {value === null ? ": no matching data" : `: ${format(value)}`}
                  </title>
                </path>
              );
            })}
          </g>
          {isPoints ? (
            <g className="geo-map__points">
              {result.rows.map((row, index) => {
                const latitude = numeric(row.latitude);
                const longitude = numeric(row.longitude);
                if (latitude === null || longitude === null) return null;
                const [x, y] = project([longitude, latitude]);
                const value = numeric(row[valueKey]);
                return (
                  <circle
                    key={`${String(row.site_id ?? row.station)}-${index}`}
                    cx={x}
                    cy={y}
                    r={value === null ? 2.4 : 3.1}
                    fill={value === null ? "#36589f" : colorFor(value, minimum, maximum)}
                  >
                    <title>
                      {String(row.station ?? "Station")} · {String(row.city ?? "")}
                      {value === null ? "" : ` · ${format(value)}`}
                    </title>
                  </circle>
                );
              })}
            </g>
          ) : null}
        </svg>
      </div>
      <div className="geo-map__legend">
        {isPoints && !result.rows.some((row) => numeric(row[valueKey]) !== null) ? (
          <span><i className="point-swatch" /> Monitoring station</span>
        ) : (
          <>
            <strong>{legendTitle}</strong>
            <span>{format(minimum)}</span>
            <i className="gradient-swatch" />
            <span>{format(maximum)}</span>
          </>
        )}
      </div>
    </figure>
  );
}
