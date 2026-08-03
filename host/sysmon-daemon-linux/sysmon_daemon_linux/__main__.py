"""Daemon entry point: collect metrics every --interval seconds and send
S3 lines to the display device, reconnecting on link loss."""

import argparse
import logging
import signal
import sys
import time

from sysmon_daemon_linux.metrics import DEFAULT_DISK_PATH, MetricsCollector
from sysmon_daemon_linux.protocol import format_line
from sysmon_daemon_linux.serial_link import SerialLink

LOG = logging.getLogger("sysmon")


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="sysmon-daemon",
        description="Send Linux system metrics to the sysmon display over serial.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="seconds between samples (default: 0.5)",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="serial port to use, skipping handshake discovery (e.g. /dev/ttyACM0)",
    )
    parser.add_argument(
        "--disk-path",
        default=DEFAULT_DISK_PATH,
        help="mount point the DISK row reports on (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # systemd stops us with SIGTERM; turn it into a clean SystemExit so the
    # finally block below closes the port.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    collector = MetricsCollector(disk_path=args.disk_path)
    link = SerialLink(port=args.port)

    LOG.info("sysmon daemon starting (interval %.3fs)", args.interval)
    try:
        while True:
            if not link.connected:
                LOG.info("searching for sysmon display...")
                link.connect()

            snapshot = collector.collect()
            line = format_line(snapshot)
            LOG.debug("send: %s", line)

            if not link.send_line(line):
                LOG.warning("link lost, will reconnect")
                continue

            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOG.info("interrupted, exiting")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
