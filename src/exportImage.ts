import { GIFEncoder, applyPalette, quantize } from "gifenc";

/** A legend drawn under an exported map. */
export type ExportLegend =
  | {
      kind: "gradient";
      colors: string[];
      minimum: string;
      maximum: string;
      title: string;
      // Positions along the bar, 0-1, with labels.
      ticks?: Array<{ fraction: number; label: string }>;
      markers?: Array<{ fraction: number; label: string }>;
    }
  | { kind: "categories"; colors: string[]; names: string[]; title: string }
  | { kind: "points"; color: string; title: string };

export type ExportFrame = {
  /** Draws the map into the given context, already offset below the header. */
  draw: (context: CanvasRenderingContext2D) => void;
  /** Shown under the title, e.g. the date of an animation frame. */
  subtitle: string;
};

const PADDING = 16;
const HEADER = 54;
const FOOTER = 78;

export function slugify(text: string) {
  return (
    text
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 80) || "map"
  );
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function drawLegend(context: CanvasRenderingContext2D, legend: ExportLegend, x: number, y: number) {
  context.font = "600 12px system-ui, sans-serif";
  context.fillStyle = "#20211f";
  context.textAlign = "left";
  context.fillText(legend.title, x, y);
  const offset = x + context.measureText(legend.title).width + 12;
  context.font = "12px system-ui, sans-serif";
  if (legend.kind === "points") {
    context.beginPath();
    context.arc(offset + 5, y - 4, 4, 0, Math.PI * 2);
    context.fillStyle = legend.color;
    context.fill();
    return;
  }
  if (legend.kind === "categories") {
    let x0 = offset;
    legend.names.forEach((name, index) => {
      context.fillStyle = legend.colors[index]!;
      context.fillRect(x0, y - 10, 10, 10);
      context.fillStyle = "#62625e";
      context.fillText(name, x0 + 14, y);
      x0 += 14 + context.measureText(name).width + 12;
    });
    return;
  }
  // A continuous bar with round tick values beneath, like the on-screen one.
  const barWidth = 300;
  const gradient = context.createLinearGradient(offset, 0, offset + barWidth, 0);
  legend.colors.forEach((color, index) =>
    gradient.addColorStop(index / Math.max(1, legend.colors.length - 1), color),
  );
  context.fillStyle = gradient;
  context.fillRect(offset, y - 10, barWidth, 10);
  context.fillStyle = "#62625e";
  context.font = "11px system-ui, sans-serif";
  const ticks = legend.ticks?.length
    ? legend.ticks
    : [
        { fraction: 0, label: legend.minimum },
        { fraction: 1, label: legend.maximum },
      ];
  context.textAlign = "center";
  for (const tick of ticks) {
    const x = offset + tick.fraction * barWidth;
    context.fillRect(x - 0.5, y, 1, 3);
    context.fillText(tick.label, x, y + 14);
  }
  context.textAlign = "left";
  for (const marker of legend.markers ?? []) {
    const x = offset + marker.fraction * barWidth;
    context.strokeStyle = "#20211f";
    context.setLineDash([2, 2]);
    context.beginPath();
    context.moveTo(x, y - 13);
    context.lineTo(x, y + 2);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = "#20211f";
    context.fillText(`┊ ${marker.label}`, offset + barWidth + 12, y);
  }
}

/**
 * Lays a map out as a self-contained figure: title and subtitle above, legend
 * and source line below, on a white ground, at `scale` times the map size.
 */
export function composeFigure(
  frame: ExportFrame,
  options: { width: number; height: number; title: string; legend: ExportLegend; source: string; scale?: number },
) {
  const scale = options.scale ?? 1;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round((options.width + PADDING * 2) * scale);
  canvas.height = Math.round((options.height + HEADER + FOOTER) * scale);
  const context = canvas.getContext("2d")!;
  context.scale(scale, scale);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, options.width + PADDING * 2, options.height + HEADER + FOOTER);

  context.fillStyle = "#20211f";
  context.font = "600 15px system-ui, sans-serif";
  context.textAlign = "left";
  context.fillText(options.title, PADDING, 24, options.width);
  context.font = "12px system-ui, sans-serif";
  context.fillStyle = "#62625e";
  context.fillText(frame.subtitle, PADDING, 42, options.width);

  context.save();
  context.translate(PADDING, HEADER);
  frame.draw(context);
  context.restore();

  const legendY = HEADER + options.height + 24;
  drawLegend(context, options.legend, PADDING, legendY);
  context.font = "11px system-ui, sans-serif";
  context.fillStyle = "#8a8983";
  context.fillText(options.source, PADDING, legendY + 38, options.width);
  return canvas;
}

export async function downloadPng(canvas: HTMLCanvasElement, filename: string) {
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (blob) downloadBlob(blob, filename);
}

/** Encodes each frame (already composed) into a looping GIF. */
export async function downloadGif(
  frames: ExportFrame[],
  options: { width: number; height: number; title: string; legend: ExportLegend; source: string; delay: number },
  filename: string,
  onProgress?: (done: number, total: number) => void,
) {
  const gif = GIFEncoder();
  for (let index = 0; index < frames.length; index += 1) {
    const canvas = composeFigure(frames[index]!, { ...options, scale: 1 });
    const context = canvas.getContext("2d")!;
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    const palette = quantize(data, 256);
    gif.writeFrame(applyPalette(data, palette), canvas.width, canvas.height, {
      palette,
      delay: options.delay,
    });
    onProgress?.(index + 1, frames.length);
    // Yield so the page stays responsive while long animations encode.
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  }
  gif.finish();
  downloadBlob(new Blob([gif.bytes()], { type: "image/gif" }), filename);
}
