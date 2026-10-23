import { useEffect, useMemo, useState } from "react";
import type { GridPayload } from "./types";
import {
  composeFigure,
  downloadGif,
  downloadPng,
  slugify,
  type ExportFrame,
  type ExportLegend,
} from "./exportImage";
import { ColorBar, tickLabels, type Marker } from "./ColorBar";
import { usePanZoom, type View } from "./usePanZoom";
import { ZoomControls, ZoomHint } from "./ZoomControls";

const WIDTH = 720;
const MARGIN = { top: 8, right: 8, bottom: 28, left: 52 };
const FRAME_MS = 450;

// Continuous palettes for weather fields, low to high.
const PALETTES: Record<string, string[]> = {
  temperature: ["#313695", "#4575b4", "#74add1", "#abd9e9", "#fee090", "#fdae61", "#f46d43", "#d73027", "#a50026"],
  rainfall: ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
  humidity: ["#8c510a", "#d8b365", "#f6e8c3", "#c7eae5", "#5ab4ac", "#01665e"],
  wind: ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
  pblh: ["#ffffcc", "#c2e699", "#78c679", "#31a354", "#006837"],
  radiation: ["#fff5eb", "#fdd0a2", "#fd8d3c", "#d94801", "#7f2704"],
  pressure: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  days: ["#ecf0f7", "#9fb3d9", "#274687"],
  hours: ["#ecf0f7", "#9fb3d9", "#274687"],
  pollutant: ["#fff7ec", "#fdd49e", "#fc8d59", "#d7301f", "#7f0000"],
  aod: ["#ffffe5", "#fee391", "#fe9929", "#cc4c02", "#662506"],
  pm25: ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026", "#3f0011"],
  pm10: ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026", "#3f0011"],
};

// Reference lines on the PM colour bars: CPCB 24-hour standards.
const STANDARD_MARKERS: Record<string, Marker[]> = {
  pm25: [{ value: 60, label: "CPCB 24-hour standard, 60 µg/m³" }],
  pm10: [{ value: 100, label: "CPCB 24-hour standard, 100 µg/m³" }],
};

