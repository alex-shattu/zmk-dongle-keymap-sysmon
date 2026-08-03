"""Tests for the Linux MetricsCollector and its detectors, using fakes.

No serial, no /proc, no /sys: every external input goes through an injection
point (clock, net counters, cpu, vm, statvfs, file reader, sensors), so the
suite gives the same answers on a developer's Mac as on the target machine.
"""

import math
import os
from types import SimpleNamespace

import pytest

from sysmon_daemon_linux.metrics import (
    PROC_NET_IPV6_ROUTE,
    PROC_NET_ROUTE,
    SYS_CLASS_NET,
    DiskUsageDetector,
    MetricsCollector,
    NetIfaceDetector,
    probe_thermals,
)

MB = 1024 * 1024
GB = 1024 * 1024 * 1024


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeNet:
    """net_io_counters stand-in returning the current totals."""

    def __init__(self, sent=0, recv=0):
        self.sent = sent
        self.recv = recv

    def __call__(self):
        return SimpleNamespace(bytes_sent=self.sent, bytes_recv=self.recv)


def make_collector(clock, net, *, cpu=10.0, cpu_calls=None):
    def cpu_source():
        if cpu_calls is not None:
            cpu_calls.append(True)
        return cpu

    return MetricsCollector(
        time_source=clock,
        net_counters_source=net,
        cpu_percent_source=cpu_source,
        virtual_memory_source=lambda: SimpleNamespace(
            total=16 * GB, available=8 * GB, percent=50.0
        ),
        disk_source=lambda: (346.3, 138.5),
        thermal_probe=lambda: (None, None),
        net_iface_source=lambda: None,
    )


def test_net_delta_two_samples():
    clock = FakeClock()
    net = FakeNet(sent=1_000_000, recv=2_000_000)
    collector = make_collector(clock, net)

    # 2 seconds later: +204800 B sent (100 KB/s), +1048576 B recv (512 KB/s).
    clock.advance(2.0)
    net.sent += 204800
    net.recv += 1048576
    s = collector.collect()
    assert math.isclose(s.net_up_kbps, 100.0)
    assert math.isclose(s.net_down_kbps, 512.0)

    # Next sample: 0.5 s, +51200 B sent (100 KB/s), no rx traffic.
    clock.advance(0.5)
    net.sent += 51200
    s = collector.collect()
    assert math.isclose(s.net_up_kbps, 100.0)
    assert math.isclose(s.net_down_kbps, 0.0)


def test_net_counter_reset_reports_zero():
    clock = FakeClock()
    net = FakeNet(sent=5_000_000, recv=5_000_000)
    collector = make_collector(clock, net)

    clock.advance(1.0)
    net.sent = 100  # counters went backwards (interface re-enumerated)
    net.recv = 200
    s = collector.collect()
    assert s.net_up_kbps == 0.0
    assert s.net_down_kbps == 0.0

    # The reset baseline is adopted: the next delta is measured from it.
    clock.advance(1.0)
    net.sent += 1024
    s = collector.collect()
    assert math.isclose(s.net_up_kbps, 1.0)


def test_zero_elapsed_reports_zero():
    clock = FakeClock()
    net = FakeNet(sent=0, recv=0)
    collector = make_collector(clock, net)

    net.sent += 999999  # traffic but no time passed
    s = collector.collect()
    assert s.net_up_kbps == 0.0
    assert s.net_down_kbps == 0.0


def test_cpu_is_primed_once_at_init():
    clock = FakeClock()
    calls = []
    collector = make_collector(clock, FakeNet(), cpu=37.4, cpu_calls=calls)
    assert len(calls) == 1  # priming call in __init__

    clock.advance(0.5)
    s = collector.collect()
    assert len(calls) == 2
    assert s.cpu_total == 37  # rounded to int


def test_ram_and_disk_conversions():
    clock = FakeClock()
    collector = make_collector(clock, FakeNet())
    clock.advance(0.5)
    s = collector.collect()

    assert s.ram_used_mb == 8 * 1024  # MemTotal - MemAvailable
    assert s.ram_free_mb == 8 * 1024  # MemAvailable
    assert s.ram_pressure_pct == 50
    assert math.isclose(s.disk_used_gb, 346.3)
    assert math.isclose(s.disk_free_gb, 138.5)


