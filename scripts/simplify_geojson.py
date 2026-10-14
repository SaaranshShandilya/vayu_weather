#!/usr/bin/env python3
"""Create a small browser-ready India state boundary file.

The source may expose its state name as ``NAME_1`` or ``region``. This script
normalises known historical spellings, keeps only the state name, and
simplifies every ring with the Ramer-Douglas-Peucker algorithm. It is
deterministic and uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


STATE_NAMES = {
    "Andaman and Nicobar": "Andaman and Nicobar Islands",
    "Chandigarth": "Chandigarh",
    "NCT of Delhi": "Delhi",
    "Orissa": "Odisha",
    "Uttaranchal": "Uttarakhand",
}


def distance(point: list[float], start: list[float], end: list[float]) -> float:
    if start == end:
        return math.dist(point, start)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    scale = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (
        dx * dx + dy * dy
    )
    projection = [start[0] + scale * dx, start[1] + scale * dy]
    return math.dist(point, projection)


def simplify(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) <= 3:
        return points
    start, end = points[0], points[-1]
    index, maximum = 0, 0.0
    for current in range(1, len(points) - 1):
        value = distance(points[current], start, end)
        if value > maximum:
            index, maximum = current, value
    if maximum <= tolerance:
        return [start, end]
    left = simplify(points[: index + 1], tolerance)
    right = simplify(points[index:], tolerance)
    return left[:-1] + right


def simplify_ring(points: list[list[float]], tolerance: float) -> list[list[float]]:
    open_ring = points[:-1] if points[0] == points[-1] else points[:]
    if len(open_ring) < 4:
        return points
    anchor = open_ring[0]
    split = max(
        range(1, len(open_ring)),
        key=lambda index: math.dist(anchor, open_ring[index]),
    )
    first = simplify(open_ring[: split + 1], tolerance)
    second = simplify(open_ring[split:] + [anchor], tolerance)
    ring = first[:-1] + second
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else points


def simplify_coordinates(geometry: dict, tolerance: float):
    if geometry["type"] == "Polygon":
        return [
            simplify_ring(ring, tolerance)
            for ring in geometry["coordinates"]
        ]
    if geometry["type"] == "MultiPolygon":
        return [
            [simplify_ring(ring, tolerance) for ring in polygon]
            for polygon in geometry["coordinates"]
        ]
    raise ValueError(f"Unsupported geometry type: {geometry['type']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.025)
    args = parser.parse_args()

    payload = json.loads(args.source.read_text())
    features = []
    for feature in payload["features"]:
        properties = feature["properties"]
        raw_name = properties.get("NAME_1") or properties.get("region")
        if not raw_name:
            raise ValueError("Every feature must contain NAME_1 or region")
        geometry = feature["geometry"]
        features.append(
            {
                "type": "Feature",
                "properties": {"state": STATE_NAMES.get(raw_name, raw_name)},
                "geometry": {
                    "type": geometry["type"],
                    "coordinates": simplify_coordinates(
                        geometry,
                        args.tolerance,
                    ),
                },
            }
        )
    result = {"type": "FeatureCollection", "features": features}
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
