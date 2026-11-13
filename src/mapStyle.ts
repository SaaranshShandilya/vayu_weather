import L from "leaflet";

/**
 * Shared map styling: the street basemap, the CPCB AQI colour bands and the
 * continuous palettes, so station maps, CAMS forecast maps and the gridded
 * heatmaps colour the same value the same way.
 */

// Esri's light grey canvas: muted streets so the data colours carry the map,
// with a separate label layer drawn above the data for place and road names.
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
const BASE_TILES = `${ESRI}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`;
const LABEL_TILES = `${ESRI}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`;
const ATTRIBUTION =
  'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
export const BASEMAP_CREDIT = "Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors.";

/**
 * Adds the basemap. With `labelsOnTop`, place names go in a pane above
 * overlays (z 450), so they stay readable over a coloured forecast grid.
 */
export function addBasemap(map: L.Map, { labelsOnTop = false } = {}) {
  L.tileLayer(BASE_TILES, { attribution: ATTRIBUTION, maxZoom: 16, crossOrigin: "anonymous" }).addTo(map);
  const options: L.TileLayerOptions = { maxZoom: 16, crossOrigin: "anonymous" };
  if (labelsOnTop) {
    const labels = map.createPane("labels");
    labels.style.zIndex = "450";
    labels.style.pointerEvents = "none";
    options.pane = "labels";
  }
  // Leaflet copies undefined options over its defaults, so `pane` is set
  // only when used: `pane: undefined` would detach the layer from any pane.
  L.tileLayer(LABEL_TILES, options).addTo(map);
}

/** Paints the loaded basemap tiles of a map container onto an export canvas. */
export function drawTiles(context: CanvasRenderingContext2D, element: HTMLElement, panes = "img.leaflet-tile-loaded") {
  const origin = element.getBoundingClientRect();
  for (const tile of element.querySelectorAll<HTMLImageElement>(panes)) {
    const box = tile.getBoundingClientRect();
    context.drawImage(tile, box.left - origin.left, box.top - origin.top, box.width, box.height);
  }
}

export type Band = { name: string; color: string; ink: string };
export const AQI_BANDS: Band[] = [
  { name: "Good", color: "#00a65a", ink: "#ffffff" },
  { name: "Satisfactory", color: "#8cc63f", ink: "#1d2b0b" },
  { name: "Moderate", color: "#f2c200", ink: "#2d2400" },
  { name: "Poor", color: "#f7811d", ink: "#2e1500" },
  { name: "Very poor", color: "#e0271c", ink: "#ffffff" },
  { name: "Severe", color: "#7e0023", ink: "#ffffff" },
];
// Upper bounds of each band (µg/m³), CPCB National AQI sub-index breakpoints.
export const AQI_BREAKS: Record<"pm25" | "pm10", number[]> = {
  pm25: [30, 60, 90, 120, 250],
  pm10: [50, 100, 250, 350, 430],
};

export function aqiBand(pollutant: "pm25" | "pm10", value: number) {
  const index = AQI_BREAKS[pollutant].findIndex((limit) => value <= limit);
  return index === -1 ? AQI_BANDS.length - 1 : index;
}

/** "0–30", "31–60" … "250+" for each band. */
export function aqiRanges(pollutant: "pm25" | "pm10") {
  const breaks = AQI_BREAKS[pollutant];
  return AQI_BANDS.map((_, index) => {
    const low = index === 0 ? 0 : breaks[index - 1]! + 1;
    return index < breaks.length ? `${low}–${breaks[index]}` : `${breaks[breaks.length - 1]}+`;
  });
}

// Continuous palettes, low to high.
export const PALETTES: Record<string, string[]> = {
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
  fires: ["#fff8e7", "#fed98e", "#fe9929", "#d95f0e", "#993404", "#4a1a02"],
  pm25: ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026", "#3f0011"],
  pm10: ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026", "#3f0011"],
};

function hexChannels(hex: string) {
  return [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
}

export function paletteColor(stops: string[], fraction: number) {
  const position = Math.max(0, Math.min(1, fraction)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(position));
  const local = position - index;
  const from = hexChannels(stops[index]!);
  const to = hexChannels(stops[index + 1]!);
  const channel = (k: number) => Math.round(from[k]! + (to[k]! - from[k]!) * local);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}