def test_thermal_probe_injection():
    clock = FakeClock()
    net = FakeNet()

    collector = MetricsCollector(
        time_source=clock,
        net_counters_source=net,
        cpu_percent_source=lambda: 0.0,
        virtual_memory_source=lambda: SimpleNamespace(
            total=16 * GB, available=8 * GB, percent=50.0
        ),
        disk_source=lambda: (1.0, 1.0),
        thermal_probe=lambda: (61.5, "fair"),
        net_iface_source=lambda: "WI-FI",
    )
    clock.advance(0.5)
    s = collector.collect()
    assert s.temp_c == 61.5
    assert s.thermal_state == "fair"
    assert s.net_iface == "WI-FI"


# --- NetIfaceDetector -------------------------------------------------------

ROUTE_HEADER = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
)

ROUTE_WIFI = ROUTE_HEADER + (
    "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n"
)

ROUTE_ETH = ROUTE_HEADER + "enp3s0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"

ROUTE_VPN = ROUTE_HEADER + "tun0\t00000000\t00000000\t0001\t0\t0\t50\t00000000\t0\t0\t0\n"

# Two default routes: the wired one has the lower metric, so it wins.
ROUTE_BOTH = ROUTE_HEADER + (
    "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "enp3s0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)

# A default route that is administratively down (RTF_UP clear).
ROUTE_DOWN = ROUTE_HEADER + "enp3s0\t00000000\t0101A8C0\t0002\t0\t0\t100\t00000000\t0\t0\t0\n"

ROUTE_NO_DEFAULT = ROUTE_HEADER + (
    "enp3s0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)

IPV6_ROUTE = (
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00 "
    "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 enp6s0\n"
)


class FakeFs:
    """Injectable file reader + path_exists over an in-memory dict."""

    def __init__(self, files=None, dirs=()):
        self.files = files if files is not None else {}
        self.dirs = set(dirs)
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)

    def exists(self, path):
        return path in self.dirs


def make_detector(clock=None, fs=None, **kwargs):
    fs = fs if fs is not None else FakeFs()
    return NetIfaceDetector(
        read_file=fs.read,
        path_exists=fs.exists,
        time_source=clock if clock is not None else FakeClock(),
        **kwargs,
    )


def wireless(device, attribute="wireless"):
    return os.path.join(SYS_CLASS_NET, device, attribute)


def test_iface_wireless_attribute_maps_to_wifi():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_WIFI}, dirs=[wireless("wlan0")])
    assert make_detector(fs=fs).get() == "WI-FI"


def test_iface_phy80211_also_maps_to_wifi():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_WIFI}, dirs=[wireless("wlan0", "phy80211")])
    assert make_detector(fs=fs).get() == "WI-FI"


def test_iface_without_wireless_attributes_is_eth():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_ETH})
    assert make_detector(fs=fs).get() == "ETH"


def test_iface_tunnel_default_route_is_vpn_without_sysfs_lookup():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_VPN}, dirs=[wireless("tun0")])
    assert make_detector(fs=fs).get() == "VPN"


@pytest.mark.parametrize("device", ["tun0", "tap0", "wg0", "ppp0", "nordlynx", "tailscale0"])
def test_iface_tunnel_prefixes(device):
    route = ROUTE_HEADER + "{0}\t00000000\t00000000\t0001\t0\t0\t50\t00000000\t0\t0\t0\n".format(
        device
    )
    fs = FakeFs({PROC_NET_ROUTE: route})
    assert make_detector(fs=fs).get() == "VPN"


def test_iface_lowest_metric_default_route_wins():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_BOTH}, dirs=[wireless("wlan0")])
    assert make_detector(fs=fs).get() == "ETH"


def test_iface_down_default_route_is_ignored():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_DOWN})
    assert make_detector(fs=fs).get() is None


def test_iface_no_default_route_is_none():
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_NO_DEFAULT})
    assert make_detector(fs=fs).get() is None


def test_iface_unreadable_procfs_is_none():
    assert make_detector(fs=FakeFs()).get() is None


def test_iface_falls_back_to_ipv6_default_route():
    fs = FakeFs(
        {PROC_NET_ROUTE: ROUTE_NO_DEFAULT, PROC_NET_IPV6_ROUTE: IPV6_ROUTE},
        dirs=[wireless("enp6s0")],
    )
    assert make_detector(fs=fs).get() == "WI-FI"


