"""System metrics collection for the Linux sysmon daemon.

All external inputs (clock, network counters, psutil calls, statvfs, sysfs
and procfs readers) are injectable so the collector can be tested without
touching the real system.

Platform notes — unlike the macOS daemon this one forks **no subprocesses at
all**: every Linux source needed here is a file under /proc or /sys, or a
psutil call that reads one.

- CPU: psutil.cpu_percent(interval=None), i.e. /proc/stat deltas.
- RAM: used = MemTotal − MemAvailable, free = MemAvailable, both in MiB
  (÷1024²), so used + free is the installed RAM and "free" means what an
  application can actually get (page cache included). This is what `free -h`
  calls used/available. psutil.virtual_memory().percent is the same ratio and
  is sent as ram_pressure_pct — the firmware only colors the MEM bar with it.
- Disk: os.statvfs on --disk-path (default `/`), reported in DECIMAL GB
  (÷1e9) the way `df --si` does: used = (f_blocks − f_bfree) × f_frsize,
  free = f_bavail × f_frsize. free uses f_bavail, not f_bfree, so the
  root-reserved blocks (5 % by default on ext4) are not counted as free —
  which is why used + free falls short of the partition size. The result is
  cached for ~30 s so a statvfs on a slow network mount cannot stall the
  500 ms sampling loop.
- Temperature: psutil.sensors_temperatures() (hwmon/thermal under /sys, no
  privileges needed). Chips are tried in a fixed order — coretemp (Intel),
  k10temp/zenpower (AMD), cpu_thermal/soc_thermal (ARM SoCs), acpitz last —
  and inside a chip a "package"/"Tctl"/"Tdie" label wins over the per-core
  readings. A machine that exposes no CPU sensor degrades to None ('-' on
  the wire).
- Thermal state: derived from that same temperature. When the sensor
  publishes a critical (or high) threshold the state is the temperature as a
  fraction of it; without a threshold, fixed bands are used. No temperature
  means no state.
- Network interface type: the default route's device comes from
  /proc/net/route (destination and mask both 0.0.0.0, lowest metric wins),
  falling back to /proc/net/ipv6_route for an IPv6-only host. A device with
  a wireless/ or phy80211/ entry under /sys/class/net is "WI-FI"; a tunnel
  name (tun*/tap*/wg*/ppp*/ipsec*/nordlynx*/tailscale*/zt*) means an active
  VPN and becomes "VPN"; anything else carrying the default route is "ETH".
  Any failure degrades to None ('-' on the wire). Cached for ~30 s.
"""

import os
import re
import time
from typing import Callable, Iterable, Optional, Tuple

import psutil

from sysmon_daemon_linux.protocol import Snapshot

_MB = 1024 * 1024
_DECIMAL_GB = 1e9

PROC_NET_ROUTE = "/proc/net/route"
PROC_NET_IPV6_ROUTE = "/proc/net/ipv6_route"
SYS_CLASS_NET = "/sys/class/net"

IFACE_CACHE_TTL = 30.0
DISK_CACHE_TTL = 30.0

DEFAULT_DISK_PATH = "/"

# Devices whose name alone identifies a tunnel, i.e. an active VPN when they
# carry the default route. Point-to-point links (ppp) are included for the
# same reason the macOS daemon includes them.
_TUNNEL_PREFIXES = ("tun", "tap", "wg", "ppp", "ipsec", "nordlynx", "tailscale", "zt")

_IPV6_DEFAULT_DEST = "0" * 32
_RTF_UP = 0x0001

# hwmon/thermal chips that carry a CPU temperature, most specific first.
_TEMP_CHIPS = (
    "coretemp",
    "k10temp",
    "zenpower",
    "cpu_thermal",
    "soc_thermal",
    "cpu-thermal",
    "x86_pkg_temp",
    "acpitz",
)
# Labels that mean "the package as a whole" rather than one core.
_PACKAGE_LABEL_RE = re.compile(r"package|tctl|tdie|composite", re.IGNORECASE)

