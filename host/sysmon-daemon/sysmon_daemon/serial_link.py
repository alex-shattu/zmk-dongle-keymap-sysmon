"""Serial transport: device discovery by handshake and resilient sending.

Discovery walks /dev/cu.usbmodem* (cu.*, not tty.*, so open() does not block
on DCD), sends "PING\\n" to each candidate and picks the one that answers
"SYSMON1\\n" — this is how the sysmon device is told apart from any other
USB-serial device (e.g. a keyboard dongle) plugged into the same Mac.
"""

import glob
import logging
import time
from typing import Callable, Optional

import serial

from sysmon_daemon.protocol import HELLO, PING

LOG = logging.getLogger("sysmon.serial")

BAUD = 115200
PORT_GLOB = "/dev/cu.usbmodem*"

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
        # Anything else (e.g. a stale S1 echo or Studio RPC noise) — keep
        # reading until the deadline.
    return False


def find_port(timeout: float = 1.0, pattern: str = PORT_GLOB) -> Optional[str]:
    """Return the device path of the first port answering the handshake."""
    for path in sorted(glob.glob(pattern)):
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
