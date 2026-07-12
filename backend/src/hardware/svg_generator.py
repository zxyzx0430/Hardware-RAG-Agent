"""SVG wiring diagram generator with clean schematic-style layout.

Layout philosophy (inspired by KiCad/Fritzing schematics):
- MCU sits on the left as a tall vertical component.
- Peripherals are stacked vertically on the right with collision avoidance.
- Each logical net gets its own horizontal routing channel (bus).
- Wires are strictly orthogonal and never cross through component boxes.
- Power rails run along the top (3V3/VCC/5V) and bottom (GND) so that
  power connections become short vertical drops instead of long buses.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from typing import Any


MCU_BOX_WIDTH = 160
PERIPHERAL_BOX_WIDTH = 190
PIN_ROW_HEIGHT = 26
HEADER_HEIGHT = 32
MARGIN_X = 80
MARGIN_Y = 60
CHANNEL_GAP_X = 200
CHANNEL_GAP_Y = 48
WIRE_OFFSET = 20
LABEL_HEIGHT = 18
PERIPHERAL_GAP_Y = 70
MIN_DROP_SPACING = 24
PULLUP_DROP_MARGIN = 20
RESISTOR_H_LENGTH = 50
RESISTOR_V_LENGTH = 60
RAIL_HEIGHT = 14
RAIL_MARGIN = 20

POWER_COLOR = "#ef4444"
GND_COLOR = "#1e293b"


def _component_color(component_type: str) -> str:
    palette = {
        "mcu": "#f97316",
        "sensor": "#38bdf8",
        "actuator": "#ef4444",
        "display": "#a855f7",
        "power": "#22c55e",
        "module": "#eab308",
        "led": "#ef4444",
        "resistor": "#94a3b8",
    }
    return palette.get(component_type.lower(), "#64748b")


def _is_power_pin(pin_name: str) -> bool:
    pin = str(pin_name).upper().replace("-", "").replace(" ", "")
    if pin in ("GND", "VSS", "VEE", "AGND", "DGND"):
        return False
    return any(p in pin for p in ("3V3", "3.3V", "VCC", "VDD", "5V", "VIN", "VBAT"))


def _is_gnd_pin(pin_name: str) -> bool:
    pin = str(pin_name).upper().replace("-", "").replace(" ", "")
    return pin in ("GND", "VSS", "VEE", "AGND", "DGND")


def _is_rail_pin(pin_name: str) -> bool:
    return _is_gnd_pin(pin_name) or _is_power_pin(pin_name)


def _is_power_net_name(name: str) -> bool:
    return _is_power_pin(name) or _is_gnd_pin(name)


def _default_color_for_pin(pin_name: str) -> str:
    if _is_gnd_pin(pin_name):
        return GND_COLOR
    if _is_power_pin(pin_name):
        return POWER_COLOR
    return "#3b82f6"


def _normalize_connection(conn: dict[str, Any]) -> dict[str, Any]:
    from_pin = conn.get("from_pin") or conn.get("pin")
    return {
        "from_component": conn.get("from_component") or conn.get("from"),
        "from_pin": from_pin,
        "to_component": conn.get("to_component"),
        "to_pin": conn.get("to_pin"),
        "color": conn.get("color") or _default_color_for_pin(from_pin),
        "label": conn.get("label") or "",
    }


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _build_electrical_nodes(
    connections: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[str, str]]:
    uf = _UnionFind()
    for c in connections:
        a = (c["from_component"], c["from_pin"])
        b = (c["to_component"], c["to_pin"])
        uf.union(a, b)
    roots: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for key in uf._parent:
        roots[uf.find(key)].add(key)
    node_map: dict[tuple[str, str], tuple[str, str]] = {}
    for root, members in roots.items():
        for m in members:
            node_map[m] = root
    return node_map


def _name_nodes(
    node_map: dict[tuple[str, str], tuple[str, str]],
    mcu: dict[str, Any],
) -> dict[tuple[str, str], str]:
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for pin, root in node_map.items():
        groups[root].append(pin)
    names: dict[tuple[str, str], str] = {}
    mcu_name = str(mcu.get("name", "MCU"))
    for root, pins in groups.items():
        mcu_pins = [p for c, p in pins if c == mcu_name]
        if mcu_pins:
            name = str(mcu_pins[0])
        else:
            name = f"{pins[0][0]}:{pins[0][1]}"
        names[root] = name
    return names


def _find_mcu(components: list[dict[str, Any]]) -> dict[str, Any]:
    for c in components:
        if c.get("type", "").lower() == "mcu":
            return c
    return components[0] if components else {"name": "MCU", "type": "mcu", "pins": []}


def _trailing_number(name: str) -> int:
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


def _base_component_name(name: str) -> str:
    base = re.sub(r"\d+$", "", name).strip()
    return base if base else name


def _merge_similar_components(
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Merge repeated identical peripherals (e.g. 蜂鸣器x5) into one compact box.

    Returns the merged component list, updated connections, and a map of
    merged component name -> original quantity for the BOM.
    """
    groups: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    for c in components:
        ctype = str(c.get("type", "")).lower()
        pins = tuple(str(p) for p in (c.get("pins") or []))
        base = _base_component_name(str(c.get("name", "")))
        groups[(base, ctype, pins)].append(c)

    merged_components: list[dict[str, Any]] = []
    pin_mapping: dict[tuple[str, str], tuple[str, str]] = {}
    merged_qty: dict[str, int] = {}

    for (base, ctype, pins), group in groups.items():
        if len(group) >= 3 and ctype != "mcu":
            count = len(group)
            new_name = f"{base}×{count}"
            sorted_group = sorted(group, key=lambda c: _trailing_number(str(c.get("name", ""))))

            power_pins = [p for p in pins if _is_power_pin(p) and not _is_gnd_pin(p)]
            gnd_pins = [p for p in pins if _is_gnd_pin(p)]
            sig_pins = [p for p in pins if not _is_rail_pin(p)]

            new_pins: list[str] = []
            new_pins.extend(power_pins)
            for i in range(1, count + 1):
                for sig in sig_pins:
                    new_pins.append(f"{sig}{i}")
            new_pins.extend(gnd_pins)

            merged_components.append({"name": new_name, "type": ctype, "pins": new_pins})
            merged_qty[new_name] = count

            for i, old_c in enumerate(sorted_group, 1):
                old_name = str(old_c["name"])
                for sig in sig_pins:
                    pin_mapping[(old_name, sig)] = (new_name, f"{sig}{i}")
                for pp in power_pins:
                    pin_mapping[(old_name, pp)] = (new_name, pp)
                for gp in gnd_pins:
                    pin_mapping[(old_name, gp)] = (new_name, gp)
        else:
            for c in group:
                merged_components.append(c)

    new_connections: list[dict[str, Any]] = []
    for conn in connections:
        new_conn = dict(conn)
        to_comp = conn.get("to_component")
        to_pin = conn.get("to_pin")
        if (to_comp, to_pin) in pin_mapping:
            new_conn["to_component"], new_conn["to_pin"] = pin_mapping[(to_comp, to_pin)]
        new_connections.append(new_conn)

    return merged_components, new_connections, merged_qty