# Temperature as a fraction of the sensor's critical threshold.
_RATIO_BANDS = ((0.75, "nominal"), (0.88, "fair"), (0.96, "serious"))
# Absolute fallback for sensors that publish no threshold, in °C.
_ABSOLUTE_BANDS = ((70.0, "nominal"), (85.0, "fair"), (95.0, "serious"))


def _read_file(path: str) -> Optional[str]:
    """Read a small /proc or /sys file; None on any failure."""
    try:
        with open(path, "r") as handle:
            return handle.read()
    except OSError:
        return None


def _band(value: float, bands: Iterable[Tuple[float, str]]) -> str:
    for limit, state in bands:
        if value < limit:
            return state
    return "critical"


def _state_from_temp(temp_c: Optional[float], critical: Optional[float]) -> Optional[str]:
    """Thermal state from a temperature, relative to a threshold if there is one."""
    if temp_c is None:
        return None
    if critical is not None and critical > 0.0:
        return _band(temp_c / critical, _RATIO_BANDS)
    return _band(temp_c, _ABSOLUTE_BANDS)


def _pick_reading(entries):
    """The most representative shwtemp of one chip, or None if it has none.

    A package-wide label wins; failing that, the hottest reading, which for a
    per-core chip is the closest thing to a package temperature.
    """
    usable = [e for e in entries if getattr(e, "current", None) is not None]
    if not usable:
        return None
    for entry in usable:
        if _PACKAGE_LABEL_RE.search(entry.label or ""):
            return entry
    return max(usable, key=lambda e: e.current)


def probe_thermals(sensors_source=None) -> Tuple[Optional[float], Optional[str]]:
    """Best-effort (temp_c, thermal_state); each part may be None.

    Reads psutil.sensors_temperatures() — hwmon and thermal-zone files under
    /sys, no privileges and no subprocess. A kernel or container that exposes
    no CPU sensor simply degrades to (None, None), i.e. '-' on the wire.
    """
    if sensors_source is None:
        sensors_source = getattr(psutil, "sensors_temperatures", None)
    if sensors_source is None:
        return None, None

    try:
        chips = sensors_source()
    except (OSError, AttributeError):
        return None, None
    if not chips:
        return None, None

    # Preferred chips first, then whatever else the machine exposes, so an
    # unknown SoC still gets a reading instead of a dash.
    names = [name for name in _TEMP_CHIPS if name in chips]
    names += [name for name in chips if name not in _TEMP_CHIPS]

    for name in names:
        reading = _pick_reading(chips.get(name) or ())
        if reading is None:
            continue
        temp_c = float(reading.current)
        threshold = getattr(reading, "critical", None) or getattr(reading, "high", None)
        return temp_c, _state_from_temp(temp_c, threshold)

    return None, None


def _default_route_device_v4(route_text: Optional[str]) -> Optional[str]:
    """Device of the IPv4 default route (destination and mask both zero)."""
    if not route_text:
        return None
    best_metric: Optional[int] = None
    best_device: Optional[str] = None
    for line in route_text.splitlines()[1:]:  # first line is the header
        fields = line.split()
        if len(fields) < 8:
            continue
        device, destination, _gateway, flags, _refcnt, _use, metric, mask = fields[:8]
        if destination != "00000000" or mask != "00000000":
            continue
        try:
            if not int(flags, 16) & _RTF_UP:
                continue
            metric_value = int(metric)
        except ValueError:
            continue
        if best_metric is None or metric_value < best_metric:
            best_metric, best_device = metric_value, device
    return best_device


def _default_route_device_v6(route_text: Optional[str]) -> Optional[str]:
    """Device of the IPv6 default route (::/0), for an IPv6-only host."""
    if not route_text:
        return None
    best_metric: Optional[int] = None
    best_device: Optional[str] = None
    for line in route_text.splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        if fields[0] != _IPV6_DEFAULT_DEST or fields[1] != "00":
            continue
        try:
            if not int(fields[8], 16) & _RTF_UP:
                continue
            metric_value = int(fields[5], 16)
        except ValueError:
            continue
        device = fields[9]
        if device == "lo":
            continue
        if best_metric is None or metric_value < best_metric:
            best_metric, best_device = metric_value, device
    return best_device


