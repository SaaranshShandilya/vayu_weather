import { useEffect, useMemo, useState } from "react";
import type { ResultData } from "./types";
import { chartAxisTitle } from "./chartMetadata";
import { ColorBar, tickLabels } from "./ColorBar";
import { composeFigure, downloadPng, slugify } from "./exportImage";
import { usePanZoom } from "./usePanZoom";
import { ZoomControls, ZoomHint } from "./ZoomControls";

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
// The two ends of colorFor's ramp, for the colour bar.
const CHOROPLETH_COLORS = ["#e0e8f6", "#274687"];
const FULL_MAP = { x: 0, y: 0, width: WIDTH, height: HEIGHT };

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

/** The SVG as a standalone image, with the CSS-driven styles written inline. */
async function rasterise(svg: SVGSVGElement): Promise<HTMLImageElement> {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(WIDTH));
  clone.setAttribute("height", String(HEIGHT));
  const ground = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  ground.setAttribute("width", String(WIDTH));
  ground.setAttribute("height", String(HEIGHT));
  ground.setAttribute("fill", "#f6f5f0");
  clone.insertBefore(ground, clone.firstChild);
  clone.querySelectorAll("path").forEach((path) => {
    path.setAttribute("stroke", "#a7a69f");
    path.setAttribute("stroke-width", "0.75");
    path.setAttribute("vector-effect", "non-scaling-stroke");
  });
  clone.querySelectorAll("circle").forEach((circle) => {
    circle.setAttribute("stroke", "rgba(252, 251, 248, 0.88)");
    circle.setAttribute("stroke-width", "0.8");
    circle.setAttribute("opacity", "0.88");
    circle.setAttribute("vector-effect", "non-scaling-stroke");
  });
  clone.querySelectorAll("title").forEach((node) => node.remove());
  // A data: URL, because the app's Content Security Policy allows data: images
  // but not blob: ones.
  const image = new Image();
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    new XMLSerializer().serializeToString(clone),
  )}`;
  await image.decode();
  return image;
}

function format(value: number) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
}

export function GeoMap({ result }: { result: ResultData }) {
  const [geography, setGeography] = useState<FeatureCollection | null>(null);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exportError, setExportError] = useState("");
  const panZoom = usePanZoom<SVGSVGElement>(WIDTH, HEIGHT, FULL_MAP);
  const { k, tx, ty } = panZoom.view;

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
  const pointsOnly = isPoints && !result.rows.some((row) => numeric(row[valueKey]) !== null);

  async function savePng() {
    const svg = panZoom.bind.ref.current;
    if (!svg) return;
    setSaving(true);
    setExportError("");
    try {
      const image = await rasterise(svg);
      const figure = composeFigure(
        {
          subtitle: `${result.rows.length.toLocaleString("en-IN")} ${isPoints ? "stations" : "states"}`,
          draw: (context) => context.drawImage(image, 0, 0, WIDTH, HEIGHT),
        },
        {
          width: WIDTH,
          height: HEIGHT,
          title: result.title || "Map",
          legend: pointsOnly
            ? { kind: "points", color: "#36589f", title: "Monitoring station" }
            : {
                kind: "gradient",
                colors: CHOROPLETH_COLORS,
                minimum: format(minimum),
                maximum: format(maximum),
                title: legendTitle,
                ticks: tickLabels(minimum, maximum, format),
              },
          source:
            "Source: CPCB station registry and analytical tables; state boundaries are a visual guide. VayuChat · IIT Gandhinagar.",
          scale: 2,
        },
      );
      await downloadPng(figure, `${slugify(result.title || "map")}.png`);
    } catch {
      setExportError("The map image could not be created.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <figure className="analysis-chart geo-map" aria-label={result.title || "Map"}>
      <div className="geo-map__header">
        {result.title ? <figcaption>{result.title}</figcaption> : <span />}
        <div className="map-download" role="group" aria-label="Download map">
          <button type="button" onClick={() => void savePng()} disabled={saving}>
            {saving ? "Saving…" : "Download PNG"}
          </button>
          {exportError ? <span className="map-download__error" role="alert">{exportError}</span> : null}
        </div>
      </div>
      <div className="geo-map__frame map-zoomable">
        <svg
          {...panZoom.bind}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className={panZoom.zoomed ? "is-zoomed" : undefined}
          role="img"
          aria-label={`${result.title || "Map"}. Use plus, minus and arrow keys to zoom and pan.`}
        >
          <g transform={`translate(${tx} ${ty}) scale(${k})`}>
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
                    // Dots keep their screen size, so zooming separates them.
                    r={(value === null ? 2.4 : 3.1) / Math.sqrt(k)}
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
          </g>
        </svg>
        <ZoomControls
          zoom={k}
          zoomed={panZoom.zoomed}
          onZoomIn={panZoom.zoomIn}
          onZoomOut={panZoom.zoomOut}
          onReset={panZoom.reset}
        />
      </div>
      <div className="geo-map__legend">
        {pointsOnly ? (
          <span><i className="point-swatch" /> Monitoring station</span>
        ) : (
          <ColorBar
            colors={CHOROPLETH_COLORS}
            minimum={minimum}
            maximum={maximum}
            title={legendTitle}
            format={format}
          />
        )}
        <ZoomHint />
      </div>
    </figure>
  );
}
