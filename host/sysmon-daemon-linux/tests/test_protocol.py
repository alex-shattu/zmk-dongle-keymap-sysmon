"""Tests for the pure S3 protocol formatter/parser."""

import math

import pytest

from sysmon_daemon_linux.protocol import HELLO, PING, Snapshot, format_line, parse_line


def snapshot(**overrides):
    base = dict(
        cpu_total=23,
        ram_used_mb=8432,
        ram_free_mb=7952,
        ram_pressure_pct=45,
        net_up_kbps=123.4,
        net_down_kbps=2048.7,
        disk_used_gb=346.3,
        disk_free_gb=138.5,
        temp_c=58.3,
        thermal_state="nominal",
        net_iface="WI-FI",
    )
    base.update(overrides)
    return Snapshot(**base)


def test_constants():
    assert PING == "PING"
    assert HELLO == "SYSMON1"


def test_exact_example_line():
    line = format_line(snapshot())
    assert line == "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|WI-FI"


def test_field_order():
    parts = format_line(snapshot()).split("|")
    assert parts == [
        "S3",
        "23",
        "8432",
        "7952",
        "45",
        "123.4",
        "2048.7",
        "346.3",
        "138.5",
        "58.3",
        "nominal",
        "WI-FI",
    ]


def test_net_iface_defaults_to_none():
    s = Snapshot(
        cpu_total=1,
        ram_used_mb=1,
        ram_free_mb=2,
        ram_pressure_pct=1,
        net_up_kbps=0.0,
        net_down_kbps=0.0,
        disk_used_gb=1.0,
        disk_free_gb=2.0,
        temp_c=None,
        thermal_state=None,
    )
    assert s.net_iface is None
    assert format_line(s).split("|")[11] == "-"


def test_floats_rendered_with_exactly_one_decimal():
    line = format_line(
        snapshot(
            net_up_kbps=0.06,
            net_down_kbps=123.44,
            disk_used_gb=99.96,
            disk_free_gb=100.0,
            temp_c=58.35,
        )
    )
    parts = line.split("|")
    assert parts[5] == "0.1"
    assert parts[6] == "123.4"
    assert parts[7] == "100.0"
    assert parts[8] == "100.0"
    # Every float field carries exactly one decimal digit.
    for field in parts[5:10]:
        whole, _, frac = field.partition(".")
        assert frac and len(frac) == 1, field


def test_none_becomes_dash():
    line = format_line(
        snapshot(ram_pressure_pct=None, temp_c=None, thermal_state=None, net_iface=None)
    )
    parts = line.split("|")
    assert parts[4] == "-"
    assert parts[9] == "-"
    assert parts[10] == "-"
    assert parts[11] == "-"


def test_cpu_clamped_to_0_100():
    assert format_line(snapshot(cpu_total=150)).split("|")[1] == "100"
    assert format_line(snapshot(cpu_total=-5)).split("|")[1] == "0"
    assert format_line(snapshot(cpu_total=0)).split("|")[1] == "0"
    assert format_line(snapshot(cpu_total=100)).split("|")[1] == "100"


def test_pressure_clamped_to_0_100():
    assert format_line(snapshot(ram_pressure_pct=101)).split("|")[4] == "100"
    assert format_line(snapshot(ram_pressure_pct=-1)).split("|")[4] == "0"


def test_negative_and_nonfinite_floats_clamped_to_zero():
    line = format_line(
        snapshot(net_up_kbps=-3.2, net_down_kbps=float("nan"), disk_used_gb=float("-inf"))
    )
    parts = line.split("|")
    assert parts[5] == "0.0"
    assert parts[6] == "0.0"
    assert parts[7] == "0.0"


def test_temp_near_zero_never_renders_minus_zero():
    assert format_line(snapshot(temp_c=-0.04)).split("|")[9] == "0.0"


def test_nan_temp_becomes_dash():
    assert format_line(snapshot(temp_c=float("nan"))).split("|")[9] == "-"


@pytest.mark.parametrize("state", ["nominal", "fair", "serious", "critical"])
def test_valid_thermal_states_pass_through(state):
    assert format_line(snapshot(thermal_state=state)).split("|")[10] == state