class NetIfaceDetector:
    """Best-effort active-network-interface type ("WI-FI"/"ETH"/"VPN"/None).

    Everything is read from procfs and sysfs, so a probe costs three small
    file reads and no process. It is still cached for cache_ttl seconds —
    both successes and failures — to keep the 500 ms loop off the filesystem.
    read_file, path_exists and time_source are injectable for tests.
    """

    def __init__(
        self,
        *,
        read_file: Callable[[str], Optional[str]] = _read_file,
        path_exists: Callable[[str], bool] = os.path.exists,
        time_source: Callable[[], float] = time.monotonic,
        cache_ttl: float = IFACE_CACHE_TTL,
    ):
        self._read_file = read_file
        self._path_exists = path_exists
        self._time_source = time_source
        self._cache_ttl = cache_ttl
        self._cached: Optional[str] = None
        self._cached_at: Optional[float] = None

    def get(self) -> Optional[str]:
        now = self._time_source()
        if self._cached_at is not None and now - self._cached_at < self._cache_ttl:
            return self._cached
        self._cached = self._detect()
        self._cached_at = now
        return self._cached

    def _detect(self) -> Optional[str]:
        device = _default_route_device_v4(self._read_file(PROC_NET_ROUTE))
        if device is None:
            device = _default_route_device_v6(self._read_file(PROC_NET_IPV6_ROUTE))
        if device is None or device == "lo":
            return None

        # A tunnel as the default route = active VPN; those devices have no
        # wireless attributes either, so classify them first.
        if device.startswith(_TUNNEL_PREFIXES):
            return "VPN"

        for attribute in ("wireless", "phy80211"):
            if self._path_exists(os.path.join(SYS_CLASS_NET, device, attribute)):
                return "WI-FI"

        # Anything else carrying the default route is wired as far as the
        # display is concerned — including bridges and virtio devices, which
        # have no /sys/class/net/<dev>/device symlink to check against.
        return "ETH"


class DiskUsageDetector:
    """(disk_used_gb, disk_free_gb) in DECIMAL GB, matching `df --si`.

    used = (f_blocks − f_bfree) × f_frsize, free = f_bavail × f_frsize — the
    Used and Avail columns of `df`. free deliberately excludes the
    root-reserved blocks, so used + free is smaller than the partition size.
    statvfs is cached for cache_ttl seconds: it is cheap on a local
    filesystem but can block for seconds on an unresponsive network mount,
    and the sampling loop must not stall. statvfs and time_source are
    injectable for tests.
    """

    def __init__(
        self,
        path: str = DEFAULT_DISK_PATH,
        *,
        statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
        time_source: Callable[[], float] = time.monotonic,
        cache_ttl: float = DISK_CACHE_TTL,
    ):
        self._path = path
        self._statvfs = statvfs
        self._time_source = time_source
        self._cache_ttl = cache_ttl
        self._cached: Optional[Tuple[float, float]] = None
        self._cached_at: Optional[float] = None

    def get(self) -> Tuple[float, float]:
        now = self._time_source()
        if (
            self._cached is not None
            and self._cached_at is not None
            and now - self._cached_at < self._cache_ttl
        ):
            return self._cached
        self._cached = self._measure()
        self._cached_at = now
        return self._cached

    def _measure(self) -> Tuple[float, float]:
        try:
            st = self._statvfs(self._path)
            # f_frsize is the fragment size statvfs block counts are in;
            # fall back to f_bsize where a filesystem reports it as 0.
            block = st.f_frsize or st.f_bsize
            used_bytes = (st.f_blocks - st.f_bfree) * block
            free_bytes = st.f_bavail * block
        except (OSError, AttributeError, ValueError):
            return 0.0, 0.0
        if used_bytes < 0 or free_bytes < 0:
            return 0.0, 0.0
        return used_bytes / _DECIMAL_GB, free_bytes / _DECIMAL_GB


