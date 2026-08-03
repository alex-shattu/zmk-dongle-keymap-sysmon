"""System metrics collection for the sysmon daemon.

All external inputs (clock, network counters, psutil calls, disk usage,
thermal probing) are injectable so the collector can be tested without
touching the real system or spawning subprocesses.

Platform notes (probed on an Apple Silicon Mac, macOS 26):
- RAM pressure: `sysctl -n kern.memorystatus_vm_pressure_level` exists but
  only exposes coarse levels (1=normal, 2=warning, 4=critical), which cannot
  be honestly turned into a 0-100 percentage. We therefore report
  psutil.virtual_memory().percent (used-vs-available percentage) as the
  best-effort ram_pressure_pct.
- Temperature: `pmset -g therm` reports "CPU die temperature" on Intel Macs
  only; on Apple Silicon there is no unprivileged temperature source, so
  temp_c degrades to None ('-' on the wire).
- Thermal state: derived from `pmset -g therm` — a CPU_Speed_Limit line
  appears under throttling; "No thermal warning level has been recorded"
  means nominal. Falls back to `sysctl -n kern.thermalpressurelevel` where
  that OID exists. Anything unparseable degrades to None.
- Network interface type: `route -n get default` yields the default-route
  device (e.g. en0), `networksetup -listallhardwareports` maps devices to
  hardware port names — "Wi-Fi" ports become "WI-FI", every other hardware
  port (Thunderbolt bridge, USB Ethernet adapters, ...) becomes "ETH".
  A tunnel device (utun*/ipsec*/ppp*/tun*/tap*) as the default route means
  an active VPN; it never appears in the hardware port list, so it is
  reported as "VPN" directly. Any failure degrades to None ('-' on the
  wire). The result is cached for ~30 s so the 500 ms sampling loop does
  not fork subprocesses on every tick.
- RAM: used = vm.total − vm.available (≈ Activity Monitor "Memory Used"),
  free = vm.available (genuinely available, incl. inactive), both in MiB
  (÷1024²) so used + free adds up to the marketed "24 GB". vm.percent is
  still sent as ram_pressure_pct — the firmware colors the MEM bar with it.
- Disk (probed empirically on this Mac, macOS 26, APFS; reference values
  from the macOS UI at plan time: free 138.29 GB / used 346.54 GB, both
  DECIMAL GB):
  * every statvfs-flavored candidate agrees on free ≈ 131.1 (psutil
    disk_usage('/') and ('/System/Volumes/Data') .free, `df -k` Available,
    diskutil APFSContainerFree) — that is the container free space WITHOUT
    purgeable, ~7 GB short of what Finder/System Settings show;
  * the purgeable-inclusive number macOS shows is
    NSURLVolumeAvailableCapacityForImportantUsageKey, reachable without
    TCC prompts via `osascript -l JavaScript` + the ObjC bridge: measured
    138.52 vs Finder's 138.52 (reference 138.29 plus a day of drift);
  * "used" in the macOS UI is the system VOLUME GROUP usage: psutil
    disk_usage('/').used + disk_usage('/System/Volumes/Data').used =
    12.57 + 333.73 = 346.30 ≈ reference 346.54 (the drifts of used/free
    complement each other: −0.24 / +0.23). Data volume alone (333.73) and
    total − free (355.86) are both clearly off.
  So DiskUsageDetector reports used = psutil used('/') + used(Data-volume)
  and free = ImportantUsage capacity via osascript (subprocess timeout 1 s,
  result cached for 30 s); any failure falls back to psutil decimal GB
  (free = st.free, used = st.total − st.free).
"""

import re
import subprocess
import time
from typing import Callable, Optional, Tuple

import psutil

from sysmon_daemon.protocol import Snapshot

_MB = 1024 * 1024
_DECIMAL_GB = 1e9

_SUBPROCESS_TIMEOUT = 0.5

_TEMP_RE = re.compile(r"CPU die temperature:\s*(-?\d+(?:\.\d+)?)")
_SPEED_LIMIT_RE = re.compile(r"CPU_Speed_Limit\s*=\s*(\d+)")
_NO_WARNING_MARKER = "No thermal warning level has been recorded"

_ROUTE_IFACE_RE = re.compile(r"^\s*interface:\s*(\S+)\s*$", re.MULTILINE)
_HARDWARE_PORT_RE = re.compile(r"^Hardware Port:\s*(.+?)\s*\nDevice:\s*(\S+)", re.MULTILINE)
_TUNNEL_PREFIXES = ("utun", "ipsec", "ppp", "tun", "tap")

IFACE_CACHE_TTL = 30.0
DISK_CACHE_TTL = 30.0

_DISK_PROBE_TIMEOUT = 1.0
DATA_VOLUME = "/System/Volumes/Data"

# Purgeable-inclusive free space of the Data volume, in bytes — the number
# Finder/System Settings show as "available". JXA + the ObjC bridge reads
# NSURLVolumeAvailableCapacityForImportantUsageKey without automating any
# app, so no TCC/Apple Events prompt is triggered.
_FREE_SPACE_SCRIPT = (
    'ObjC.import("Foundation");'
    'const key = "NSURLVolumeAvailableCapacityForImportantUsageKey";'
    'const url = $.NSURL.fileURLWithPath("' + DATA_VOLUME + '");'
    "const values = url.resourceValuesForKeysError([key], null);"
    "ObjC.unwrap(values.objectForKey(key)).toString();"
)
FREE_SPACE_CMD = ["osascript", "-l", "JavaScript", "-e", _FREE_SPACE_SCRIPT]