function hexChannels(hex: string) {
  return [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
}

function paletteColor(stops: string[], fraction: number) {
  const position = Math.max(0, Math.min(1, fraction)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(position));
  const local = position - index;
  const from = hexChannels(stops[index]!);
  const to = hexChannels(stops[index + 1]!);
  const channel = (k: number) => Math.round(from[k]! + (to[k]! - from[k]!) * local);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

type Ring = [number, number][];
let boundaryRings: Promise<Ring[]> | null = null;

// The state layer is only a visual guide; values never come from it.
function loadBoundaries(): Promise<Ring[]> {
  boundaryRings ??= fetch("/india-states.geojson")
    .then((response) => (response.ok ? response.json() : { features: [] }))
    .then((collection: { features: Array<{ geometry: { type: string; coordinates: unknown } }> }) =>
      collection.features.flatMap((feature) => {
        const polygons =
          feature.geometry.type === "Polygon"
            ? [feature.geometry.coordinates as Ring[]]
            : (feature.geometry.coordinates as Ring[][]);
        return polygons.flat();
      }),
    )
    .catch(() => []);
  return boundaryRings;
}

function tickStep(span: number) {
  for (const candidate of [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]) {
    if (span / candidate <= 8) return candidate;
  }
  return 10;
}

function format(value: number, digits = 0) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(value);
}

export function GridHeatmap({ grid, title }: { grid: GridPayload; title: string }) {
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState("");
  const [hover, setHover] = useState<{ lat: number; lon: number; value: number | null } | null>(
    null,
  );

  const ny = grid.latitudes.length;
  const nx = grid.longitudes.length;
  const step = grid.resolution_degrees || 0.02;
  const lat0 = grid.latitudes[0]! - step / 2;
  const lat1 = grid.latitudes[ny - 1]! + step / 2;
  const lon0 = grid.longitudes[0]! - step / 2;
  const lon1 = grid.longitudes[nx - 1]! + step / 2;
  // Keep the map true to shape: a degree of longitude is shorter than a degree
  // of latitude at Delhi's latitude.
  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const aspect =
    (lat1 - lat0) / ((lon1 - lon0) * Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180));
  const plotHeight = Math.round(Math.min(plotWidth * aspect, 640));
  const height = plotHeight + MARGIN.top + MARGIN.bottom;
  const plotBox = useMemo(
    () => ({ x: MARGIN.left, y: MARGIN.top, width: plotWidth, height: plotHeight }),
    [plotWidth, plotHeight],
  );
  const panZoom = usePanZoom<HTMLCanvasElement>(WIDTH, height, plotBox);
  const { k, tx, ty } = panZoom.view;
  // The coordinate under a canvas point, in the current view.
  const lonAt = (px: number) =>
    lon0 + (((px - tx) / k - MARGIN.left) / plotWidth) * (lon1 - lon0);
  const latAt = (py: number) =>
    lat1 - (((py - ty) / k - MARGIN.top) / plotHeight) * (lat1 - lat0);

  const frame = grid.frames[Math.min(frameIndex, grid.frames.length - 1)]!;
  const isCount = grid.scale === "days" || grid.scale === "hours";
  const palette = PALETTES[grid.scale]!;
  const markers = STANDARD_MARKERS[grid.scale] ?? [];
  // Optical depth has no unit and lives around 0.1-1: two decimals.
  const digits = grid.unit ? 1 : 2;
  const [boundaries, setBoundaries] = useState<Ring[]>([]);

  // One colour range across every frame, so an animation is comparable.
  const range = useMemo(() => {
    let minimum = Infinity;
    let maximum = -Infinity;
    for (const f of grid.frames) {
      for (const v of f.values) {
        if (v === null) continue;
        if (v < minimum) minimum = v;
        if (v > maximum) maximum = v;
      }
    }
    if (!Number.isFinite(minimum)) return { minimum: 0, maximum: 1 };
    if (grid.scale === "rainfall" || isCount) minimum = 0;
    if (isCount && grid.scale_max) maximum = grid.scale_max;
    return { minimum, maximum: Math.max(maximum, minimum + 1e-6) };
  }, [grid, isCount]);

  const colorOf = (value: number) =>
    paletteColor(palette, (value - range.minimum) / (range.maximum - range.minimum));

  useEffect(() => {
    if (!grid.boundaries) return;
    let active = true;
    void loadBoundaries().then((rings) => {
      if (active) setBoundaries(rings);
    });
    return () => {
      active = false;
    };
  }, [grid.boundaries]);

  useEffect(() => {
    if (!playing || grid.frames.length < 2) return;
    const timer = window.setInterval(
      () => setFrameIndex((current) => (current + 1) % grid.frames.length),
      FRAME_MS,
    );
    return () => window.clearInterval(timer);
  }, [playing, grid.frames.length]);

  /** Draws one frame's values in a given zoom/pan view: screen and exports share it. */
  function drawMap(
    context: CanvasRenderingContext2D,
    values: Array<number | null>,
    view: View,
    // Exports paint a ground: clearing would leave transparent (black in a GIF) margins.
    ground: string | null = null,
  ) {
    const x = (lon: number) =>
      (MARGIN.left + ((lon - lon0) / (lon1 - lon0)) * plotWidth) * view.k + view.tx;
    const y = (lat: number) =>
      (MARGIN.top + ((lat1 - lat) / (lat1 - lat0)) * plotHeight) * view.k + view.ty;
    const traceRings = (rings: Ring[]) => {
      context.beginPath();
      for (const ring of rings) {
        ring.forEach(([lon, lat], index) => {
          if (index === 0) context.moveTo(x(lon), y(lat));
          else context.lineTo(x(lon), y(lat));
        });
        context.closePath();
      }
    };

    if (ground) {
      context.fillStyle = ground;
      context.fillRect(0, 0, WIDTH, height);
    } else {
      context.clearRect(0, 0, WIDTH, height);
    }
    // Cells, outlines and labels stay inside the plot area when zoomed.
    context.save();
    context.beginPath();
    context.rect(MARGIN.left, MARGIN.top, plotWidth, plotHeight);
    context.clip();
    const cellWidth = (plotWidth / nx) * view.k;
    const cellHeight = (plotHeight / ny) * view.k;
    for (let i = 0; i < ny; i += 1) {
      const top = y(grid.latitudes[i]! + step / 2);
      if (top > MARGIN.top + plotHeight || top + cellHeight < MARGIN.top) continue;
      for (let j = 0; j < nx; j += 1) {
        const left = x(grid.longitudes[j]! - step / 2);
        if (left > MARGIN.left + plotWidth || left + cellWidth < MARGIN.left) continue;
        const value = values[i * nx + j];
        context.fillStyle = value === null || value === undefined ? "#e8e7e1" : colorOf(value);
        context.fillRect(left, top, cellWidth + 0.6, cellHeight + 0.6);
      }
    }

    if (boundaries.length) {
      traceRings(boundaries);
      context.lineWidth = 0.7;
      context.strokeStyle = "rgba(32, 33, 31, 0.45)";
      context.stroke();
    }

    // The place asked about: a dark line on a white halo, readable on any colour.
    if (grid.outline.length) {
      traceRings(grid.outline as Ring[]);
      context.lineJoin = "round";
      context.lineWidth = 4;
      context.strokeStyle = "rgba(255, 255, 255, 0.85)";
      context.stroke();
      context.lineWidth = 1.8;
      context.strokeStyle = "#20211f";
      context.stroke();
    }

    context.font = "600 12px system-ui, sans-serif";
    context.lineJoin = "round";
    for (const landmark of grid.landmarks) {
      const px = x(landmark.longitude);
      const py = y(landmark.latitude);
      context.beginPath();
      context.arc(px, py, 3.5, 0, Math.PI * 2);
      context.fillStyle = "#ffffff";
      context.fill();
      context.lineWidth = 1.4;
      context.strokeStyle = "#20211f";
      context.stroke();
      context.lineWidth = 3;
      context.strokeStyle = "rgba(32, 33, 31, 0.78)";
      context.strokeText(landmark.name, px + 6, py + 4);
      context.fillStyle = "#ffffff";
      context.fillText(landmark.name, px + 6, py + 4);
    }
    context.restore();

    context.fillStyle = "#62625e";
    context.font = "11px system-ui, sans-serif";
    // Ticks follow the visible part of the map.
    const lonOf = (px: number) =>
      lon0 + (((px - view.tx) / view.k - MARGIN.left) / plotWidth) * (lon1 - lon0);
    const latOf = (py: number) =>
      lat1 - (((py - view.ty) / view.k - MARGIN.top) / plotHeight) * (lat1 - lat0);
    const visibleLon0 = lonOf(MARGIN.left);
    const visibleLon1 = lonOf(MARGIN.left + plotWidth);
    const visibleLat0 = latOf(MARGIN.top + plotHeight);
    const visibleLat1 = latOf(MARGIN.top);
    const span = Math.max(visibleLat1 - visibleLat0, visibleLon1 - visibleLon0);
    const stepSize = tickStep(span);
    const digits = stepSize >= 1 ? 0 : stepSize >= 0.1 ? 1 : 2;
    const ticks = (from: number, to: number) => {
      const marks = [];
      for (let v = Math.ceil(from / stepSize) * stepSize; v <= to + 1e-9; v += stepSize) {
        marks.push(Number(v.toFixed(2)));
      }
      return marks;
    };
    context.textAlign = "right";
    for (const lat of ticks(visibleLat0, visibleLat1)) {
      context.fillText(`${lat.toFixed(digits)}°N`, MARGIN.left - 6, y(lat) + 4);
    }
    context.textAlign = "center";
    for (const lon of ticks(visibleLon0, visibleLon1)) {
      context.fillText(`${lon.toFixed(digits)}°E`, x(lon), height - 10);
    }
    context.textAlign = "left";
  }

  useEffect(() => {
    const context = panZoom.bind.ref.current?.getContext("2d");
    if (context) drawMap(context, frame.values, panZoom.view);
  });

  function onMove(event: React.MouseEvent<HTMLCanvasElement>) {
    const point = panZoom.toDrawing(event.clientX, event.clientY);
    const outsidePlot =
      point.x < MARGIN.left ||
      point.x > MARGIN.left + plotWidth ||
      point.y < MARGIN.top ||
      point.y > MARGIN.top + plotHeight;
    if (outsidePlot) {
      setHover(null);
      return;
    }
    const lon = lonAt(point.x);
    const lat = latAt(point.y);
    const j = Math.round((lon - grid.longitudes[0]!) / step);
    const i = Math.round((lat - grid.latitudes[0]!) / step);
    if (i < 0 || j < 0 || i >= ny || j >= nx) {
      setHover(null);
      return;
    }
    setHover({
      lat: grid.latitudes[i]!,
      lon: grid.longitudes[j]!,
      value: frame.values[i * nx + j] ?? null,
    });
  }

  const legendTitle = isCount
    ? grid.value_label
    : `${grid.value_label}${grid.unit ? ` (${grid.unit})` : ""}`;
  const isCams = grid.domain === "cams";
  const formatLegend = (value: number) => format(value, isCount ? 0 : digits);
  const span = range.maximum - range.minimum || 1;
  const exportLegend: ExportLegend = {
    kind: "gradient",
    colors: palette,
    minimum: formatLegend(range.minimum),
    maximum: formatLegend(range.maximum),
    title: legendTitle,
    ticks: tickLabels(range.minimum, range.maximum, formatLegend),
    markers: markers
      .filter((marker) => marker.value >= range.minimum && marker.value <= range.maximum)
      .map((marker) => ({ fraction: (marker.value - range.minimum) / span, label: marker.label })),
  };
  const exportOptions = {
    width: WIDTH,
    height,
    title: title || grid.value_label,
    legend: exportLegend,
    source: isCams
      ? `Source: ${grid.source_label}. Contains modified Copernicus Atmosphere Monitoring Service information (CC-BY-4.0). Model forecast, not observation. VayuChat · IIT Gandhinagar.`
      : `Source: ${grid.source_label}. WRF model simulation, not observation. VayuChat · IIT Gandhinagar.`,
  };
  const exportFrame = (index: number): ExportFrame => {
    const shown = grid.frames[index]!;
    return {
      subtitle: [shown.label, grid.domain_label && `Domain ${grid.domain_label}`]
        .filter(Boolean)
        .join(" · "),
      draw: (context) => drawMap(context, shown.values, panZoom.view, "#ffffff"),
    };
  };

  async function savePng() {
    setExporting("png");
    setExportError("");
    try {
      const figure = composeFigure(exportFrame(frameIndex), { ...exportOptions, scale: 2 });
      const suffix = grid.frames.length > 1 ? `-${slugify(frame.label)}` : "";
      await downloadPng(figure, `${slugify(title)}${suffix}.png`);
    } catch {
      setExportError("The map image could not be created.");
    } finally {
      setExporting(null);
    }
  }

  async function saveGif() {
    setPlaying(false);
    setExporting("gif 0%");
    setExportError("");
    try {
      await downloadGif(
        grid.frames.map((_, index) => exportFrame(index)),
        { ...exportOptions, delay: FRAME_MS },
        `${slugify(title)}.gif`,
        (done, total) => setExporting(`gif ${Math.round((done / total) * 100)}%`),
      );
    } catch {
      setExportError("The animation could not be encoded.");
    } finally {
      setExporting(null);
    }
  }

  const [days, sourceNy, sourceNx] = grid.source_shape;
  const sourceValues = grid.source_shape.reduce((product, size) => product * size, 1);

  return (
    <figure className="analysis-chart grid-heatmap" aria-label={title || "Gridded weather map"}>
      {title ? <figcaption>{title}</figcaption> : null}
      <div className="grid-heatmap__header">
        <p className="chart-dimensions">
          {grid.domain ? (
            <span className="domain-chip" title={`WRF domain ${grid.domain_label}`}>
              {grid.domain_label || grid.domain}
            </span>
          ) : null}
          Latitude <span aria-hidden="true">×</span> longitude grid · {ny} × {nx} cells of{" "}
          {grid.resolution_degrees}°
          {grid.outline_label ? ` · outlined: ${grid.outline_label}` : ""}
        </p>
        <div className="map-download" role="group" aria-label="Download map">
          <button type="button" onClick={() => void savePng()} disabled={exporting !== null}>
            {exporting === "png" ? "Saving…" : grid.frames.length > 1 ? "PNG (this frame)" : "Download PNG"}
          </button>
          {grid.frames.length > 1 ? (
            <button type="button" onClick={() => void saveGif()} disabled={exporting !== null}>
              {exporting?.startsWith("gif") ? `Encoding ${exporting.slice(4)}` : "Download GIF"}
            </button>
          ) : null}
          {exportError ? <span className="map-download__error" role="alert">{exportError}</span> : null}
        </div>
      </div>
      <div className="grid-heatmap__frame map-zoomable">
        <canvas
          {...panZoom.bind}
          width={WIDTH}
          height={height}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          className={panZoom.zoomed ? "is-zoomed" : undefined}
          role="img"
          aria-label={`${grid.value_label} heatmap. Use plus, minus and arrow keys to zoom and pan.`}
        />
        <ZoomControls
          zoom={k}
          zoomed={panZoom.zoomed}
          onZoomIn={panZoom.zoomIn}
          onZoomOut={panZoom.zoomOut}
          onReset={panZoom.reset}
        />
      </div>
      {grid.frames.length > 1 ? (
        <div className="grid-heatmap__controls">
          <button type="button" onClick={() => setPlaying((value) => !value)}>
            {playing ? "Pause" : "Play"}
          </button>
          <input
            type="range"
            min={0}
            max={grid.frames.length - 1}
            value={frameIndex}
            onChange={(event) => {
              setPlaying(false);
              setFrameIndex(Number(event.target.value));
            }}
            aria-label="Period"
          />
          <span>{frame.label}</span>
        </div>
      ) : null}
      <div className="grid-heatmap__footer">
        <span className="grid-heatmap__readout" aria-live="polite">
          {hover
            ? `${hover.lat.toFixed(2)}°N, ${hover.lon.toFixed(2)}°E · ${
                hover.value === null ? "no value" : `${format(hover.value, digits)} ${grid.unit}`.trim()
              }`
            : `Hover a cell to read its value`}
        </span>
        <ZoomHint />
        <ColorBar
          colors={palette}
          minimum={range.minimum}
          maximum={range.maximum}
          title={legendTitle}
          format={formatLegend}
          markers={markers}
        />
      </div>
      <p className="chart-note">
        Computed with xarray on the {grid.source_label} ({days} {grid.time_unit ?? "days"} ×{" "}
        {sourceNy} × {sourceNx} ={" "}
        {format(sourceValues)} values per variable). Flattened, that would be one table row per
        value.
        {grid.boundaries ? " State outlines are a visual guide only." : ""}
        {markers.length ? " The dashed mark is a 24-hour standard; hourly values are for reference." : ""}
        {isCams ? " Contains modified Copernicus Atmosphere Monitoring Service information." : ""}
        {" Downloads show the current zoom."}
      </p>
    </figure>
  );
}
