import { useCallback, useEffect, useRef, useState } from "react";

/** A drawing-space box the view must keep filled, e.g. a map's plot area. */
export type Box = { x: number; y: number; width: number; height: number };

/** screen = drawing * k + (tx, ty), in the element's own drawing units. */
export type View = { k: number; tx: number; ty: number };

const IDENTITY: View = { k: 1, tx: 0, ty: 0 };
const MIN_ZOOM = 1;
const MAX_ZOOM = 12;
const BUTTON_STEP = 1.6;
const PAN_STEP = 60;

function clamp(view: View, box: Box): View {
  const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k));
  // Keep the zoomed box covering the original box: no empty margins.
  const clampAxis = (t: number, start: number, size: number) =>
    Math.min(start - start * k, Math.max(start + size - (start + size) * k, t));
  return {
    k,
    tx: clampAxis(view.tx, box.x, box.width),
    ty: clampAxis(view.ty, box.y, box.height),
  };
}

function zoomAround(view: View, factor: number, px: number, py: number, box: Box): View {
  const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
  const ratio = k / view.k;
  return clamp({ k, tx: px - (px - view.tx) * ratio, ty: py - (py - view.ty) * ratio }, box);
}

/**
 * Zoom and pan for a canvas or SVG drawn in fixed drawing units
 * (`drawWidth` × `drawHeight`) and scaled to fit its container by CSS.
 *
 * Drag pans; Ctrl/⌘ + wheel (also a trackpad pinch) and two-finger pinch zoom
 * around the pointer; double-click zooms in (Shift zooms out). Plain wheel
 * scrolling is left to the page.
 */
export function usePanZoom<T extends Element>(drawWidth: number, drawHeight: number, box: Box) {
  const [view, setView] = useState<View>(IDENTITY);
  const elementRef = useRef<T | null>(null);
  const boxRef = useRef(box);
  boxRef.current = box;
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const dragged = useRef(false);

  const toDrawing = useCallback(
    (clientX: number, clientY: number) => {
      const element = elementRef.current;
      // An SVG may be letterboxed inside its box; its own matrix is exact.
      if (element instanceof SVGSVGElement) {
        const matrix = element.getScreenCTM();
        if (matrix) {
          const point = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse());
          return { x: point.x, y: point.y };
        }
      }
      const rect = element?.getBoundingClientRect();
      if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 };
      return {
        x: ((clientX - rect.left) * drawWidth) / rect.width,
        y: ((clientY - rect.top) * drawHeight) / rect.height,
      };
    },
    [drawWidth, drawHeight],
  );

  const center = () => ({
    x: boxRef.current.x + boxRef.current.width / 2,
    y: boxRef.current.y + boxRef.current.height / 2,
  });

  const zoomIn = () => {
    const c = center();
    setView((v) => zoomAround(v, BUTTON_STEP, c.x, c.y, boxRef.current));
  };
  const zoomOut = () => {
    const c = center();
    setView((v) => zoomAround(v, 1 / BUTTON_STEP, c.x, c.y, boxRef.current));
  };
  const reset = () => setView(IDENTITY);

  // A wheel listener must be non-passive to stop the page zooming instead.
  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    const onWheel = (event: Event) => {
      const wheel = event as WheelEvent;
      if (!wheel.ctrlKey && !wheel.metaKey) return;
      wheel.preventDefault();
      const point = toDrawing(wheel.clientX, wheel.clientY);
      const factor = Math.exp(-wheel.deltaY * 0.0025);  // ~1.3x per mouse-wheel notch
      setView((v) => zoomAround(v, factor, point.x, point.y, boxRef.current));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [toDrawing]);

  // The data or plot size changed: start from the full view again.
  useEffect(() => {
    setView((v) => clamp(v, box));
  }, [box.x, box.y, box.width, box.height]);

  const onPointerDown = (event: React.PointerEvent<T>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    pointers.current.set(event.pointerId, toDrawing(event.clientX, event.clientY));
    dragged.current = false;
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<T>) => {
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    const point = toDrawing(event.clientX, event.clientY);
    const other = [...pointers.current.entries()].find(([id]) => id !== event.pointerId)?.[1];
    if (other) {
      // Pinch: zoom by the change in finger distance around their midpoint.
      const before = Math.hypot(previous.x - other.x, previous.y - other.y);
      const after = Math.hypot(point.x - other.x, point.y - other.y);
      if (before > 0) {
        const mx = (point.x + other.x) / 2;
        const my = (point.y + other.y) / 2;
        setView((v) => zoomAround(v, after / before, mx, my, boxRef.current));
      }
      dragged.current = true;
    } else {
      const dx = point.x - previous.x;
      const dy = point.y - previous.y;
      if (Math.abs(dx) + Math.abs(dy) > 1) dragged.current = true;
      setView((v) => clamp({ k: v.k, tx: v.tx + dx, ty: v.ty + dy }, boxRef.current));
    }
    pointers.current.set(event.pointerId, point);
  };

  const onPointerEnd = (event: React.PointerEvent<T>) => {
    pointers.current.delete(event.pointerId);
  };

  const onDoubleClick = (event: React.MouseEvent<T>) => {
    const point = toDrawing(event.clientX, event.clientY);
    const factor = event.shiftKey ? 1 / 2 : 2;
    setView((v) => zoomAround(v, factor, point.x, point.y, boxRef.current));
  };

  const onKeyDown = (event: React.KeyboardEvent<T>) => {
    const pan = (dx: number, dy: number) =>
      setView((v) => clamp({ k: v.k, tx: v.tx + dx, ty: v.ty + dy }, boxRef.current));
    const actions: Record<string, () => void> = {
      "+": zoomIn,
      "=": zoomIn,
      "-": zoomOut,
      "0": reset,
      ArrowLeft: () => pan(PAN_STEP, 0),
      ArrowRight: () => pan(-PAN_STEP, 0),
      ArrowUp: () => pan(0, PAN_STEP),
      ArrowDown: () => pan(0, -PAN_STEP),
    };
    const action = actions[event.key];
    if (action) {
      event.preventDefault();
      action();
    }
  };

  return {
    view,
    zoomed: view.k > 1.001,
    /** True right after a drag, so a hover readout can ignore it. */
    wasDragged: () => dragged.current,
    toDrawing,
    zoomIn,
    zoomOut,
    reset,
    bind: {
      ref: elementRef,
      onPointerDown,
      onPointerMove,
      onPointerUp: onPointerEnd,
      onPointerCancel: onPointerEnd,
      onDoubleClick,
      onKeyDown,
      tabIndex: 0,
    },
  };
}