ThermalProbe = Callable[[], Tuple[Optional[float], Optional[str]]]


def _run_command(cmd, timeout: float = _SUBPROCESS_TIMEOUT) -> Optional[str]:
    """Run a probe command; return stdout, or None on any failure/timeout."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _speed_limit_to_state(limit: int) -> str:
    if limit >= 100:
        return "nominal"
    if limit >= 80:
        return "fair"
    if limit >= 50:
        return "serious"
    return "critical"


def _pressure_level_to_state(level: int) -> Optional[str]:
    return {0: "nominal", 1: "fair", 2: "serious"}.get(level, "critical" if level >= 3 else None)


def probe_thermals() -> Tuple[Optional[float], Optional[str]]:
    """Best-effort (temp_c, thermal_state); each part may be None.

    Every probe is wrapped so a missing tool or unknown output format simply
    degrades to None ('-' on the wire).
    """
    temp_c: Optional[float] = None
    state: Optional[str] = None

    out = _run_command(["pmset", "-g", "therm"])
    if out is not None:
        m = _TEMP_RE.search(out)
        if m is not None:
            try:
                temp_c = float(m.group(1))
            except ValueError:
                temp_c = None

        m = _SPEED_LIMIT_RE.search(out)
        if m is not None:
            state = _speed_limit_to_state(int(m.group(1)))
        elif _NO_WARNING_MARKER in out:
            state = "nominal"

    if state is None:
        raw = _run_command(["sysctl", "-n", "kern.thermalpressurelevel"])
        if raw is not None:
            try:
                state = _pressure_level_to_state(int(raw.strip()))
            except ValueError:
                state = None

    return temp_c, state


class NetIfaceDetector:
    """Best-effort active-network-interface type ("WI-FI"/"ETH"/"VPN"/None).

    The two probe commands (`route -n get default`, `networksetup
    -listallhardwareports`) are forked at most once per cache_ttl seconds;
    both a successful detection and a failure (None) are cached, so a
    machine with no network does not fork on every 500 ms sample either.
    run_command and time_source are injectable for tests.
    """

    def __init__(
        self,
        *,
        run_command: Callable[[list], Optional[str]] = _run_command,
        time_source: Callable[[], float] = time.monotonic,
        cache_ttl: float = IFACE_CACHE_TTL,
    ):
        self._run_command = run_command
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
        out = self._run_command(["route", "-n", "get", "default"])
        if out is None:
            return None
        m = _ROUTE_IFACE_RE.search(out)
        if m is None:
            return None
        device = m.group(1)

        # A tunnel as the default route = active VPN; tunnels never appear
        # in the hardware port list, so classify them before the lookup.
        if device.startswith(_TUNNEL_PREFIXES):
            return "VPN"

        ports = self._run_command(["networksetup", "-listallhardwareports"])
        if ports is None:
            return None
        for port_name, port_device in _HARDWARE_PORT_RE.findall(ports):
            if port_device == device:
                return "WI-FI" if "wi-fi" in port_name.lower() else "ETH"
        return None


class DiskUsageDetector:
    """(disk_used_gb, disk_free_gb) in DECIMAL GB, matching the macOS UI.

    used = psutil disk_usage('/').used + disk_usage(Data volume).used (the
    system volume group, what Finder reports as "used"); free = purgeable-
    inclusive available capacity via one `osascript` subprocess (see the
    module docstring for the empirical probe that picked these sources).
    The subprocess is forked at most once per cache_ttl seconds; failures
    are cached too, and degrade to plain psutil decimal GB (free = st.free,
    used = st.total − st.free). run_command, disk_usage and time_source are
    injectable for tests.
    """

    def __init__(
        self,
        *,
        run_command: Optional[Callable[[list], Optional[str]]] = None,
        disk_usage=psutil.disk_usage,
        time_source: Callable[[], float] = time.monotonic,
        cache_ttl: float = DISK_CACHE_TTL,
    ):
        self._run_command = (
            run_command
            if run_command is not None
            else lambda cmd: _run_command(cmd, timeout=_DISK_PROBE_TIMEOUT)
        )
        self._disk_usage = disk_usage
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
            out = self._run_command(FREE_SPACE_CMD)
            free_bytes = int(out.strip())  # None/garbage → TypeError/ValueError
            if free_bytes < 0:
                raise ValueError("negative free space: {0!r}".format(free_bytes))
            used_bytes = self._disk_usage("/").used + self._disk_usage(DATA_VOLUME).used
        except (OSError, ValueError, TypeError, AttributeError):
            return self._fallback()
        return used_bytes / _DECIMAL_GB, free_bytes / _DECIMAL_GB

    def _fallback(self) -> Tuple[float, float]:
        try:
            st = self._disk_usage("/")
        except OSError:
            return 0.0, 0.0
        return (st.total - st.free) / _DECIMAL_GB, st.free / _DECIMAL_GB


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
        time_source: Callable[[], float] = time.monotonic,
        net_counters_source=None,
        cpu_percent_source: Optional[Callable[[], float]] = None,
        virtual_memory_source=None,
        disk_source: Optional[Callable[[], Tuple[float, float]]] = None,
        thermal_probe: ThermalProbe = probe_thermals,
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
        self._disk_source = disk_source if disk_source is not None else DiskUsageDetector().get
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
        # Best-effort pressure percentage; see module docstring for why the
        # kern.memorystatus_vm_pressure_level sysctl is not used here.
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
