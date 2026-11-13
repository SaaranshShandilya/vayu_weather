import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GridPayload } from "./types";
import { ColorBar, tickLabels } from "./ColorBar";
import {
  composeFigure,
  downloadGif,
  downloadPng,
  slugify,
  type ExportFrame,
  type ExportLegend,
} from "./exportImage";
import { STANDARD_MARKERS } from "./GridHeatmap";
import {
  AQI_BANDS,
  BASEMAP_CREDIT,
  PALETTES,
  addBasemap,
  aqiBand,
  aqiRanges,
  drawTiles,
  paletteColor,
} from "./mapStyle";

/**
 * A grid (the CAMS forecast or binned NASA FIRMS fire detections) as a
 * translucent layer on the street basemap, with place names drawn above it. PM2.5 and PM10 use the CPCB AQI
 * bands, like the station maps; other fields keep their continuous palettes.
 * The view fits the grid, which the server already crops to the place asked
 * about, so a city question opens on that city.
 */

const FRAME_MS = 450;
const OPACITY = 0.68;
const MAX_CANVAS_WIDTH = 1400;

function format(value: number, digits = 0) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(value);
}

/** Web Mercator y of a latitude, the projection Leaflet draws in. */
function mercatorY(latitude: number) {
  return Math.log(Math.tan(Math.PI / 4 + (latitude * Math.PI) / 360));
}

