export function ZoomControls({
  zoom,
  zoomed,
  onZoomIn,
  onZoomOut,
  onReset,
}: {
  zoom: number;
  zoomed: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
}) {
  return (
    <div className="map-zoom" role="group" aria-label="Map zoom">
      <button type="button" onClick={onZoomIn} disabled={zoom >= 12} aria-label="Zoom in" title="Zoom in">
        +
      </button>
      <button type="button" onClick={onZoomOut} disabled={!zoomed} aria-label="Zoom out" title="Zoom out">
        −
      </button>
      {zoomed ? (
        <button type="button" className="map-zoom__reset" onClick={onReset} title="Show the whole map">
          Reset
        </button>
      ) : null}
    </div>
  );
}

export function ZoomHint() {
  return (
    <span className="map-zoom__hint">
      Drag to pan · Ctrl/⌘ + scroll, pinch or double-click to zoom
    </span>
  );
}
