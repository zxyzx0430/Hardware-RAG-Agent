"""计算 I2C 上拉电阻值。

依据 I2C 标准的上拉电阻公式（基于上升时间与灌电流限制）：
  R_min = (Vcc - V_ol) / I_ol
  R_max = (Vcc - 0.4) / (C_bus * 0.874 * f_SCL)   # based on rise time
  recommended = (R_min + R_max) / 2

输入:
  args["vcc"]       电压 (V)，例如 3.3
  args["v_ol"]      输出低电平 (V)，例如 0.4
  args["i_ol"]      灌电流 (mA)，例如 3
  args["scl_freq"]  SCL 频率 (kHz)，例如 100
  args["c_bus_pf"]  总线电容 (pF)，可选，默认 100

输出: {"r_min": float, "r_max": float, "recommended": float, "unit": "ohm"}

典型用法：
  from src.agent.skills.calc_pullup_resistor import main
  main({"vcc": 3.3, "v_ol": 0.4, "i_ol": 3, "scl_freq": 100})
"""
from __future__ import annotations

from typing import Any

# Named constants
_RISE_TIME_CONST: float = 0.874       # rise-time approximation constant
_V_OL_RISE_REF: float = 0.4           # V_OL reference used in R_max formula (V)
_DEFAULT_BUS_CAP_PF: float = 100.0    # I2C typical bus capacitance (pF)
_MA_TO_A: float = 0.001               # milliamp → amp
_PF_TO_F: float = 1e-12               # picofarad → farad
_KHZ_TO_HZ: float = 1000.0            # kilohertz → hertz
_OHM_UNIT: str = "ohm"


def main(args: dict[str, Any]) -> dict:
    """Calculate I2C pull-up resistor range.

    Returns {"r_min", "r_max", "recommended", "unit"} on success,
    {"error": "..."} on missing/invalid input.
    """
    parsed = _parse_args(args)
    if isinstance(parsed, str):
        return _err(parsed)
    vcc, v_ol, i_ol_a, f_scl_hz, c_bus_f = parsed
    r_min = _calc_r_min(vcc, v_ol, i_ol_a)
    r_max = _calc_r_max(vcc, c_bus_f, f_scl_hz)
    if r_max <= 0 or r_min >= r_max:
        return _err(f"invalid range: r_min={r_min:.2f} >= r_max={r_max:.2f}")
    recommended = (r_min + r_max) / 2
    return {
        "r_min": round(r_min, 2),
        "r_max": round(r_max, 2),
        "recommended": round(recommended, 2),
        "unit": _OHM_UNIT,
    }


def _parse_args(args: dict[str, Any]) -> tuple[float, float, float, float, float] | str:
    """Validate and convert inputs; return tuple or error string."""
    try:
        vcc = float(args["vcc"])
        v_ol = float(args["v_ol"])
        i_ol_ma = float(args["i_ol"])
        f_scl_khz = float(args["scl_freq"])
    except (KeyError, TypeError, ValueError):
        return "missing/invalid vcc, v_ol, i_ol or scl_freq"
    c_bus_pf = float(args.get("c_bus_pf", _DEFAULT_BUS_CAP_PF))
    if vcc <= 0 or v_ol < 0 or i_ol_ma <= 0 or f_scl_khz <= 0 or c_bus_pf <= 0:
        return "all electrical params must be positive"
    i_ol_a = i_ol_ma * _MA_TO_A
    f_scl_hz = f_scl_khz * _KHZ_TO_HZ
    c_bus_f = c_bus_pf * _PF_TO_F
    return vcc, v_ol, i_ol_a, f_scl_hz, c_bus_f


def _calc_r_min(vcc: float, v_ol: float, i_ol_a: float) -> float:
    """R_min = (Vcc - V_ol) / I_ol."""
    return (vcc - v_ol) / i_ol_a


def _calc_r_max(vcc: float, c_bus_f: float, f_scl_hz: float) -> float:
    """R_max = (Vcc - 0.4) / (C_bus * 0.874 * f_SCL)."""
    return (vcc - _V_OL_RISE_REF) / (c_bus_f * _RISE_TIME_CONST * f_scl_hz)


def _err(msg: str) -> dict[str, Any]:
    """Build error result dict."""
    return {"error": msg}