def _identify_resistors(
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    node_map: dict[tuple[str, str], tuple[str, str]],
    node_names: dict[tuple[str, str], str],
    mcu: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    mcu_name = str(mcu.get("name", "MCU"))
    comps_by_name = {str(c.get("name")): c for c in components}
    resistors = {
        str(c.get("name")): c
        for c in components
        if c.get("type", "").lower() == "resistor"
    }

    def node_has_mcu_gpio(root: tuple[str, str]) -> bool:
        for comp, pin in node_map:
            if node_map[(comp, pin)] == root and comp == mcu_name:
                if not _is_rail_pin(pin):
                    return True
        return False

    def node_has_power(root: tuple[str, str]) -> bool:
        for comp, pin in node_map:
            if node_map[(comp, pin)] == root and _is_power_pin(pin):
                return True
        return False

    def other_pins_of_component(comp_name: str, pin_name: str) -> list[tuple[str, str]]:
        comp = comps_by_name.get(comp_name)
        if not comp:
            return []
        return [
            (comp_name, p)
            for p in (comp.get("pins") or [])
            if p != pin_name
        ]

    result: dict[str, dict[str, Any]] = {}
    for rname, rcomp in resistors.items():
        pins = rcomp.get("pins") or []
        if len(pins) != 2:
            continue
        pa, pb = pins[0], pins[1]
        root_a = node_map.get((rname, pa))
        root_b = node_map.get((rname, pb))
        if root_a is None or root_b is None:
            continue

        a_power = node_has_power(root_a)
        b_power = node_has_power(root_b)
        a_gpio = node_has_mcu_gpio(root_a)
        b_gpio = node_has_mcu_gpio(root_b)

        # Pull-up: one side power, other side reaches an MCU GPIO through a destination component.
        if a_power or b_power:
            power_side = pa if a_power else pb
            signal_side = pb if a_power else pa
            signal_root = root_b if a_power else root_a
            signal_pin = (rname, signal_side)

            gpio_net = None
            dest_comp = None
            dest_pin = None
            for comp, pin in node_map:
                if node_map[(comp, pin)] != signal_root or (comp, pin) == signal_pin or comp == mcu_name:
                    continue
                for other_comp, other_pin in other_pins_of_component(comp, pin):
                    other_root = node_map.get((other_comp, other_pin))
                    if other_root and node_has_mcu_gpio(other_root):
                        gpio_net = node_names.get(other_root)
                        dest_comp = comp
                        dest_pin = pin
                        break
                if gpio_net:
                    break

            if gpio_net:
                result[rname] = {
                    "type": "pullup",
                    "power_pin": power_side,
                    "signal_pin": signal_side,
                    "power_node": node_names.get(root_a if a_power else root_b),
                    "signal_node": gpio_net,
                    "destination": (dest_comp, dest_pin),
                }
                continue

        # Current-limiting: one side MCU GPIO, other side non-MCU non-power load.
        if a_gpio != b_gpio:
            gpio_side = pa if a_gpio else pb
            load_side = pb if a_gpio else pa
            load_root = root_b if a_gpio else root_a
            load_pin = (rname, load_side)

            load_comp = None
            load_comp_pin = None
            for comp, pin in node_map:
                if node_map[(comp, pin)] != load_root or (comp, pin) == load_pin:
                    continue
                if comp != mcu_name and not _is_rail_pin(pin):
                    load_comp = comp
                    load_comp_pin = pin
                    break

            if load_comp:
                result[rname] = {
                    "type": "current",
                    "gpio_pin": gpio_side,
                    "load_pin": load_side,
                    "gpio_node": node_names.get(root_a if a_gpio else root_b),
                    "load_node": node_names.get(load_root),
                    "load_component": load_comp,
                    "load_component_pin": load_comp_pin,
                }
                continue

        result[rname] = {"type": "regular"}
    return result


def _primary_signal_pin(
    comp: dict[str, Any],
    connections: list[dict[str, Any]],
    node_map: dict[tuple[str, str], tuple[str, str]],
    node_names: dict[tuple[str, str], str],
    mcu: dict[str, Any],
) -> str | None:
    mcu_name = str(mcu.get("name", "MCU"))
    pins = comp.get("pins") or []
    if not pins:
        return None

    def pin_on_gpio(pin: str) -> bool:
        root = node_map.get((comp_name, pin))
        if not root:
            return False
        for c, p in node_map:
            if node_map[(c, p)] == root and c == mcu_name and not _is_rail_pin(p):
                return True
        return False

    comp_name = str(comp.get("name", ""))
    comp_type = comp.get("type", "").lower()

    if comp_type == "led":
        for pin in pins:
            if pin_on_gpio(pin):
                return pin
        for pin in pins:
            if not _is_rail_pin(pin):
                return pin
        return pins[0]

    if comp_type in ("button", "switch"):
        for pin in pins:
            if pin_on_gpio(pin):
                return pin
        for pin in pins:
            if not _is_gnd_pin(pin) and not _is_power_pin(pin):
                return pin
        return pins[0]

    if comp_type == "sensor":
        for prefer in ("DATA", "SIGNAL", "SIG", "OUT", "DOUT", "TX", "RX"):
            for pin in pins:
                if str(pin).upper() == prefer or str(pin).upper().startswith(prefer):
                    return pin
        for pin in pins:
            if pin_on_gpio(pin):
                return pin
        for pin in pins:
            if not _is_rail_pin(pin):
                return pin
        return pins[0]

    # Generic: prefer GPIO-connected, then first non-rail pin.
    for pin in pins:
        if pin_on_gpio(pin):
            return pin
    for pin in pins:
        if not _is_rail_pin(pin):
            return pin
    return pins[0]


def _compute_net_y_positions(
    node_map: dict[tuple[str, str], tuple[str, str]],
    node_names: dict[tuple[str, str], str],
    mcu: dict[str, Any],
    resistors: dict[str, dict[str, Any]],
    start_y: int,
) -> dict[str, int]:
    mcu_pins = mcu.get("pins", []) or []
    index = {str(p): i for i, p in enumerate(mcu_pins)}
    mcu_name = str(mcu.get("name", "MCU"))

    net_y: dict[str, int] = {}
    for (comp, pin), root in node_map.items():
        if comp == mcu_name:
            net = node_names[root]
            net_y[net] = start_y + index.get(pin, 0) * CHANNEL_GAP_Y

    # Derived nets (load side of current-limiting resistors) share the signal net y.
    for info in resistors.values():
        if info.get("type") == "current":
            gpio_node = info.get("gpio_node")
            load_node = info.get("load_node")
            if gpio_node in net_y and load_node:
                net_y[load_node] = net_y[gpio_node]

    return net_y


def _peripheral_height(peripheral: dict[str, Any]) -> int:
    pins = peripheral.get("pins", []) or []
    return HEADER_HEIGHT + max(80, len(pins) * PIN_ROW_HEIGHT + 24)


def _compute_peripheral_positions(
    components: list[dict[str, Any]],
    resistors: dict[str, dict[str, Any]],
    net_y: dict[str, int],
    node_map: dict[tuple[str, str], tuple[str, str]],
    node_names: dict[tuple[str, str], str],
    mcu: dict[str, Any],
    peri_x: int,
    mcu_y: int,
) -> dict[str, dict[str, Any]]:
    mcu_name = str(mcu.get("name", "MCU"))
    scored: list[tuple[int, dict[str, Any], int]] = []

    for c in components:
        name = str(c.get("name", ""))
        if c.get("type", "").lower() == "resistor" or name == mcu_name:
            continue
        primary = _primary_signal_pin(c, [], node_map, node_names, mcu)
        top_y = mcu_y + 50
        if primary is not None:
            root = node_map.get((name, primary))
            if root:
                net = node_names.get(root)
                if net in net_y:
                    pin_idx = (c.get("pins") or []).index(primary)
                    pin_offset_y = HEADER_HEIGHT + 18 + pin_idx * PIN_ROW_HEIGHT
                    top_y = net_y[net] - pin_offset_y
        height = _peripheral_height(c)
        scored.append((top_y, c, height))

    scored.sort(key=lambda x: x[0])
    positions: dict[str, dict[str, Any]] = {}
    current_bottom = mcu_y - PERIPHERAL_GAP_Y

    for _, c, height in scored:
        name = str(c.get("name", ""))
        primary = _primary_signal_pin(c, [], node_map, node_names, mcu)
        desired_top = mcu_y + 50
        if primary is not None:
            root = node_map.get((name, primary))
            if root:
                net = node_names.get(root)
                if net in net_y:
                    pin_idx = (c.get("pins") or []).index(primary)
                    pin_offset_y = HEADER_HEIGHT + 18 + pin_idx * PIN_ROW_HEIGHT
                    desired_top = net_y[net] - pin_offset_y

        desired_top = max(mcu_y, desired_top)
        if desired_top < current_bottom + PERIPHERAL_GAP_Y:
            desired_top = current_bottom + PERIPHERAL_GAP_Y

        positions[name] = {
            "x": peri_x,
            "y": desired_top,
            "width": PERIPHERAL_BOX_WIDTH,
            "height": height,
            "type": c.get("type", "module"),
            "pins": c.get("pins", []) or [],
        }
        current_bottom = desired_top + height

    return positions


def _draw_box(
    svg_parts: list[str],
    x: int,
    y: int,
    w: int,
    h: int,
    color: str,
    title: str,
    subtitle: str,
) -> None:
    # Drop shadow removed to keep node count low; the colored stroke is enough.
    svg_parts.append(
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ffffff" '
        f'stroke="{color}" stroke-width="2" rx="8"/>'
    )
    svg_parts.append(
        f'  <rect x="{x}" y="{y}" width="{w}" height="{HEADER_HEIGHT}" fill="{color}" opacity="0.15" rx="8"/>'
    )
    svg_parts.append(
        f'  <text x="{x + 12}" y="{y + 22}" font-family="ui-sans-serif, system-ui, sans-serif" '
        f'font-size="14" font-weight="700" fill="#1e293b">{html.escape(title)}</text>'
    )


def _draw_resistor_horizontal(
    svg_parts: list[str],
    x_left: int,
    x_right: int,
    y: int,
    color: str = "#475569",
) -> None:
    width = x_right - x_left
    segments = 8
    step = width / segments
    zig = 8
    points = [f"{x_left},{y}"]
    for i in range(1, segments):
        px = x_left + i * step
        dy = zig if i % 2 == 1 else -zig
        points.append(f"{px},{y + dy}")
    points.append(f"{x_right},{y}")
    svg_parts.append(
        f'  <polyline points="{" ".join(points)}" '
        f'fill="none" stroke="{html.escape(color)}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    svg_parts.append(f'  <circle cx="{x_left}" cy="{y}" r="3" fill="{html.escape(color)}"/>')
    svg_parts.append(f'  <circle cx="{x_right}" cy="{y}" r="3" fill="{html.escape(color)}"/>')


def _draw_resistor_vertical(
    svg_parts: list[str],
    x: int,
    y_top: int,
    y_bottom: int,
    color: str = "#475569",
) -> None:
    height = y_bottom - y_top
    segments = 8
    step = height / segments
    zig = 6
    points = [f"{x},{y_top}"]
    for i in range(1, segments):
        py = y_top + i * step
        dx = zig if i % 2 == 1 else -zig
        points.append(f"{x + dx},{py}")
    points.append(f"{x},{y_bottom}")
    svg_parts.append(
        f'  <polyline points="{" ".join(points)}" '
        f'fill="none" stroke="{html.escape(color)}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    svg_parts.append(f'  <circle cx="{x}" cy="{y_top}" r="3" fill="{html.escape(color)}"/>')
    svg_parts.append(f'  <circle cx="{x}" cy="{y_bottom}" r="3" fill="{html.escape(color)}"/>')


def _compute_drop_xs(
    destinations: list[tuple[int, int, dict[str, Any]]],
    bus_x_start: int,
    bus_x_end: int,
) -> list[tuple[int, tuple[int, int, dict[str, Any]]]]:
    if len(destinations) == 1:
        dy, dx, conn = destinations[0]
        return [(dx - WIRE_OFFSET, destinations[0])]

    min_dx = min(dx for _, dx, _ in destinations)
    lower = bus_x_start + 20
    upper = min_dx - 10
    count = len(destinations)
    needed = (count - 1) * MIN_DROP_SPACING
    if upper - lower < needed:
        # Not enough room: compress spacing and shift upper if possible.
        available = max(upper - lower, 0)
        spacing = available // max(count - 1, 1)
    else:
        spacing = MIN_DROP_SPACING

    # Sort by Y to give stable left-to-right placement.
    sorted_dests = sorted(destinations, key=lambda d: d[0])
    result: list[tuple[int, tuple[int, int, dict[str, Any]]]] = []
    current_x = lower
    for i, dest in enumerate(sorted_dests):
        _, dx, _ = dest
        x = min(current_x, dx - 10)
        result.append((x, dest))
        current_x += spacing
    return result


def _draw_rail(
    svg_parts: list[str],
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
    label: str,
) -> None:
    mid_y = y + height // 2
    svg_parts.append(
        f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{height // 2}" fill="{color}" stroke="{color}" stroke-width="1"/>'
    )
    svg_parts.append(
        f'  <text x="{x + width // 2}" y="{mid_y + 4}" text-anchor="middle" '
        f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="11" '
        f'font-weight="600" fill="#ffffff">{html.escape(label)}</text>'
    )


def _draw_peripheral_power_drop(
    svg_parts: list[str],
    pin_x: int,
    pin_y: int,
    rail_y: int,
    color: str,
    tap_x_offset: int = WIRE_OFFSET,
) -> None:
    """Draw a peripheral power pin drop: left out of the box, then up/down to the rail."""
    joint_y = rail_y + RAIL_HEIGHT // 2
    tap_x = pin_x - tap_x_offset
    svg_parts.append(
        f'  <polyline points="{pin_x},{pin_y} {tap_x},{pin_y} '
        f'{tap_x},{joint_y}" '
        f'fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
    )


def _draw_mcu_power_drop(
    svg_parts: list[str],
    pin_x: int,
    pin_y: int,
    rail_y: int,
    color: str,
) -> None:
    """Draw an MCU power pin drop: straight up/down to the rail."""
    joint_y = rail_y + RAIL_HEIGHT // 2
    svg_parts.append(
        f'  <polyline points="{pin_x},{pin_y} {pin_x},{joint_y}" '
        f'fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
    )


def generate_wiring_svg(
    title: str,
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    components = components or []
    connections = [_normalize_connection(c) for c in (connections or [])]

    components, connections, merged_qty = _merge_similar_components(components, connections)

    mcu = _find_mcu(components)
    mcu_name = str(mcu.get("name", "MCU"))
    mcu_pins = mcu.get("pins", []) or []
    mcu_pin_index = {str(p): i for i, p in enumerate(mcu_pins)}

    node_map = _build_electrical_nodes(connections)
    node_names = _name_nodes(node_map, mcu)
    resistors = _identify_resistors(components, connections, node_map, node_names, mcu)

    mcu_height = HEADER_HEIGHT + max(120, len(mcu_pins) * PIN_ROW_HEIGHT + 24)
    mcu_x = MARGIN_X
    mcu_y = MARGIN_Y + 60
    mcu_w = MCU_BOX_WIDTH

    bus_x_start = mcu_x + mcu_w + WIRE_OFFSET
    bus_x_end = mcu_x + mcu_w + CHANNEL_GAP_X
    peri_x = bus_x_end + WIRE_OFFSET
    peri_w = PERIPHERAL_BOX_WIDTH

    # Net y positions based on MCU pin order.
    num_mcu_nets = sum(
        1 for name in node_names.values() if name in mcu_pin_index
    )
    if num_mcu_nets:
        net_y_base = mcu_y + (mcu_height - (num_mcu_nets - 1) * CHANNEL_GAP_Y) // 2
        net_y_base = max(mcu_y + HEADER_HEIGHT + 20, net_y_base)
    else:
        net_y_base = mcu_y + HEADER_HEIGHT + 20

    net_y = _compute_net_y_positions(node_map, node_names, mcu, resistors, net_y_base)

    # Compute peripheral positions first so we know pin coordinates.
    peripheral_positions = _compute_peripheral_positions(
        components, resistors, net_y, node_map, node_names, mcu, peri_x, mcu_y,
    )

    # Pin coordinates.
    mcu_pin_y: dict[str, int] = {}
    for idx, pin in enumerate(mcu_pins):
        py = mcu_y + HEADER_HEIGHT + 18 + idx * PIN_ROW_HEIGHT
        mcu_pin_y[str(pin)] = py

    peri_pin_coords: dict[tuple[str, str], tuple[int, int]] = {}
    for name, pos in peripheral_positions.items():
        for idx, pin in enumerate(pos["pins"]):
            py = pos["y"] + HEADER_HEIGHT + 18 + idx * PIN_ROW_HEIGHT
            peri_pin_coords[(name, str(pin))] = (pos["x"], py)

    # Compute resistor anchor positions.
    for rname, info in resistors.items():
        if info.get("type") == "pullup":
            dest_comp, dest_pin = info.get("destination", (None, None))
            dest_x = peri_pin_coords.get((dest_comp, dest_pin), (peri_x, net_y_base))[0]
            # Place pull-up resistor near destination but left of it.
            rx = max(bus_x_end + PULLUP_DROP_MARGIN, dest_x - RESISTOR_H_LENGTH - WIRE_OFFSET)
            info["x"] = rx
            info["y_top"] = net_y.get(info.get("power_node"), net_y_base)
            info["y_bottom"] = net_y.get(info.get("signal_node"), net_y_base)
        elif info.get("type") == "current":
            load_comp = info.get("load_component")
            load_pin = info.get("load_component_pin")
            load_x = peri_pin_coords.get((load_comp, load_pin), (peri_x, net_y_base))[0]
            ry = net_y.get(info.get("gpio_node"), net_y_base)
            # Place resistor between bus and load.
            right_x = max(bus_x_end + RESISTOR_H_LENGTH + 20, load_x - WIRE_OFFSET)
            left_x = right_x - RESISTOR_H_LENGTH
            info["y"] = ry
            info["x_left"] = left_x
            info["x_right"] = right_x

    canvas_width = max(900, peri_x + peri_w + MARGIN_X)
    max_peri_bottom = max(
        (p["y"] + p["height"] for p in peripheral_positions.values()),
        default=mcu_y + mcu_height,
    )

    power_nets = {name for name in node_names.values() if _is_power_net_name(name)}
    has_power = bool(power_nets)
    top_rail_y = mcu_y - RAIL_HEIGHT - RAIL_MARGIN
    bottom_rail_y = mcu_y + mcu_height + RAIL_MARGIN
    rail_width = canvas_width - 2 * MARGIN_X
    rail_center_x = MARGIN_X + rail_width // 2

    canvas_height = max(
        400,
        mcu_y + mcu_height + MARGIN_Y + 40,
        max_peri_bottom + MARGIN_Y + 40,
        MARGIN_Y * 2 + len(net_y) * CHANNEL_GAP_Y + 120,
        bottom_rail_y + RAIL_HEIGHT + MARGIN_Y + 40,
    )

    svg_parts: list[str] = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_width} {canvas_height}">'
    )
    svg_parts.append(f'  <rect width="{canvas_width}" height="{canvas_height}" fill="#f8fafc" rx="8"/>')
    svg_parts.append(
        f'  <text x="{canvas_width // 2}" y="36" text-anchor="middle" '
        f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="18" font-weight="600" fill="#1e293b">{html.escape(title)}</text>'
    )

    # Draw power rails first so wires appear on top.
    if has_power:
        _draw_rail(svg_parts, MARGIN_X, top_rail_y, rail_width, RAIL_HEIGHT, POWER_COLOR, "3V3")
        _draw_rail(svg_parts, MARGIN_X, bottom_rail_y, rail_width, RAIL_HEIGHT, GND_COLOR, "GND")

    # MCU box and pins.
    _draw_box(
        svg_parts, mcu_x, mcu_y, mcu_w, mcu_height,
        _component_color(mcu.get("type", "mcu")),
        mcu_name,
        str(mcu.get("type", "mcu")),
    )
    mcu_color = _component_color(mcu.get("type", "mcu"))
    for idx, pin in enumerate(mcu_pins):
        py = mcu_pin_y[str(pin)]
        px = mcu_x + mcu_w
        svg_parts.append(
            f'  <circle cx="{px}" cy="{py}" r="4" fill="#ffffff" stroke="{mcu_color}" stroke-width="2"/>'
        )
        svg_parts.append(
            f'  <text x="{px - 10}" y="{py + 4}" text-anchor="end" font-family="ui-monospace, monospace" '
            f'font-size="12" font-weight="500" fill="#334155">{html.escape(str(pin))}</text>'
        )

    # Peripheral boxes and pins.
    for name, pos in peripheral_positions.items():
        _draw_box(
            svg_parts, pos["x"], pos["y"], pos["width"], pos["height"],
            _component_color(pos["type"]), name, pos["type"],
        )
        peri_color = _component_color(pos["type"])
        for idx, pin in enumerate(pos["pins"]):
            py = pos["y"] + HEADER_HEIGHT + 18 + idx * PIN_ROW_HEIGHT
            px = pos["x"]
            peri_pin_coords[(name, str(pin))] = (px, py)
            svg_parts.append(
                f'  <circle cx="{px}" cy="{py}" r="4" fill="#ffffff" stroke="{peri_color}" stroke-width="2"/>'
            )
            svg_parts.append(
                f'  <text x="{px + 10}" y="{py + 4}" font-family="ui-monospace, monospace" '
                f'font-size="12" font-weight="500" fill="#334155">{html.escape(str(pin))}</text>'
            )

    # Draw regular resistors as peripheral boxes (already drawn above).
    # Draw pull-up and current-limiting resistors as inline symbols.
    for rname, info in resistors.items():
        if info.get("type") == "pullup":
            _draw_resistor_vertical(
                svg_parts, info["x"], info["y_top"], info["y_bottom"],
            )
        elif info.get("type") == "current":
            _draw_resistor_horizontal(
                svg_parts, info["x_left"], info["x_right"], info["y"],
            )

    # Pins handled by special resistors are excluded from normal net routing.
    excluded_pins: set[tuple[str, str]] = set()
    for rname, info in resistors.items():
        if info.get("type") == "pullup":
            excluded_pins.add((rname, info["power_pin"]))
            excluded_pins.add((rname, info["signal_pin"]))
        elif info.get("type") == "current":
            excluded_pins.add((rname, info["gpio_pin"]))
            excluded_pins.add((rname, info["load_pin"]))

    # Draw direct power drops for MCU and peripheral power pins.
    if has_power:
        # MCU power pins to rails.
        for pin in mcu_pins:
            py = mcu_pin_y.get(str(pin))
            if py is None:
                continue
            if _is_power_pin(pin):
                _draw_mcu_power_drop(svg_parts, mcu_x + mcu_w, py, top_rail_y, POWER_COLOR)
            elif _is_gnd_pin(pin):
                _draw_mcu_power_drop(svg_parts, mcu_x + mcu_w, py, bottom_rail_y, GND_COLOR)

        # Peripheral power pins to rails.
        # Sort top-to-bottom and stagger tap-x so drops don't overlap vertically.
        sorted_peris = sorted(peripheral_positions.items(), key=lambda item: item[1]["y"])
        for stack_idx, (name, pos) in enumerate(sorted_peris):
            tap_x_offset = WIRE_OFFSET + stack_idx * 8
            for idx, pin in enumerate(pos["pins"]):
                px, py = peri_pin_coords[(name, str(pin))]
                if _is_power_pin(pin):
                    _draw_peripheral_power_drop(svg_parts, px, py, top_rail_y, POWER_COLOR, tap_x_offset)
                elif _is_gnd_pin(pin):
                    _draw_peripheral_power_drop(svg_parts, px, py, bottom_rail_y, GND_COLOR, tap_x_offset)

    # Build net sources and destinations from electrical-node membership.
    net_source_pin: dict[str, str] = {}
    net_dests: dict[str, list[tuple[int, int, dict[str, Any]]]] = defaultdict(list)

    # Group pins by net.
    net_pins: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (comp, pin), root in node_map.items():
        net = node_names[root]
        net_pins[net].append((comp, pin))

    for net, pins in net_pins.items():
        if _is_power_net_name(net):
            continue
        source_comp = None
        source_pin = None
        # Prefer MCU pin as source.
        for comp, pin in pins:
            if comp == mcu_name:
                source_comp = comp
                source_pin = pin
                break
        # Fall back to current-limiting resistor load pin for derived nets.
        if source_pin is None:
            for rname, info in resistors.items():
                if info.get("type") == "current" and info.get("load_node") == net:
                    source_comp = rname
                    source_pin = info["load_pin"]
                    break
        if source_pin is None:
            continue
        net_source_pin[net] = source_pin

        for comp, pin in pins:
            if comp == mcu_name:
                continue
            if (comp, pin) in excluded_pins:
                continue
            if _is_rail_pin(pin):
                continue
            coord = peri_pin_coords.get((comp, pin))
            if coord is None:
                continue
            synthetic = {
                "from_component": source_comp or mcu_name,
                "from_pin": source_pin,
                "to_component": comp,
                "to_pin": pin,
                "color": _default_color_for_pin(source_pin),
                "label": "",
            }
            net_dests[net].append((coord[1], coord[0], synthetic))

    # Draw buses and drops.
    for net, dests in net_dests.items():
        source_pin = net_source_pin.get(net)
        bus_y = net_y.get(net)
        if bus_y is None or source_pin is None:
            continue
        source_color = _default_color_for_pin(source_pin)

        # Source pin to bus.
        source_py = mcu_pin_y.get(source_pin)
        if source_py is None:
            continue

        # Always use a shared horizontal bus so that each signal net has a unique
        # vertical channel and drops do not pile up on the same x coordinate.
        # Stagger drop-x by source pin index so different nets use different columns.
        source_pin_index = mcu_pin_index.get(source_pin, 0)
        first_dx = dests[0][1] if dests else bus_x_end
        available = max(0, first_dx - 10 - bus_x_start - 10)
        stagger_step = min(16, available // max(1, len(mcu_pin_index)))
        drop_x = first_dx - 10 - (source_pin_index % 10) * stagger_step
        drop_x = max(drop_x, bus_x_start + 10)
        drops: list[tuple[int, tuple[int, int, dict[str, Any]]]] = []
        for dest in dests:
            drops.append((drop_x, dest))
        actual_bus_end = max(bus_x_start + 20, max((x for x, _ in drops), default=bus_x_end) + 10)

        svg_parts.append(
            f'  <polyline points="{mcu_x + mcu_w},{source_py} {bus_x_start},{source_py} '
            f'{bus_x_start},{bus_y} {actual_bus_end},{bus_y}" '
            f'fill="none" stroke="{html.escape(source_color)}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )

        for drop_x, (dy, dx, conn) in drops:
            color = conn.get("color") or source_color
            svg_parts.append(
                f'  <polyline points="{actual_bus_end},{bus_y} {drop_x},{bus_y} '
                f'{drop_x},{dy} {dx},{dy}" '
                f'fill="none" stroke="{html.escape(color)}" stroke-width="2" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
            )

            label = conn.get("label")
            pin_name = conn.get("to_pin", "")
            if label and str(label).lower() != str(pin_name).lower():
                label_w = len(str(label)) * 7 + 10
                label_x = dx + 10
                label_y = dy - 8
                peri_pos = peripheral_positions.get(conn["to_component"])
                if peri_pos and label_x + label_w > peri_pos["x"] + peri_pos["width"] - 4:
                    label_x = peri_pos["x"] + peri_pos["width"] - label_w - 6
                svg_parts.append(
                    f'  <rect x="{label_x - 2}" y="{label_y - 10}" width="{label_w}" '
                    f'height="{LABEL_HEIGHT}" fill="#ffffff" opacity="0.92" rx="3" '
                    f'stroke="#e2e8f0" stroke-width="0.5"/>'
                )
                svg_parts.append(
                    f'  <text x="{label_x + 3}" y="{label_y + 3}" font-family="ui-sans-serif, system-ui, sans-serif" '
                    f'font-size="10" font-weight="500" fill="#475569">{html.escape(str(label))}</text>'
                )

    # Draw pull-up connections: power bus -> resistor top, resistor bottom -> signal bus.
    for info in resistors.values():
        if info.get("type") != "pullup":
            continue
        power_y = info["y_top"]
        signal_y = info["y_bottom"]
        rx = info["x"]
        power_color = "#ef4444"
        signal_color = "#3b82f6"
        # Power bus to resistor top.
        svg_parts.append(
            f'  <polyline points="{bus_x_end},{power_y} {rx},{power_y}" '
            f'fill="none" stroke="{power_color}" stroke-width="2.5" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )
        # Resistor bottom to signal bus.
        svg_parts.append(
            f'  <polyline points="{rx},{signal_y} {bus_x_end},{signal_y}" '
            f'fill="none" stroke="{signal_color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )

    # Draw current-limiting connections: bus -> resistor left, resistor right -> load pin.
    for info in resistors.values():
        if info.get("type") != "current":
            continue
        y = info["y"]
        left_x = info["x_left"]
        right_x = info["x_right"]
        load_comp = info.get("load_component")
        load_pin = info.get("load_component_pin")
        load_coord = peri_pin_coords.get((load_comp, load_pin))
        if load_coord is None:
            continue
        lx, ly = load_coord
        color = "#3b82f6"
        # Bus to resistor left.
        svg_parts.append(
            f'  <polyline points="{bus_x_end},{y} {left_x},{y}" '
            f'fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )
        # Resistor right to load pin.
        svg_parts.append(
            f'  <polyline points="{right_x},{y} {lx - WIRE_OFFSET},{y} '
            f'{lx - WIRE_OFFSET},{ly} {lx},{ly}" '
            f'fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
        )

    svg_parts.append("</svg>")
    svg = "\n".join(svg_parts)

    name_counts = Counter(str(c.get("name", "Unknown")) for c in components)
    bom = []
    for name, qty in sorted(name_counts.items()):
        if name in merged_qty:
            qty = merged_qty[name]
        bom.append({"component": name, "qty": qty})

    return svg, bom