def test_iface_loopback_default_route_is_none():
    route = ROUTE_HEADER + "lo\t00000000\t00000000\t0001\t0\t0\t0\t00000000\t0\t0\t0\n"
    assert make_detector(fs=FakeFs({PROC_NET_ROUTE: route})).get() is None


def test_iface_result_cached_within_ttl():
    clock = FakeClock()
    fs = FakeFs({PROC_NET_ROUTE: ROUTE_WIFI}, dirs=[wireless("wlan0")])
    detector = make_detector(clock, fs)

    assert detector.get() == "WI-FI"
    reads_after_first = len(fs.reads)

    # 500 ms sampling for the next ~29.5 s: procfs is not touched again.
    for _ in range(59):
        clock.advance(0.5)
        assert detector.get() == "WI-FI"
    assert len(fs.reads) == reads_after_first

    # Past the 30 s TTL the detector probes again (and picks up changes).
    clock.advance(1.0)
    fs.files[PROC_NET_ROUTE] = ROUTE_VPN
    assert detector.get() == "VPN"
    assert len(fs.reads) > reads_after_first


def test_iface_failure_is_cached_too():
    clock = FakeClock()
    fs = FakeFs()
    detector = make_detector(clock, fs)

    assert detector.get() is None
    reads_after_first = len(fs.reads)

    clock.advance(0.5)
    assert detector.get() is None
    assert len(fs.reads) == reads_after_first  # no immediate retry

    clock.advance(30.0)
    fs.files[PROC_NET_ROUTE] = ROUTE_ETH
    assert detector.get() == "ETH"


# --- DiskUsageDetector ------------------------------------------------------

BLOCK = 4096
BLOCKS = 122_096_640  # 500.1 GB partition at 4 KiB blocks
BFREE = 40_000_000  # incl. the root-reserved blocks
BAVAIL = 33_896_640  # what an unprivileged process can actually use


def fake_statvfs(frsize=BLOCK, bsize=BLOCK, blocks=BLOCKS, bfree=BFREE, bavail=BAVAIL):
    return SimpleNamespace(
        f_frsize=frsize,
        f_bsize=bsize,
        f_blocks=blocks,
        f_bfree=bfree,
        f_bavail=bavail,
    )


class FakeStatvfs:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else fake_statvfs()
        self.error = error
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return self.result


def make_disk_detector(clock=None, statvfs=None, path="/", **kwargs):
    return DiskUsageDetector(
        path,
        statvfs=statvfs if statvfs is not None else FakeStatvfs(),
        time_source=clock if clock is not None else FakeClock(),
        **kwargs,
    )


def test_disk_matches_df_used_and_avail():
    used, free = make_disk_detector().get()
    # Used is total-minus-bfree, i.e. `df` Used; free is bavail, `df` Avail.
    assert math.isclose(used, (BLOCKS - BFREE) * BLOCK / 1e9)
    assert math.isclose(free, BAVAIL * BLOCK / 1e9)


def test_disk_free_excludes_root_reserved_blocks():
    _, free = make_disk_detector().get()
    # bfree - bavail is reserved for root and must not be reported as free.
    assert free < BFREE * BLOCK / 1e9
    assert math.isclose(free, (BFREE - (BFREE - BAVAIL)) * BLOCK / 1e9)


def test_disk_zero_frsize_falls_back_to_bsize():
    statvfs = FakeStatvfs(fake_statvfs(frsize=0, bsize=BLOCK))
    used, free = make_disk_detector(statvfs=statvfs).get()
    assert math.isclose(free, BAVAIL * BLOCK / 1e9)
    assert math.isclose(used, (BLOCKS - BFREE) * BLOCK / 1e9)


def test_disk_uses_the_configured_mount_point():
    statvfs = FakeStatvfs()
    make_disk_detector(statvfs=statvfs, path="/home").get()
    assert statvfs.calls == ["/home"]


def test_disk_statvfs_failure_degrades_to_zero():
    statvfs = FakeStatvfs(error=OSError("no such mount"))
    assert make_disk_detector(statvfs=statvfs).get() == (0.0, 0.0)


