"""Tests for MetricsCollector using injected fakes.

No serial and no subprocess access: every external input goes through the
collector's injection points (clock, net counters, cpu, vm, disk, thermal
probe).
"""

import math
from types import SimpleNamespace

import pytest

from sysmon_daemon.metrics import (
    DATA_VOLUME,
    FREE_SPACE_CMD,
    DiskUsageDetector,
    MetricsCollector,
    NetIfaceDetector,
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

    assert s.ram_used_mb == 8 * 1024  # total - available
    assert s.ram_free_mb == 8 * 1024  # available
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

ROUTE_WIFI = """\
   route to: default
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""

ROUTE_THUNDERBOLT = ROUTE_WIFI.replace("interface: en0", "interface: en5")

ROUTE_VPN = """\
   route to: default
destination: default
       mask: default
  interface: utun4
      flags: <UP,DONE,CLONING,STATIC,GLOBAL>
"""

HARDWARE_PORTS = """\
Hardware Port: Ethernet Adapter (en5)
Device: en5
Ethernet Address: 56:ae:34:7a:3d:7b

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 36:61:af:f5:aa:40

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 84:2f:57:87:cb:f5

Hardware Port: Thunderbolt 1
Device: en1
Ethernet Address: 36:61:af:f5:aa:40

VLAN Configurations
===================
"""


class FakeRunCommand:
    """Injectable _run_command double keyed by argv[0]; counts invocations."""

    def __init__(self, route=None, ports=None):
        self.route = route
        self.ports = ports
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if cmd[0] == "route":
            return self.route
        if cmd[0] == "networksetup":
            return self.ports
        raise AssertionError("unexpected command: {0!r}".format(cmd))


def make_detector(clock, run_command, **kwargs):
    return NetIfaceDetector(run_command=run_command, time_source=clock, **kwargs)


def test_iface_wifi_port_maps_to_wifi():
    fake = FakeRunCommand(route=ROUTE_WIFI, ports=HARDWARE_PORTS)
    assert make_detector(FakeClock(), fake).get() == "WI-FI"


def test_iface_non_wifi_port_maps_to_eth():
    fake = FakeRunCommand(route=ROUTE_THUNDERBOLT, ports=HARDWARE_PORTS)
    assert make_detector(FakeClock(), fake).get() == "ETH"


def test_iface_tunnel_default_route_is_vpn_without_port_lookup():
    fake = FakeRunCommand(route=ROUTE_VPN, ports=HARDWARE_PORTS)
    assert make_detector(FakeClock(), fake).get() == "VPN"
    # A tunnel is classified straight away: networksetup is never forked.
    assert [c[0] for c in fake.calls] == ["route"]


def test_iface_route_failure_is_none():
    fake = FakeRunCommand(route=None, ports=HARDWARE_PORTS)
    assert make_detector(FakeClock(), fake).get() is None


def test_iface_route_without_interface_line_is_none():
    fake = FakeRunCommand(route="route to: default\n", ports=HARDWARE_PORTS)
    assert make_detector(FakeClock(), fake).get() is None


def test_iface_networksetup_failure_is_none():
    fake = FakeRunCommand(route=ROUTE_WIFI, ports=None)
    assert make_detector(FakeClock(), fake).get() is None


def test_iface_device_missing_from_port_list_is_none():
    fake = FakeRunCommand(
        route=ROUTE_WIFI.replace("interface: en0", "interface: en9"),
        ports=HARDWARE_PORTS,
    )
    assert make_detector(FakeClock(), fake).get() is None


def test_iface_result_cached_within_ttl():
    clock = FakeClock()
    fake = FakeRunCommand(route=ROUTE_WIFI, ports=HARDWARE_PORTS)
    detector = make_detector(clock, fake)

    assert detector.get() == "WI-FI"
    probes_after_first = len(fake.calls)

    # 500 ms sampling for the next ~29.5 s: no new subprocesses.
    for _ in range(59):
        clock.advance(0.5)
        assert detector.get() == "WI-FI"
    assert len(fake.calls) == probes_after_first

    # Past the 30 s TTL the detector probes again (and picks up changes).
    clock.advance(1.0)
    fake.route = ROUTE_VPN
    assert detector.get() == "VPN"
    assert len(fake.calls) > probes_after_first


# --- DiskUsageDetector ------------------------------------------------------

# Real values from the empirical probe on this Mac (see metrics.py docstring).
FREE_IMPORTANT_USAGE_BYTES = 138_520_750_528  # osascript output: 138.52 GB
SYSTEM_USED_BYTES = 12_569_067_520  # psutil.disk_usage('/').used: 12.57 GB
DATA_USED_BYTES = 333_732_052_992  # psutil.disk_usage(Data).used: 333.73 GB
TOTAL_BYTES = 494_384_795_648
STATVFS_FREE_BYTES = 131_102_052_352  # container free WITHOUT purgeable


class FakeOsascript:
    """Injectable run_command double for the free-space probe."""

    def __init__(self, output="{0}\n".format(FREE_IMPORTANT_USAGE_BYTES)):
        self.output = output
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        assert cmd[0] == "osascript"
        return self.output


class FakeDiskUsage:
    """Injectable psutil.disk_usage double keyed by mount path."""

    def __init__(self, mapping=None):
        self.mapping = mapping if mapping is not None else {
            "/": SimpleNamespace(
                total=TOTAL_BYTES,
                used=SYSTEM_USED_BYTES,
                free=STATVFS_FREE_BYTES,
            ),
            DATA_VOLUME: SimpleNamespace(
                total=TOTAL_BYTES,
                used=DATA_USED_BYTES,
                free=STATVFS_FREE_BYTES,
            ),
        }
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        result = self.mapping.get(path)
        if result is None:
            raise OSError("no such mount: {0}".format(path))
        return result


def make_disk_detector(clock=None, run_command=None, disk_usage=None, **kwargs):
    return DiskUsageDetector(
        run_command=run_command if run_command is not None else FakeOsascript(),
        disk_usage=disk_usage if disk_usage is not None else FakeDiskUsage(),
        time_source=clock if clock is not None else FakeClock(),
        **kwargs,
    )


def test_disk_used_is_volume_group_free_is_purgeable_inclusive():
    used, free = make_disk_detector().get()
    # used = System + Data volumes (what Finder shows as "used").
    assert math.isclose(used, (SYSTEM_USED_BYTES + DATA_USED_BYTES) / 1e9)
    assert math.isclose(used, 346.30, abs_tol=0.005)
    # free = ImportantUsage capacity (incl. purgeable), NOT statvfs free.
    assert math.isclose(free, FREE_IMPORTANT_USAGE_BYTES / 1e9)
    assert math.isclose(free, 138.52, abs_tol=0.005)


def test_disk_probe_runs_the_jxa_free_space_command():
    fake = FakeOsascript()
    make_disk_detector(run_command=fake).get()
    assert fake.calls == [FREE_SPACE_CMD]


def test_disk_osascript_output_whitespace_is_tolerated():
    fake = FakeOsascript(output="  {0}\n\n".format(FREE_IMPORTANT_USAGE_BYTES))
    _, free = make_disk_detector(run_command=fake).get()
    assert math.isclose(free, FREE_IMPORTANT_USAGE_BYTES / 1e9)


@pytest.mark.parametrize("bad_output", [None, "", "\n", "garbage", "-5\n", "1.5e9\n"])
def test_disk_bad_osascript_output_falls_back_to_psutil(bad_output):
    fake = FakeOsascript(output=bad_output)
    used, free = make_disk_detector(run_command=fake).get()
    # Fallback: plain psutil decimal GB, used = total - free.
    assert math.isclose(free, STATVFS_FREE_BYTES / 1e9)
    assert math.isclose(used, (TOTAL_BYTES - STATVFS_FREE_BYTES) / 1e9)


def test_disk_missing_data_volume_falls_back_to_root():
    disk_usage = FakeDiskUsage(
        mapping={
            "/": SimpleNamespace(
                total=TOTAL_BYTES,
                used=SYSTEM_USED_BYTES,
                free=STATVFS_FREE_BYTES,
            )
        }
    )
    used, free = make_disk_detector(disk_usage=disk_usage).get()
    assert math.isclose(free, STATVFS_FREE_BYTES / 1e9)
    assert math.isclose(used, (TOTAL_BYTES - STATVFS_FREE_BYTES) / 1e9)


def test_disk_everything_failing_degrades_to_zero():
    disk_usage = FakeDiskUsage(mapping={})
    used, free = make_disk_detector(
        run_command=FakeOsascript(output=None), disk_usage=disk_usage
    ).get()
    assert used == 0.0
    assert free == 0.0


def test_disk_result_cached_within_ttl():
    clock = FakeClock()
    fake = FakeOsascript()
    disk_usage = FakeDiskUsage()
    detector = make_disk_detector(clock, fake, disk_usage)

    first = detector.get()
    assert len(fake.calls) == 1

    # 500 ms sampling for the next ~29.5 s: no new subprocess, no re-stat.
    stats_after_first = len(disk_usage.calls)
    for _ in range(59):
        clock.advance(0.5)
        assert detector.get() == first
    assert len(fake.calls) == 1
    assert len(disk_usage.calls) == stats_after_first

    # Past the 30 s TTL the detector measures again (and picks up changes).
    clock.advance(1.0)
    fake.output = "{0}\n".format(FREE_IMPORTANT_USAGE_BYTES + 10_000_000_000)
    _, free = detector.get()
    assert len(fake.calls) == 2
    assert math.isclose(free, (FREE_IMPORTANT_USAGE_BYTES + 10_000_000_000) / 1e9)


def test_disk_failure_is_cached_too():
    clock = FakeClock()
    fake = FakeOsascript(output=None)
    detector = make_disk_detector(clock, fake)

    fallback = detector.get()
    assert len(fake.calls) == 1
    assert math.isclose(fallback[1], STATVFS_FREE_BYTES / 1e9)

    clock.advance(0.5)
    assert detector.get() == fallback
    assert len(fake.calls) == 1  # failure did not trigger an immediate retry

    clock.advance(30.0)
    fake.output = "{0}\n".format(FREE_IMPORTANT_USAGE_BYTES)
    _, free = detector.get()
    assert math.isclose(free, FREE_IMPORTANT_USAGE_BYTES / 1e9)


def test_iface_failure_is_cached_too():
    clock = FakeClock()
    fake = FakeRunCommand(route=None, ports=None)
    detector = make_detector(clock, fake)

    assert detector.get() is None
    assert len(fake.calls) == 1

    clock.advance(0.5)
    assert detector.get() is None
    assert len(fake.calls) == 1  # failure did not trigger an immediate retry

    clock.advance(30.0)
    fake.route = ROUTE_WIFI
    fake.ports = HARDWARE_PORTS
    assert detector.get() == "WI-FI"
