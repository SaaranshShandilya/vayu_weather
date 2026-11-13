import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { ResultData } from "./types";
import { chartAxisTitle } from "./chartMetadata";
import { composeFigure, downloadPng, slugify } from "./exportImage";
import { AQI_BANDS, BASEMAP_CREDIT, addBasemap, aqiBand, aqiRanges, drawTiles } from "./mapStyle";

/**
 * Station points on a street basemap, coloured by CPCB AQI category for PM2.5
 * and PM10 (as OAQ and the CPCB bulletin do) and by a sequential ramp for
 * everything else. The view fits the stations, so a question about one city
 * opens on that city. Reference monitors are circles; low-cost sensors squares.
 */

// India and its islands; anything outside is a placeholder coordinate (0, 0).
const INDIA = { south: 6, north: 38, west: 68, east: 98 };

const RAMP = ["#fde725", "#7ad151", "#22a884", "#2a788e", "#414487", "#440154"];

type Point = {
  latitude: number;
  longitude: number;
  value: number | null;
  sensor: boolean;
  row: Record<string, unknown>;
};

function numeric(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function format(value: number) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(value);
}

function escapeHtml(text: string) {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function pollutantOf(key: string, title: string): "pm25" | "pm10" | null {
  const text = `${key} ${title}`.toLowerCase();
  if (/pm25|pm2\.5/.test(text)) return "pm25";
  if (/pm10/.test(text)) return "pm10";
  return null;
}

function isSensor(row: Record<string, unknown>) {
  const network = String(row.network ?? "");
  return network !== "" && !/cpcb/i.test(network);
}

const TOOLTIP_SKIP = new Set(["latitude", "longitude", "station", "city", "network"]);

function tooltip(point: Point, valueKey: string, unit: string) {
  const row = point.row;
  const lines = [
    `<strong>${escapeHtml(String(row.station ?? row.sensor ?? "Station"))}</strong>`,
    escapeHtml([row.city, row.network].filter(Boolean).map(String).join(" · ")),
  ];
  if (point.value !== null) {
    lines.push(`<span class="point-map__tip-value">${format(point.value)} ${escapeHtml(unit)}</span>`);
  }
  for (const [key, value] of Object.entries(row)) {
    if (TOOLTIP_SKIP.has(key) || key === valueKey || value === null || value === "") continue;
    const shown = typeof value === "number" ? format(value) : String(value);
    lines.push(`${escapeHtml(chartAxisTitle(key))}: ${escapeHtml(shown)}`);
  }
  return lines.filter(Boolean).join("<br>");
}

export function PointMap({ result }: { result: ResultData }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const boundsRef = useRef<L.LatLngBounds | null>(null);
  const [saving, setSaving] = useState(false);
  const [exportError, setExportError] = useState("");

  const valueKey =
    result.y_keys.find((key) => key !== "latitude" && key !== "longitude") ?? "";
  const pollutant = pollutantOf(valueKey, result.title);
  const unit = pollutant ? "µg/m³" : "";

  const points = useMemo<Point[]>(
    () =>
      result.rows.flatMap((row) => {
        const latitude = numeric(row.latitude);
        const longitude = numeric(row.longitude);
        if (
          latitude === null || longitude === null ||
          latitude < INDIA.south || latitude > INDIA.north ||
          longitude < INDIA.west || longitude > INDIA.east
        ) {
          return [];
        }
        return [{ latitude, longitude, value: numeric(row[valueKey]), sensor: isSensor(row), row }];
      }),
    [result.rows, valueKey],
  );
  const values = points.flatMap((point) => (point.value === null ? [] : [point.value]));
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 1;

  function bandIndex(value: number) {
    if (pollutant) return aqiBand(pollutant, value);
    const fraction = (value - minimum) / Math.max(maximum - minimum, 1e-9);
    return Math.min(RAMP.length - 1, Math.floor(fraction * RAMP.length));
  }
  function style(value: number | null) {
    if (value === null) return { color: "#36589f", ink: "#ffffff" };
    const index = bandIndex(value);
    if (pollutant) return { color: AQI_BANDS[index]!.color, ink: AQI_BANDS[index]!.ink };
    return { color: RAMP[index]!, ink: index < 2 ? "#1d1d1b" : "#ffffff" };
  }

  const categories = pollutant
    ? AQI_BANDS.map((band, index) => ({
        ...band,
        range: aqiRanges(pollutant)[index]!,
        count: values.filter((value) => bandIndex(value) === index).length,
      }))
    : [];
  const hasSensors = points.some((point) => point.sensor);
  const hasReference = points.some((point) => !point.sensor);
  // Few points: show the value on each marker, as a live map does.
  const labelled = points.length <= 80 && values.length > 0;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const map = L.map(element, {
      scrollWheelZoom: false,
      zoomSnap: 0.5,
      attributionControl: true,
      preferCanvas: false,
    });
    mapRef.current = map;
    addBasemap(map);

    const layer = L.layerGroup().addTo(map);
    // Draw the most polluted last, so they sit on top where markers overlap.
    const ordered = [...points].sort((a, b) => (a.value ?? -1) - (b.value ?? -1));
    for (const point of ordered) {
      const { color, ink } = style(point.value);
      const size = labelled ? 27 : 12;
      const text = labelled && point.value !== null ? format(Math.round(point.value)) : "";
      const icon = L.divIcon({
        className: "",
        html:
          `<span class="point-map__marker${point.sensor ? " is-sensor" : ""}${labelled ? " is-labelled" : ""}"` +
          ` style="background:${color};color:${ink};width:${size}px;height:${size}px">${text}</span>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      L.marker([point.latitude, point.longitude], { icon, keyboard: false, riseOnHover: true })
        .bindTooltip(tooltip(point, valueKey, unit), { direction: "top", offset: [0, -size / 2] })
        .addTo(layer);
    }

    const bounds = points.length
      ? L.latLngBounds(points.map((point) => [point.latitude, point.longitude] as [number, number]))
      : L.latLngBounds([INDIA.south, INDIA.west], [INDIA.north, INDIA.east]);
    boundsRef.current = bounds;
    const fit = () => map.fitBounds(bounds, { padding: [28, 28], maxZoom: 13 });
    fit();

    // Ctrl/⌘ + scroll zooms; plain scroll keeps scrolling the conversation.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const at = map.mouseEventToLatLng(event);
      map.setZoomAround(at, map.getZoom() + (event.deltaY < 0 ? 0.5 : -0.5));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(element);
    return () => {
      resize.disconnect();
      element.removeEventListener("wheel", onWheel);
      map.remove();
      mapRef.current = null;
    };
    // style/bandIndex derive from points and pollutant.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, valueKey, pollutant, labelled]);

  function resetView() {
    const map = mapRef.current;
    if (map && boundsRef.current) map.fitBounds(boundsRef.current, { padding: [28, 28], maxZoom: 13 });
  }

  async function savePng() {
    const map = mapRef.current;
    const element = container.current;
    if (!map || !element) return;
    setSaving(true);
    setExportError("");
    try {
      const width = element.clientWidth;
      const height = element.clientHeight;
      const figure = composeFigure(
        {
          subtitle: `${points.length.toLocaleString("en-IN")} ${hasSensors ? "monitors and sensors" : "stations"}`,
          draw: (context) => {
            context.save();
            context.beginPath();
            context.rect(0, 0, width, height);
            context.clip();
            context.fillStyle = "#eef0f2";
            context.fillRect(0, 0, width, height);
            drawTiles(context, element);
            const ordered = [...points].sort((a, b) => (a.value ?? -1) - (b.value ?? -1));
            for (const point of ordered) {
              const { x, y } = map.latLngToContainerPoint([point.latitude, point.longitude]);
              if (x < -20 || y < -20 || x > width + 20 || y > height + 20) continue;
              const { color, ink } = style(point.value);
              const radius = labelled ? 12 : 5;
              context.fillStyle = color;
              context.strokeStyle = "#ffffff";
              context.lineWidth = 1.5;
              context.beginPath();
              if (point.sensor) context.rect(x - radius, y - radius, radius * 2, radius * 2);
              else context.arc(x, y, radius, 0, Math.PI * 2);
              context.fill();
              context.stroke();
              if (labelled && point.value !== null) {
                context.fillStyle = ink;
                context.font = "600 10px system-ui, sans-serif";
                context.textAlign = "center";
                context.textBaseline = "middle";
                context.fillText(format(Math.round(point.value)), x, y + 0.5);
              }
            }
            context.restore();
          },
        },
        {
          width,
          height,
          title: result.title || "Map",
          legend: pollutant
            ? {
                kind: "categories",
                colors: AQI_BANDS.map((band) => band.color),
                names: categories.map((band) => `${band.name} ${band.range}`),
                title: `${pollutant === "pm25" ? "PM2.5" : "PM10"} (µg/m³)`,
              }
            : { kind: "points", color: "#36589f", title: chartAxisTitle(valueKey) || "Station" },
          source:
            `Data: CPCB via OAQ (oaq.notf.in) and VayuChat tables. ${BASEMAP_CREDIT} VayuChat · IIT Gandhinagar.`,
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
    <figure className="analysis-chart point-map" aria-label={result.title || "Map"}>
      <div className="geo-map__header">
        {result.title ? <figcaption>{result.title}</figcaption> : <span />}
        <div className="map-download" role="group" aria-label="Map actions">
          <button type="button" onClick={resetView}>Reset view</button>
          <button type="button" onClick={() => void savePng()} disabled={saving}>
            {saving ? "Saving…" : "Download PNG"}
          </button>
          {exportError ? <span className="map-download__error" role="alert">{exportError}</span> : null}
        </div>
      </div>
      <div
        ref={container}
        className="point-map__canvas"
        role="region"
        aria-label={`${result.title || "Map"}: ${points.length} stations. Drag to pan; use the + and − buttons to zoom.`}
      />
      <div className="point-map__legend">
        {pollutant ? (
          <>
            <span className="point-map__legend-title">
              {pollutant === "pm25" ? "PM2.5" : "PM10"} µg/m³ · CPCB AQI bands
            </span>
            <ul className="point-map__bands">
              {categories.map((band) => (
                <li key={band.name} className={band.count ? undefined : "is-empty"}>
                  <i style={{ background: band.color }} />
                  <span>{band.name}</span>
                  <small>{band.range} · {band.count}</small>
                </li>
              ))}
            </ul>
          </>
        ) : values.length ? (
          <span className="point-map__legend-title">
            {chartAxisTitle(valueKey)}: {format(minimum)}
            <span className="point-map__ramp" style={{ background: `linear-gradient(90deg, ${RAMP.join(", ")})` }} />
            {format(maximum)}
          </span>
        ) : (
          <span className="point-map__legend-title">Monitoring stations</span>
        )}
        <span className="point-map__shapes">
          {hasReference ? <span><i className="point-map__shape" /> CPCB reference monitor</span> : null}
          {hasSensors ? <span><i className="point-map__shape is-sensor" /> Low-cost sensor</span> : null}
          <span className="point-map__hint">Drag to pan · Ctrl/⌘ + scroll or ± to zoom · hover a marker for details</span>
        </span>
      </div>
    </figure>
  );
}