def test_disk_result_cached_within_ttl():
    clock = FakeClock()
    statvfs = FakeStatvfs()
    detector = make_disk_detector(clock, statvfs)

    first = detector.get()
    assert len(statvfs.calls) == 1

    # 500 ms sampling for the next ~29.5 s: no re-stat.
    for _ in range(59):
        clock.advance(0.5)
        assert detector.get() == first
    assert len(statvfs.calls) == 1

    # Past the 30 s TTL it measures again (and picks up changes).
    clock.advance(1.0)
    statvfs.result = fake_statvfs(bavail=BAVAIL // 2)
    _, free = detector.get()
    assert len(statvfs.calls) == 2
    assert math.isclose(free, (BAVAIL // 2) * BLOCK / 1e9)


def test_disk_failure_is_cached_too():
    clock = FakeClock()
    statvfs = FakeStatvfs(error=OSError("gone"))
    detector = make_disk_detector(clock, statvfs)

    assert detector.get() == (0.0, 0.0)
    assert len(statvfs.calls) == 1

    clock.advance(0.5)
    assert detector.get() == (0.0, 0.0)
    assert len(statvfs.calls) == 1  # failure did not trigger an immediate retry

    clock.advance(30.0)
    statvfs.error = None
    _, free = detector.get()
    assert math.isclose(free, BAVAIL * BLOCK / 1e9)


# --- probe_thermals ---------------------------------------------------------


def reading(label, current, high=None, critical=None):
    return SimpleNamespace(label=label, current=current, high=high, critical=critical)


def test_thermals_no_sensor_source_is_none():
    assert probe_thermals(lambda: {}) == (None, None)


def test_thermals_sensors_raising_is_none():
    def boom():
        raise OSError("hwmon disappeared")

    assert probe_thermals(boom) == (None, None)


def test_thermals_prefers_coretemp_over_acpitz():
    chips = {
        "acpitz": [reading("", 40.0)],
        "coretemp": [reading("Package id 0", 55.0, high=84.0, critical=100.0)],
    }
    temp, state = probe_thermals(lambda: chips)
    assert temp == 55.0
    assert state == "nominal"  # 55/100 = 0.55


def test_thermals_package_label_beats_hotter_core():
    chips = {
        "coretemp": [
            reading("Core 0", 71.0, critical=100.0),
            reading("Package id 0", 66.0, critical=100.0),
        ]
    }
    temp, _ = probe_thermals(lambda: chips)
    assert temp == 66.0


def test_thermals_without_package_label_takes_the_hottest_core():
    chips = {"coretemp": [reading("Core 0", 51.0), reading("Core 1", 63.0)]}
    temp, _ = probe_thermals(lambda: chips)
    assert temp == 63.0


@pytest.mark.parametrize(
    "current,expected",
    [(60.0, "nominal"), (80.0, "fair"), (90.0, "serious"), (99.0, "critical")],
)
def test_thermals_state_from_critical_threshold(current, expected):
    chips = {"k10temp": [reading("Tctl", current, critical=100.0)]}
    assert probe_thermals(lambda: chips)[1] == expected


@pytest.mark.parametrize(
    "current,expected",
    [(45.0, "nominal"), (78.0, "fair"), (90.0, "serious"), (97.0, "critical")],
)
def test_thermals_state_from_absolute_bands_without_threshold(current, expected):
    chips = {"cpu_thermal": [reading("", current)]}
    assert probe_thermals(lambda: chips)[1] == expected


def test_thermals_high_is_used_when_critical_is_absent():
    chips = {"coretemp": [reading("Package id 0", 83.0, high=84.0)]}
    # 83/84 = 0.988 of the only threshold there is.
    assert probe_thermals(lambda: chips)[1] == "critical"


def test_thermals_unknown_chip_is_still_read():
    chips = {"some_soc_sensor": [reading("", 48.0)]}
    temp, state = probe_thermals(lambda: chips)
    assert temp == 48.0
    assert state == "nominal"


def test_thermals_entries_without_current_are_skipped():
    chips = {
        "coretemp": [reading("Package id 0", None)],
        "acpitz": [reading("", 42.0)],
    }
    temp, _ = probe_thermals(lambda: chips)
    assert temp == 42.0
