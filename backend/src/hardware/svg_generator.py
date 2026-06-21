"""Simple SVG wiring diagram generator.

Takes a list of components and connections and produces a readable SVG
schematic plus a BOM inference.
"""

from __future__ import annotations

import html
from collections import Counter
from typing import Any


BOX_WIDTH = 180
PIN_ROW_HEIGHT = 22
HEADER_HEIGHT = 34
MARGIN_X = 60
MARGIN_Y = 50
SPACING_X = 220


def _pin_y(box_top: int, index: int) -> int:
    return box_top + HEADER_HEIGHT + 12 + index * PIN_ROW_HEIGHT


def _component_color(component_type: str) -> str:
    palette = {
        "mcu": "#f97316",
        "sensor": "#38bdf8",
        "actuator": "#ef4444",
        "display": "#a855f7",
        "power": "#22c55e",
        "module": "#eab308",
    }
    return palette.get(component_type.lower(), "#64748b")


def generate_wiring_svg(
    title: str,
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Generate a wiring diagram SVG and a BOM list.

    Args:
        title: Diagram title rendered at the top.
        components: List of components with keys ``name``, ``type``, ``pins``.
        connections: List of connections with keys ``from_component``,
            ``from_pin``, ``to_component``, ``to_pin``, ``color``, ``label``.

    Returns:
        Tuple of ``(svg_string, bom_list)`` where ``bom_list`` contains
        ``{"component": str, "qty": int}`` items.
    """
    # Normalize empty inputs
    components = components or []
    connections = connections or []

    # Compute layout
    num_components = len(components)
    canvas_width = max(800, MARGIN_X * 2 + max(1, num_components) * SPACING_X)
    max_pins = max((len(c.get("pins", [])) for c in components), default=1)
    box_height = HEADER_HEIGHT + max(28, max_pins * PIN_ROW_HEIGHT + 16)
    canvas_height = max(360, box_height + MARGIN_Y * 2 + 80)

    # Component positions and pin coordinate map
    positions: dict[str, dict[str, Any]] = {}
    pin_coords: dict[tuple[str, str], tuple[int, int]] = {}
    start_x = (canvas_width - max(1, num_components) * SPACING_X) // 2 + 40

    for i, comp in enumerate(components):
        name = str(comp.get("name", f"C{i}"))
        ctype = str(comp.get("type", "module"))
        pins = comp.get("pins", []) or []

        x = start_x + i * SPACING_X
        y = MARGIN_Y + 40
        positions[name] = {
            "x": x,
            "y": y,
            "width": BOX_WIDTH,
            "height": box_height,
            "type": ctype,
            "pins": pins,
        }
        for idx, pin in enumerate(pins):
            pin_coords[(name, str(pin))] = (x + 12, _pin_y(y, idx))

    # Build SVG parts
    svg_parts: list[str] = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_width} {canvas_height}">'
    )
    svg_parts.append(f'  <rect width="{canvas_width}" height="{canvas_height}" fill="#f8fafc" rx="8"/>')
    svg_parts.append(
        f'  <text x="{canvas_width // 2}" y="36" text-anchor="middle" '
        f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="18" fill="#1e293b">{html.escape(title)}</text>'
    )

    # Draw components
    for name, pos in positions.items():
        x, y = pos["x"], pos["y"]
        w, h = pos["width"], pos["height"]
        color = _component_color(pos["type"])

        # Box shadow
        svg_parts.append(
            f'  <rect x="{x + 3}" y="{y + 3}" width="{w}" height="{h}" fill="#000000" opacity="0.06" rx="6"/>'
        )
        # Main box
        svg_parts.append(
            f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ffffff" '
            f'stroke="{color}" stroke-width="2" rx="6"/>'
        )
        # Header bar
        svg_parts.append(
            f'  <rect x="{x}" y="{y}" width="{w}" height="{HEADER_HEIGHT}" fill="{color}" opacity="0.12" rx="6"/>'
        )
        # Component name & type
        svg_parts.append(
            f'  <text x="{x + 10}" y="{y + 22}" font-family="ui-sans-serif, system-ui, sans-serif" '
            f'font-size="13" font-weight="600" fill="#1e293b">{html.escape(name)}</text>'
        )
        svg_parts.append(
            f'  <text x="{x + w - 10}" y="{y + 22}" text-anchor="end" font-family="ui-sans-serif, system-ui, sans-serif" '
            f'font-size="11" fill="{color}">{html.escape(pos["type"].upper())}</text>'
        )

        # Pins
        for idx, pin in enumerate(pos["pins"]):
            py = _pin_y(y, idx)
            svg_parts.append(
                f'  <circle cx="{x + 12}" cy="{py}" r="4" fill="{color}"/>'
            )
            svg_parts.append(
                f'  <text x="{x + 24}" y="{py + 4}" font-family="ui-monospace, monospace" '
                f'font-size="12" fill="#334155">{html.escape(str(pin))}</text>'
            )

    # Draw connections
    for conn in connections:
        from_comp = conn.get("from_component") or conn.get("from")
        from_pin = conn.get("from_pin") or conn.get("pin")
        to_comp = conn.get("to_component")
        to_pin = conn.get("to_pin")
        color = conn.get("color") or "#38bdf8"
        label = conn.get("label") or ""

        start = pin_coords.get((from_comp, from_pin))
        end = pin_coords.get((to_comp, to_pin))
        if not start or not end:
            continue

        x1, y1 = start
        x2, y2 = end

        # Orthogonal polyline: exit left, horizontal, vertical, horizontal approach
        mid_x = (x1 + x2) // 2
        points = f"{x1},{y1} {x1 - 20},{y1} {x1 - 20},{y2} {x2},{y2}"
        if abs(x1 - x2) < 40:
            # Direct line for nearby components
            points = f"{x1},{y1} {x2},{y2}"

        svg_parts.append(
            f'  <polyline points="{points}" fill="none" stroke="{html.escape(color)}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )

        if label:
            mid_x_label = (x1 + x2) // 2
            mid_y_label = (y1 + y2) // 2 - 6
            svg_parts.append(
                f'  <text x="{mid_x_label}" y="{mid_y_label}" text-anchor="middle" '
                f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="11" fill="#475569">{html.escape(str(label))}</text>'
            )

    svg_parts.append("</svg>")
    svg = "\n".join(svg_parts)

    # BOM inference: count duplicate component names
    name_counts = Counter(str(c.get("name", "Unknown")) for c in components)
    bom = [{"component": name, "qty": qty} for name, qty in sorted(name_counts.items())]

    return svg, bom
