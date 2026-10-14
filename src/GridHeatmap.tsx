import { useEffect, useMemo, useRef, useState } from "react";
import type { GridPayload } from "./types";

const WIDTH = 720;
const MARGIN = { top: 8, right: 8, bottom: 28, left: 52 };

// CPCB National AQI sub-index breakpoints (µg/m³) and their category colours.
const AQI_BANDS: Record<"pm25" | "pm10", number[]> = {
  pm25: [30, 60, 90, 120, 250],
  pm10: [50, 100, 250, 350, 430],
};
const AQI_COLORS = ["#2f9e5b", "#9ccc4f", "#f2cf3c", "#f08c2e", "#d9412f", "#8e1d3b"];
const AQI_NAMES = ["Good", "Satisfactory", "Moderate", "Poor", "Very poor", "Severe"];

function hoursColor(value: number, maximum: number) {
  const fraction = Math.max(0, Math.min(1, value / Math.max(maximum, 1)));
  const start = [236, 240, 247];
  const end = [39, 70, 135];
  const channel = (index: number) =>
    Math.round(start[index]! + (end[index]! - start[index]!) * fraction);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function aqiIndex(value: number, scale: "pm25" | "pm10") {
  const index = AQI_BANDS[scale].findIndex((limit) => value <= limit);
  return index === -1 ? AQI_COLORS.length - 1 : index;
}

function format(value: number, digits = 0) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(value);
}

export function GridHeatmap({ grid, title }: { grid: GridPayload; title: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
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
  const x = (lon: number) => MARGIN.left + ((lon - lon0) / (lon1 - lon0)) * plotWidth;
  const y = (lat: number) => MARGIN.top + ((lat1 - lat) / (lat1 - lat0)) * plotHeight;

  const frame = grid.frames[Math.min(frameIndex, grid.frames.length - 1)]!;
  const hoursMax = useMemo(() => {
    if (grid.scale !== "hours") return 1;
    if (grid.scale_max) return grid.scale_max;
    return Math.max(1, ...grid.frames.flatMap((f) => f.values.filter((v): v is number => v !== null)));
  }, [grid]);

  const colorOf = (value: number) =>
    grid.scale === "hours" ? hoursColor(value, hoursMax) : AQI_COLORS[aqiIndex(value, grid.scale)]!;

  useEffect(() => {
    if (!playing || grid.frames.length < 2) return;
    const timer = window.setInterval(
      () => setFrameIndex((current) => (current + 1) % grid.frames.length),
      450,
    );
    return () => window.clearInterval(timer);
  }, [playing, grid.frames.length]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    context.clearRect(0, 0, WIDTH, height);
    const cellWidth = plotWidth / nx;
    const cellHeight = plotHeight / ny;
    for (let i = 0; i < ny; i += 1) {
      for (let j = 0; j < nx; j += 1) {
        const value = frame.values[i * nx + j];
        context.fillStyle = value === null || value === undefined ? "#e8e7e1" : colorOf(value);
        context.fillRect(
          x(grid.longitudes[j]! - step / 2),
          y(grid.latitudes[i]! + step / 2),
          cellWidth + 0.6,
          cellHeight + 0.6,
        );
      }
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

    context.fillStyle = "#62625e";
    context.font = "11px system-ui, sans-serif";
    const ticks = (from: number, to: number) => {
      const span = to - from;
      const tickStep = span > 1 ? 0.2 : span > 0.4 ? 0.1 : 0.05;
      const values = [];
      for (let v = Math.ceil(from / tickStep) * tickStep; v <= to + 1e-9; v += tickStep) {
        values.push(Number(v.toFixed(2)));
      }
      return values;
    };
    context.textAlign = "right";
    for (const lat of ticks(lat0, lat1)) {
      context.fillText(`${lat.toFixed(2)}°N`, MARGIN.left - 6, y(lat) + 4);
    }
    context.textAlign = "center";
    for (const lon of ticks(lon0, lon1)) {
      context.fillText(`${lon.toFixed(2)}°E`, x(lon), height - 10);
    }
    context.textAlign = "left";
  });

  function onMove(event: React.MouseEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) * WIDTH) / rect.width;
    const py = ((event.clientY - rect.top) * height) / rect.height;
    const lon = lon0 + ((px - MARGIN.left) / plotWidth) * (lon1 - lon0);
    const lat = lat1 - ((py - MARGIN.top) / plotHeight) * (lat1 - lat0);
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

  const [hours, sourceNy, sourceNx] = grid.source_shape;
  const sourceValues = grid.source_shape.reduce((product, size) => product * size, 1);

  return (
    <figure className="analysis-chart grid-heatmap" aria-label={title || "Gridded forecast map"}>
      {title ? <figcaption>{title}</figcaption> : null}
      <p className="chart-dimensions">
        Latitude <span aria-hidden="true">×</span> longitude grid · {ny} × {nx} cells of{" "}
        {grid.resolution_degrees}°{grid.synthetic ? (
          <strong className="grid-heatmap__badge">Synthetic sample</strong>
        ) : null}
      </p>
      <div className="grid-heatmap__frame">
        <canvas
          ref={canvasRef}
          width={WIDTH}
          height={height}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          role="img"
          aria-label={`${grid.value_label} heatmap`}
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
            aria-label="Forecast hour"
          />
          <span>{frame.label}</span>
        </div>
      ) : null}
      <div className="grid-heatmap__footer">
        <span className="grid-heatmap__readout" aria-live="polite">
          {hover
            ? `${hover.lat.toFixed(2)}°N, ${hover.lon.toFixed(2)}°E · ${
                hover.value === null ? "no value" : `${format(hover.value, 1)} ${grid.unit}`
              }`
            : `Hover a cell to read its value`}
        </span>
        {grid.scale === "hours" ? (
          <span className="geo-map__legend grid-heatmap__legend">
            <strong>{grid.value_label}</strong>
            <span>0</span>
            <i className="gradient-swatch" />
            <span>{format(hoursMax)}</span>
          </span>
        ) : (
          <span className="grid-heatmap__legend grid-heatmap__aqi">
            {AQI_NAMES.map((name, index) => (
              <span key={name}>
                <i style={{ background: AQI_COLORS[index] }} />
                {name}
              </span>
            ))}
          </span>
        )}
      </div>
      <p className="chart-note">
        Computed directly on the forecast array ({hours} h × {sourceNy} × {sourceNx} ={" "}
        {format(sourceValues)} values). Flattened, that would be one table row per value.
        {grid.scale === "hours" ? "" : " Colours follow CPCB AQI breakpoints."}
      </p>
    </figure>
  );
}