class MetricsCollector:
    """Collects one Snapshot per call to collect().

    CPU: psutil.cpu_percent(interval=None) — system-wide average since the
    previous call; primed once at init so the first collect() is meaningful.
    Network: delta of net_io_counters totals vs the previous sample divided
    by elapsed time (KB/s). RAM/disk/thermals: see module docstring.
    """

    def __init__(
        self,
        *,
        disk_path: str = DEFAULT_DISK_PATH,
        time_source: Callable[[], float] = time.monotonic,
        net_counters_source=None,
        cpu_percent_source: Optional[Callable[[], float]] = None,
        virtual_memory_source=None,
        disk_source: Optional[Callable[[], Tuple[float, float]]] = None,
        thermal_probe: Callable[[], Tuple[Optional[float], Optional[str]]] = probe_thermals,
        net_iface_source: Optional[Callable[[], Optional[str]]] = None,
    ):
        self._time_source = time_source
        self._net_counters_source = (
            net_counters_source if net_counters_source is not None else psutil.net_io_counters
        )
        self._cpu_percent_source = (
            cpu_percent_source
            if cpu_percent_source is not None
            else lambda: psutil.cpu_percent(interval=None)
        )
        self._virtual_memory_source = (
            virtual_memory_source if virtual_memory_source is not None else psutil.virtual_memory
        )
        self._disk_source = (
            disk_source if disk_source is not None else DiskUsageDetector(disk_path).get
        )
        self._thermal_probe = thermal_probe
        self._net_iface_source = (
            net_iface_source if net_iface_source is not None else NetIfaceDetector().get
        )

        # Prime cpu_percent: the first interval=None call always returns a
        # meaningless value (0.0 or the average since boot depending on the
        # psutil version); subsequent calls measure since this one.
        self._cpu_percent_source()

        self._last_time = self._time_source()
        self._last_net = self._net_counters_source()

    def _net_kbps(self) -> Tuple[float, float]:
        now = self._time_source()
        counters = self._net_counters_source()
        elapsed = now - self._last_time

        up = down = 0.0
        if counters is not None and self._last_net is not None and elapsed > 0.0:
            up = (counters.bytes_sent - self._last_net.bytes_sent) / elapsed / 1024.0
            down = (counters.bytes_recv - self._last_net.bytes_recv) / elapsed / 1024.0
            # Counter reset (interface re-enumeration) shows up as a negative
            # delta; report 0 for that sample instead of garbage.
            up = max(0.0, up)
            down = max(0.0, down)

        self._last_time = now
        if counters is not None:
            self._last_net = counters
        return up, down

    def collect(self) -> Snapshot:
        cpu = int(round(self._cpu_percent_source()))

        vm = self._virtual_memory_source()
        ram_used_mb = int((vm.total - vm.available) // _MB)
        ram_free_mb = int(vm.available // _MB)
        # MemAvailable-based ratio, the same number `free` reports; the
        # firmware uses it only to color the MEM bar.
        ram_pressure_pct = int(round(vm.percent))

        net_up, net_down = self._net_kbps()

        disk_used_gb, disk_free_gb = self._disk_source()

        temp_c, thermal_state = self._thermal_probe()
        net_iface = self._net_iface_source()

        return Snapshot(
            cpu_total=cpu,
            ram_used_mb=ram_used_mb,
            ram_free_mb=ram_free_mb,
            ram_pressure_pct=ram_pressure_pct,
            net_up_kbps=net_up,
            net_down_kbps=net_down,
            disk_used_gb=disk_used_gb,
            disk_free_gb=disk_free_gb,
            temp_c=temp_c,
            thermal_state=thermal_state,
            net_iface=net_iface,
        )