export function GridBasemap({ grid, title }: { grid: GridPayload; title: string }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const overlayRef = useRef<L.ImageOverlay | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState("");
  const [hover, setHover] = useState<{ lat: number; lon: number; value: number | null } | null>(null);

  const ny = grid.latitudes.length;
  const nx = grid.longitudes.length;
  const step = grid.resolution_degrees || 0.4;
  const lat0 = grid.latitudes[0]! - step / 2;
  const lat1 = grid.latitudes[ny - 1]! + step / 2;
  const lon0 = grid.longitudes[0]! - step / 2;
  const lon1 = grid.longitudes[nx - 1]! + step / 2;
  const frame = grid.frames[Math.min(frameIndex, grid.frames.length - 1)]!;
  // The hover readout reads the frame on screen without rebuilding the map.
  const frameRef = useRef(frame.values);
  frameRef.current = frame.values;
  const pollutant = grid.scale === "pm25" || grid.scale === "pm10" ? grid.scale : null;
  const isCount = grid.scale === "days" || grid.scale === "hours";
  // FIRMS fire grids: cells without detections stay clear so the streets show.
  const isFire = grid.domain === "firms";
  const wholeNumbers = isCount || (isFire && grid.unit === "detections");
  const palette = PALETTES[grid.scale] ?? PALETTES.pollutant!;
  const markers = pollutant ? [] : STANDARD_MARKERS[grid.scale] ?? [];
  const digits = grid.unit ? 1 : 3;

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
    if (isCount || isFire) minimum = 0;
    if ((isCount || isFire) && grid.scale_max) maximum = grid.scale_max;
    return { minimum, maximum: Math.max(maximum, minimum + 1e-6) };
  }, [grid, isCount, isFire]);

  const colorOf = (value: number) =>
    pollutant
      ? AQI_BANDS[aqiBand(pollutant, value)]!.color
      : paletteColor(palette, (value - range.minimum) / (range.maximum - range.minimum));

  // Each frame as an image in Mercator rows, so cells land on their true
  // latitudes when Leaflet stretches the image between the corners.
  const frameImages = useMemo(() => {
    const cell = Math.max(2, Math.floor(MAX_CANVAS_WIDTH / nx));
    const width = nx * cell;
    const top = mercatorY(lat1);
    const spanY = top - mercatorY(lat0);
    const height = Math.max(1, Math.round((width * spanY) / (((lon1 - lon0) * Math.PI) / 180)));
    const rowY = (latitude: number) => ((top - mercatorY(latitude)) / spanY) * height;
    return grid.frames.map((shown) => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d")!;
      for (let i = 0; i < ny; i += 1) {
        const y0 = Math.floor(rowY(grid.latitudes[i]! + step / 2));
        const y1 = Math.ceil(rowY(grid.latitudes[i]! - step / 2));
        for (let j = 0; j < nx; j += 1) {
          const value = shown.values[i * nx + j];
          if (value === null || value === undefined || (isFire && value <= 0)) continue;
          context.fillStyle = colorOf(value);
          context.fillRect(j * cell, y0, cell, y1 - y0);
        }
      }
      return canvas;
    });
    // colorOf depends only on the grid and its range.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid, range]);
  const frameUrls = useMemo(() => frameImages.map((canvas) => canvas.toDataURL("image/png")), [frameImages]);
  const bounds = useMemo(() => L.latLngBounds([lat0, lon0], [lat1, lon1]), [lat0, lon0, lat1, lon1]);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const map = L.map(element, { scrollWheelZoom: false, zoomSnap: 0.25, maxZoom: 12 });
    mapRef.current = map;
    addBasemap(map, { labelsOnTop: true });
    overlayRef.current = L.imageOverlay(frameUrls[0]!, bounds, {
      opacity: OPACITY,
      className: "grid-basemap__cells",
      interactive: false,
    }).addTo(map);
    if (grid.outline.length) {
      // The place asked about: a dark line on a white halo, above the cells.
      const outlinePane = map.createPane("outline");
      outlinePane.style.zIndex = "460";
      const rings = grid.outline.map((ring) => ring.map(([lon, lat]) => [lat, lon] as [number, number]));
      L.polygon(rings, { pane: "outline", color: "#ffffff", weight: 5, opacity: 0.85, fill: false, interactive: false }).addTo(map);
      L.polygon(rings, { pane: "outline", color: "#20211f", weight: 2, fill: false, interactive: false }).addTo(map);
    }
    map.fitBounds(bounds, { padding: [12, 12] });

    map.on("mousemove", (event: L.LeafletMouseEvent) => {
      const { lat, lng } = event.latlng;
      const i = Math.round((lat - grid.latitudes[0]!) / step);
      const j = Math.round((lng - grid.longitudes[0]!) / step);
      if (i < 0 || j < 0 || i >= ny || j >= nx) {
        setHover(null);
        return;
      }
      setHover({ lat: grid.latitudes[i]!, lon: grid.longitudes[j]!, value: frameRef.current[i * nx + j] ?? null });
    });
    map.on("mouseout", () => setHover(null));
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      map.setZoomAround(map.mouseEventToLatLng(event), map.getZoom() + (event.deltaY < 0 ? 0.5 : -0.5));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(element);
    return () => {
      resize.disconnect();
      element.removeEventListener("wheel", onWheel);
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
    // The map is rebuilt only for a new grid; frames swap the overlay image.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid, bounds]);

  useEffect(() => {
    overlayRef.current?.setUrl(frameUrls[Math.min(frameIndex, frameUrls.length - 1)]!);
  }, [frameIndex, frameUrls]);

  useEffect(() => {
    if (!playing || grid.frames.length < 2) return;
    const timer = window.setInterval(
      () => setFrameIndex((current) => (current + 1) % grid.frames.length),
      FRAME_MS,
    );
    return () => window.clearInterval(timer);
  }, [playing, grid.frames.length]);

  const legendTitle = isCount ? grid.value_label : `${grid.value_label}${grid.unit ? ` (${grid.unit})` : ""}`;
  const formatLegend = (value: number) => format(value, wholeNumbers ? 0 : digits);
  const span = range.maximum - range.minimum || 1;
  const exportLegend: ExportLegend = pollutant
    ? {
        kind: "categories",
        colors: AQI_BANDS.map((band) => band.color),
        names: AQI_BANDS.map((band, index) => `${band.name} ${aqiRanges(pollutant)[index]}`),
        title: `${pollutant === "pm25" ? "PM2.5" : "PM10"} (µg/m³)`,
      }
    : {
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

  /** One frame over the basemap as shown now (tiles, cells, outline, labels). */
  function exportFrame(index: number, width: number, height: number): ExportFrame {
    const map = mapRef.current!;
    const element = container.current!;
    const shown = grid.frames[index]!;
    return {
      subtitle: [shown.label, grid.source_label].filter(Boolean).join(" · "),
      draw: (context) => {
        context.save();
        context.beginPath();
        context.rect(0, 0, width, height);
        context.clip();
        context.fillStyle = "#eef0f2";
        context.fillRect(0, 0, width, height);
        drawTiles(context, element, ".leaflet-tile-pane img.leaflet-tile-loaded");
        const nw = map.latLngToContainerPoint([lat1, lon0]);
        const se = map.latLngToContainerPoint([lat0, lon1]);
        context.globalAlpha = OPACITY;
        context.drawImage(frameImages[index]!, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
        context.globalAlpha = 1;
        if (grid.outline.length) {
          context.beginPath();
          for (const ring of grid.outline) {
            ring.forEach(([lon, lat], k) => {
              const p = map.latLngToContainerPoint([lat, lon]);
              if (k === 0) context.moveTo(p.x, p.y);
              else context.lineTo(p.x, p.y);
            });
            context.closePath();
          }
          context.lineJoin = "round";
          context.lineWidth = 5;
          context.strokeStyle = "rgba(255, 255, 255, 0.85)";
          context.stroke();
          context.lineWidth = 2;
          context.strokeStyle = "#20211f";
          context.stroke();
        }
        drawTiles(context, element, ".leaflet-labels-pane img.leaflet-tile-loaded");
        context.restore();
      },
    };
  }

  function exportOptions() {
    const element = container.current!;
    return {
      width: element.clientWidth,
      height: element.clientHeight,
      title: title || grid.value_label,
      legend: exportLegend,
      source: isFire
        ? `Source: ${grid.source_label}. Satellite fire detections, not fires or burned area. ` +
          `We acknowledge the use of data from NASA's FIRMS, part of NASA's ESDIS. ${BASEMAP_CREDIT} ` +
          "VayuChat · IIT Gandhinagar."
        : `Source: ${grid.source_label}. Contains modified Copernicus Atmosphere Monitoring Service ` +
          `information (CC-BY-4.0). Model forecast, not observation. ${BASEMAP_CREDIT} VayuChat · IIT Gandhinagar.`,
    };
  }

  async function savePng() {
    if (!mapRef.current || !container.current) return;
    setExporting("png");
    setExportError("");
    try {
      const options = exportOptions();
      const figure = composeFigure(exportFrame(frameIndex, options.width, options.height), { ...options, scale: 2 });
      const suffix = grid.frames.length > 1 ? `-${slugify(frame.label)}` : "";
      await downloadPng(figure, `${slugify(title)}${suffix}.png`);
    } catch {
      setExportError("The map image could not be created.");
    } finally {
      setExporting(null);
    }
  }

  async function saveGif() {
    if (!mapRef.current || !container.current) return;
    setPlaying(false);
    setExporting("gif 0%");
    setExportError("");
    try {
      const options = exportOptions();
      await downloadGif(
        grid.frames.map((_, index) => exportFrame(index, options.width, options.height)),
        { ...options, delay: FRAME_MS },
        `${slugify(title)}.gif`,
        (done, total) => setExporting(`gif ${Math.round((done / total) * 100)}%`),
      );
    } catch {
      setExportError("The animation could not be encoded.");
    } finally {
      setExporting(null);
    }
  }

  return (
    <figure className="analysis-chart grid-basemap" aria-label={title || (isFire ? "Fire map" : "Forecast map")}>
      {title ? <figcaption>{title}</figcaption> : null}
      <div className="grid-heatmap__header">
        <p className="chart-dimensions">
          <span className="domain-chip">{grid.domain_label || grid.domain}</span>
          {ny} × {nx} cells of {grid.resolution_degrees}°
          {grid.outline_label ? ` · outlined: ${grid.outline_label}` : ""}
        </p>
        <div className="map-download" role="group" aria-label="Map actions">
          <button type="button" onClick={() => mapRef.current?.fitBounds(bounds, { padding: [12, 12] })}>
            Reset view
          </button>
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
      <div
        ref={container}
        className="point-map__canvas grid-basemap__canvas"
        role="region"
        aria-label={`${grid.value_label} ${isFire ? "map" : "forecast map"}. Drag to pan; use the + and − buttons to zoom.`}
      />
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
      <div className="point-map__legend">
        {pollutant ? (
          <>
            <span className="point-map__legend-title">
              {pollutant === "pm25" ? "PM2.5" : "PM10"} µg/m³ · CPCB AQI bands
            </span>
            <ul className="point-map__bands">
              {AQI_BANDS.map((band, index) => (
                <li key={band.name}>
                  <i style={{ background: band.color }} />
                  <span>{band.name}</span>
                  <small>{aqiRanges(pollutant)[index]}</small>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <ColorBar
            colors={palette}
            minimum={range.minimum}
            maximum={range.maximum}
            title={legendTitle}
            format={formatLegend}
            markers={markers}
          />
        )}
        <span className="point-map__shapes">
          <span className="grid-heatmap__readout" aria-live="polite">
            {hover
              ? `${hover.lat.toFixed(2)}°N, ${hover.lon.toFixed(2)}°E · ${
                  hover.value === null ? "no value" : `${format(hover.value, digits)} ${grid.unit}`.trim()
                }`
              : "Hover the map to read a cell's value"}
          </span>
          <span className="point-map__hint">Drag to pan · Ctrl/⌘ + scroll or ± to zoom</span>
        </span>
      </div>
      {isFire ? (
        <p className="chart-note">
          NASA FIRMS satellite fire detections (VIIRS 375 m, MODIS 1 km) counted in{" "}
          {grid.resolution_degrees}° cells; clear cells had none. These are detections, not fires
          or burned area: one fire can be seen by several satellites, cloud and smoke hide fires,
          and persistent detections at coal fields and industrial sites are not crop burning. We
          acknowledge the use of data from NASA's FIRMS, part of NASA's ESDIS.
        </p>
      ) : (
        <p className="chart-note">
          CAMS model forecast on a {grid.resolution_degrees}° grid (about 44 km), not measurement.
          {pollutant ? " AQI bands are defined for 24-hour means; hourly frames are for reference." : ""}
          {" "}Contains modified Copernicus Atmosphere Monitoring Service information.
        </p>
      )}
    </figure>
  );
}
