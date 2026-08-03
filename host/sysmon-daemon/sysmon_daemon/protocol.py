"""S3 wire protocol: pure formatting/parsing, no I/O.

Line format (fixed field order, '|' separator, LF terminated by the caller):

    S3|<cpu_total>|<ram_used_mb>|<ram_free_mb>|<ram_pressure_pct>|
       <net_up_kbps>|<net_down_kbps>|<disk_used_gb>|<disk_free_gb>|
       <temp_c>|<thermal_state>|<net_iface>

- cpu_total: integer 0-100 (clamped);
- ram_used_mb / ram_free_mb: non-negative integers (MiB);
- ram_pressure_pct: integer 0-100 or '-' (N/A) — the firmware uses it only
  to pick the MEM bar color, the number itself is no longer displayed;
- net_up_kbps / net_down_kbps / disk_used_gb / disk_free_gb: non-negative,
  rendered with exactly one decimal digit (the firmware accepts at most one);
  disk values are DECIMAL GB (bytes / 1e9), matching what macOS shows;
- temp_c: one decimal digit, optional leading '-', or '-' alone for N/A;
- thermal_state: one of nominal|fair|serious|critical, or '-' for N/A;
- net_iface: token of 1-7 chars from [A-Z0-9-] (e.g. "WI-FI", "ETH",
  "VPN"), or '-' alone for N/A (the firmware hides the badge on '-').

S3 replaces the S1/S2 total fields with used/free pairs: ram_free_mb
instead of ram_total_mb, disk_used_gb+disk_free_gb instead of
disk_free_gb+disk_total_gb. The daemon always sends S3; backward
compatibility with S1/S2 lives in the firmware parser, so parse_line here
(used only for round-trip tests and debugging) accepts S3 alone.

Handshake: the host sends "PING\\n", the device answers "SYSMON1\\n".
"""

import math
import re
from dataclasses import dataclass
from typing import Optional

PING = "PING"
HELLO = "SYSMON1"

PREFIX = "S3"
NA = "-"
FIELD_COUNT = 12  # including the "S3" prefix field

THERMAL_STATES = frozenset({"nominal", "fair", "serious", "critical"})

IFACE_MAX_LEN = 7

_X10_RE = re.compile(r"\d+(?:\.\d)?")
_TEMP_RE = re.compile(r"-?\d+(?:\.\d)?")
_IFACE_RE = re.compile(r"[A-Z0-9-]{1,7}")


@dataclass
class Snapshot:
    cpu_total: int
    ram_used_mb: int
    ram_free_mb: int
    ram_pressure_pct: Optional[int]
    net_up_kbps: float
    net_down_kbps: float
    disk_used_gb: float
    disk_free_gb: float
    temp_c: Optional[float]
    thermal_state: Optional[str]
    net_iface: Optional[str] = None


def _fmt_uint(value: int) -> str:
    """Non-negative integer field (firmware accepts digits only)."""
    return str(max(0, int(value)))


def _fmt_pct(value: int) -> str:
    return str(max(0, min(100, int(round(value)))))


def _fmt1(value: float) -> str:
    """Non-negative fixed-point field with exactly one decimal digit."""
    if not math.isfinite(value) or value < 0.0:
        value = 0.0
    return "{0:.1f}".format(value)


def _fmt_temp(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return NA
    s = "{0:.1f}".format(value)
    # Avoid "-0.0": the sign is pointless and only tests firmware edge cases.
    return "0.0" if s == "-0.0" else s


def _fmt_thermal(value: Optional[str]) -> str:
    return value if value in THERMAL_STATES else NA


def _fmt_iface(value: Optional[str]) -> str:
    """Normalize (strip, uppercase) and validate the iface token.

    Anything that does not survive as 1-7 chars of [A-Z0-9-] degrades to
    '-' (N/A). A bare '-' is itself the N/A token.
    """
    if value is None:
        return NA
    token = str(value).strip().upper()
    if not _IFACE_RE.fullmatch(token):
        return NA
    return token


def format_line(s: Snapshot) -> str:
    """Render a Snapshot as an S3 line (without the trailing newline)."""
    fields = [
        PREFIX,
        _fmt_pct(s.cpu_total),
        _fmt_uint(s.ram_used_mb),
        _fmt_uint(s.ram_free_mb),
        NA if s.ram_pressure_pct is None else _fmt_pct(s.ram_pressure_pct),
        _fmt1(s.net_up_kbps),
        _fmt1(s.net_down_kbps),
        _fmt1(s.disk_used_gb),
        _fmt1(s.disk_free_gb),
        _fmt_temp(s.temp_c),
        _fmt_thermal(s.thermal_state),
        _fmt_iface(s.net_iface),
    ]
    return "|".join(fields)


def _parse_uint(field: str) -> int:
    if not field.isdigit():
        raise ValueError("expected unsigned integer, got {0!r}".format(field))
    return int(field)


def _parse_pct(field: str) -> int:
    value = _parse_uint(field)
    if value > 100:
        raise ValueError("percentage out of range: {0!r}".format(field))
    return value


def _parse_x10(field: str) -> float:
    if not _X10_RE.fullmatch(field):
        raise ValueError("expected <int>[.<d>], got {0!r}".format(field))
    return float(field)


def parse_line(line: str) -> Snapshot:
    """Parse an S3 line back into a Snapshot.

    Mirrors the firmware parser's constraints: fixed field count, digits-only
    integers, at most one decimal digit, '-' only where N/A is allowed.
    Raises ValueError on malformed input. Legacy S1/S2 lines are rejected —
    only the firmware keeps compatibility with them.
    """
    parts = line.rstrip("\r\n").split("|")
    if not parts or parts[0] != PREFIX or len(parts) != FIELD_COUNT:
        raise ValueError("not a valid S3 line: {0!r}".format(line))

    if parts[11] == NA:
        iface: Optional[str] = None
    elif _IFACE_RE.fullmatch(parts[11]):
        iface = parts[11]
    else:
        raise ValueError("invalid net_iface: {0!r}".format(parts[11]))

    if parts[10] == NA:
        thermal: Optional[str] = None
    elif parts[10] in THERMAL_STATES:
        thermal = parts[10]
    else:
        raise ValueError("invalid thermal_state: {0!r}".format(parts[10]))

    if parts[9] == NA:
        temp: Optional[float] = None
    elif _TEMP_RE.fullmatch(parts[9]):
        temp = float(parts[9])
    else:
        raise ValueError("invalid temp_c: {0!r}".format(parts[9]))

    return Snapshot(
        cpu_total=_parse_pct(parts[1]),
        ram_used_mb=_parse_uint(parts[2]),
        ram_free_mb=_parse_uint(parts[3]),
        ram_pressure_pct=None if parts[4] == NA else _parse_pct(parts[4]),
        net_up_kbps=_parse_x10(parts[5]),
        net_down_kbps=_parse_x10(parts[6]),
        disk_used_gb=_parse_x10(parts[7]),
        disk_free_gb=_parse_x10(parts[8]),
        temp_c=temp,
        thermal_state=thermal,
        net_iface=iface,
    )