@pytest.mark.parametrize("state", ["bogus", "", "NOMINAL", "nominal ", "warm"])
def test_invalid_thermal_state_becomes_dash(state):
    assert format_line(snapshot(thermal_state=state)).split("|")[10] == "-"


@pytest.mark.parametrize("iface", ["WI-FI", "ETH", "VPN", "USB4", "A", "1234567", "-"])
def test_valid_iface_tokens_pass_through(iface):
    assert format_line(snapshot(net_iface=iface)).split("|")[11] == iface


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("wi-fi", "WI-FI"),  # uppercased
        ("  eth ", "ETH"),  # stripped then uppercased
        ("\tvpn\n", "VPN"),
    ],
)
def test_iface_normalized(raw, expected):
    assert format_line(snapshot(net_iface=raw)).split("|")[11] == expected


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "ETHERNET",  # 8 chars: too long
        "WI FI",  # inner space
        "WI_FI",  # underscore not allowed
        "wi.fi",  # dot not allowed
        "EN0|X",  # separator injection
        "ЛВС",  # non-ASCII
    ],
)
def test_invalid_iface_becomes_dash(bad):
    assert format_line(snapshot(net_iface=bad)).split("|")[11] == "-"


def test_iface_never_breaks_field_count():
    for iface in [None, "WI-FI", "EN0|X", "a b c", "x" * 40]:
        assert len(format_line(snapshot(net_iface=iface)).split("|")) == 12


@pytest.mark.parametrize(
    "s",
    [
        snapshot(),
        snapshot(ram_pressure_pct=None, temp_c=None, thermal_state=None, net_iface=None),
        snapshot(cpu_total=0, net_up_kbps=0.0, net_down_kbps=0.0, temp_c=-12.5),
        snapshot(thermal_state="critical", ram_pressure_pct=100, net_iface="ETH"),
        snapshot(net_iface="VPN"),
        snapshot(disk_used_gb=0.0, disk_free_gb=0.0),
    ],
)
def test_roundtrip_parse_of_format(s):
    parsed = parse_line(format_line(s))
    assert parsed.cpu_total == max(0, min(100, s.cpu_total))
    assert parsed.ram_used_mb == s.ram_used_mb
    assert parsed.ram_free_mb == s.ram_free_mb
    assert parsed.ram_pressure_pct == s.ram_pressure_pct
    assert math.isclose(parsed.net_up_kbps, s.net_up_kbps, abs_tol=0.05)
    assert math.isclose(parsed.net_down_kbps, s.net_down_kbps, abs_tol=0.05)
    assert math.isclose(parsed.disk_used_gb, s.disk_used_gb, abs_tol=0.05)
    assert math.isclose(parsed.disk_free_gb, s.disk_free_gb, abs_tol=0.05)
    if s.temp_c is None:
        assert parsed.temp_c is None
    else:
        assert math.isclose(parsed.temp_c, s.temp_c, abs_tol=0.05)
    assert parsed.thermal_state == s.thermal_state
    assert parsed.net_iface == s.net_iface


def test_parse_accepts_trailing_newline():
    line = format_line(snapshot())
    assert parse_line(line + "\n") == parse_line(line)


def test_parse_s3_dash_iface_is_none():
    parsed = parse_line("S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|-")
    assert parsed.net_iface is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "S3",
        # Legacy prefixes are rejected: only the firmware keeps S1/S2 compat.
        "S1|23|8432|16384|45|123.4|2048.7|312.5|494.4|58.3|nominal",
        "S2|23|8432|16384|45|123.4|2048.7|312.5|494.4|58.3|nominal|WI-FI",
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal",  # missing iface
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|ETH|extra",
        "S3|abc|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|ETH",
        "S3|123|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|ETH",  # cpu > 100
        "S3|23|8432|7952|45|123.45|2048.7|346.3|138.5|58.3|nominal|ETH",  # 2 decimals
        "S3|23|8432|7952|45|-1.0|2048.7|346.3|138.5|58.3|nominal|ETH",  # negative net
        "S3|23|8432|7952|45|123.4|2048.7|-346.3|138.5|58.3|nominal|ETH",  # negative disk
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|toasty|ETH",
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|wi-fi",  # lowercase
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|ETHERNET",  # >7
        "S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|",  # empty iface
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_line(bad)
