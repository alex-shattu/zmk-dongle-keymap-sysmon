"""Serial transport: device discovery by handshake and resilient sending.

Discovery walks /dev/ttyACM* (the CDC-ACM class the dongle enumerates as)
and then /dev/ttyUSB* for the odd USB-serial bridge, sends "PING\\n" to each
candidate and picks the one that answers "SYSMON1\\n" — this is how the
sysmon port is told apart from the dongle's other USB functions and from any
other serial device on the machine.

Two Linux-specific ways this fails, both covered in README.md:
- the port nodes belong to group `dialout` (Debian/Ubuntu/Fedora) or `uucp`
  (Arch), so a user outside that group gets EACCES on every candidate;
- ModemManager probes fresh /dev/ttyACM* nodes with AT commands and holds
  them open for a few seconds, which shows up here as EBUSY or as a
  handshake that times out on a port that would otherwise answer.
Either way the probe is logged at debug level and discovery simply moves on.
"""

import glob
import logging
import time
from typing import Callable, Iterable, Optional

import serial

from sysmon_daemon_linux.protocol import HELLO, PING

LOG = logging.getLogger("sysmon.serial")

BAUD = 115200
PORT_GLOBS = ("/dev/ttyACM*", "/dev/ttyUSB*")

_OPEN_TIMEOUT = 0.2
_WRITE_TIMEOUT = 1.0


def _handshake(port: serial.Serial, timeout: float) -> bool:
    port.reset_input_buffer()
    port.write((PING + "\n").encode("ascii"))
    port.flush()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline()
        if not line:
            continue
        if line.strip().decode("ascii", errors="replace") == HELLO:
            return True
        # Anything else (e.g. a stale S1 echo, ModemManager's AT chatter or
        # Studio RPC noise) — keep reading until the deadline.
    return False


def _candidates(patterns: Iterable[str]):
    """Device paths matching the globs, in order, without duplicates."""
    seen = set()
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            if path not in seen:
                seen.add(path)
                yield path


def find_port(timeout: float = 1.0, patterns: Iterable[str] = PORT_GLOBS) -> Optional[str]:
    """Return the device path of the first port answering the handshake."""
    for path in _candidates(patterns):
        LOG.debug("probing %s", path)
        try:
            with serial.Serial(
                path, BAUD, timeout=_OPEN_TIMEOUT, write_timeout=_WRITE_TIMEOUT
            ) as port:
                if _handshake(port, timeout):
                    LOG.debug("%s answered %s", path, HELLO)
                    return path
        except (serial.SerialException, OSError) as err:
            LOG.debug("probe of %s failed: %s", path, err)
    return None


class SerialLink:
    """Connection to the display device with reconnect backoff and safe writes."""

    def __init__(
        self,
        port: Optional[str] = None,
        *,
        backoff_min: float = 1.0,
        backoff_max: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        find_port_fn: Callable[..., Optional[str]] = find_port,
    ):
        self._fixed_port = port
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._find_port = find_port_fn
        self._serial: Optional[serial.Serial] = None

    @property
    def connected(self) -> bool:
        return self._serial is not None

    @property
    def port(self) -> Optional[str]:
        return self._serial.port if self._serial is not None else None

    def connect(self) -> None:
        """Block until connected, retrying with backoff (1 s up to 5 s)."""
        backoff = self._backoff_min
        while True:
            path = self._fixed_port or self._find_port()
            if path is not None:
                try:
                    self._serial = serial.Serial(
                        path, BAUD, timeout=_OPEN_TIMEOUT, write_timeout=_WRITE_TIMEOUT
                    )
                    LOG.info("connected to %s", path)
                    return
                except (serial.SerialException, OSError) as err:
                    LOG.warning("open %s failed: %s", path, err)
            else:
                LOG.debug("no sysmon port found")
            self._sleep(backoff)
            backoff = min(backoff * 2.0, self._backoff_max)

    def send_line(self, line: str) -> bool:
        """Write one line; on any serial error close and report False.

        Tolerates the port disappearing mid-write (device unplug): the link
        is marked disconnected and the caller is expected to reconnect.
        """
        if self._serial is None:
            return False
        try:
            self._serial.write((line + "\n").encode("ascii"))
            self._serial.flush()
            return True
        except (serial.SerialException, OSError) as err:
            LOG.warning("write failed (%s), disconnecting", err)
            self.close()
            return False

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
            self._serial = None
