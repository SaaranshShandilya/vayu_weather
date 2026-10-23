export type Tick = { fraction: number; label: string };
export type Marker = { value: number; label: string };

/** Round tick values spanning [minimum, maximum], about `count` of them. */
export function niceTicks(minimum: number, maximum: number, count = 5): number[] {
  const span = maximum - minimum;
  if (!(span > 0)) return [minimum];
  const raw = span / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = Math.ceil(minimum / step) * step; v <= maximum + step * 1e-9; v += step) {
    ticks.push(Number(v.toPrecision(12)));
  }
  return ticks;
}

export function tickLabels(
  minimum: number,
  maximum: number,
  format: (value: number) => string,
): Tick[] {
  const span = maximum - minimum || 1;
  return niceTicks(minimum, maximum).map((value) => ({
    fraction: (value - minimum) / span,
    label: format(value),
  }));
}

/**
 * A continuous colour bar: the gradient, round tick values beneath it, and
 * optional reference markers (e.g. a CPCB standard) drawn as dashed lines.
 */
export function ColorBar({
  colors,
  minimum,
  maximum,
  title,
  format,
  markers = [],
}: {
  colors: string[];
  minimum: number;
  maximum: number;
  title: string;
  format: (value: number) => string;
  markers?: Marker[];
}) {
  const span = maximum - minimum || 1;
  const ticks = tickLabels(minimum, maximum, format);
  const shown = markers.filter((marker) => marker.value >= minimum && marker.value <= maximum);
  return (
    <figure className="color-bar" aria-label={`${title} colour scale from ${format(minimum)} to ${format(maximum)}`}>
      <figcaption>{title}</figcaption>
      <div className="color-bar__track">
        <i style={{ background: `linear-gradient(90deg, ${colors.join(", ")})` }} />
        {shown.map((marker) => (
          <span
            key={marker.label}
            className="color-bar__marker"
            style={{ left: `${((marker.value - minimum) / span) * 100}%` }}
            title={marker.label}
          />
        ))}
      </div>
      <div className="color-bar__ticks">
        {ticks.map((tick) => (
          <span key={tick.label} style={{ left: `${tick.fraction * 100}%` }}>
            {tick.label}
          </span>
        ))}
      </div>
      {shown.length ? (
        <p className="color-bar__note">
          {shown.map((marker) => `┊ ${marker.label}`).join(" · ")}
        </p>
      ) : null}
    </figure>
  );
}
