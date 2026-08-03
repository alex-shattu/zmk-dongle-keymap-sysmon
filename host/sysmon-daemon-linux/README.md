**English** · [Русский](README.ru.md)

# sysmon-daemon-linux

A Linux daemon: it samples system metrics (CPU, RAM, network, disk,
temperature / thermal state) and pushes them every 500 ms over USB CDC-ACM
(serial) to a dongle with an ST7789V 240×240 panel. The protocol is plain
line-based text (`S3|...`) — byte for byte the same one the
[macOS daemon](../sysmon-daemon/README.md) speaks, so the firmware cannot
tell the two hosts apart.

Everything it reads comes from `/proc`, `/sys` and psutil: **no subprocesses
and no privileges**, unlike the macOS version, which has to shell out to
`pmset`, `route` and `osascript`.

## Flashing the dongle

The firmware is built by GitHub Actions in your own config repository — see the
top-level README. Download the artifact, double-tap reset on the board (a
`NICENANO` drive appears) and copy `zmk.uf2` onto it.

The dongle exposes **two** USB functions: HID (keyboard/mouse) and a serial
port (`/dev/ttyACM*`). The daemon finds the right one by handshake: it sends
`PING` and the sysmon port answers `SYSMON1`. Neither the VID/PID nor the port
number matters.

## Port permissions and ModemManager

Two things stand between a freshly plugged dongle and a working link, and both
are Linux-only:

- **Group.** `/dev/ttyACM*` belongs to `dialout` (Debian, Ubuntu, Fedora) or
  `uucp` (Arch). A user outside it gets `Permission denied` on every port.
- **ModemManager.** It probes every new `/dev/ttyACM*` with AT commands and
  keeps it open for several seconds, which the daemon sees as a busy port or
  as a handshake that times out on the port that would have answered.

The bundled udev rule fixes both — it tags the port for the seated user
(`uaccess`, so no group juggling and no logout) and tells ModemManager to keep
its hands off:

```sh
sudo cp 99-zmk-sysmon.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Unplug and replug the dongle afterwards. The rule matches ZMK's default
VID/PID; check yours and edit the file if they differ:

```sh
udevadm info -q property -n /dev/ttyACM0 | grep -E 'ID_VENDOR_ID|ID_MODEL_ID'
```

The blunter alternatives are `sudo usermod -aG dialout $USER` (log out and back
in) for the permission half and `sudo systemctl mask ModemManager` for the
other — the latter only if nothing on the machine dials a modem.

## Installing the daemon

Needs Python ≥ 3.9. From `host/sysmon-daemon-linux`:

```sh
python3 -m venv ~/.venvs/sysmon
~/.venvs/sysmon/bin/pip install -e .
```

If the repository lives on a removable drive and you intend to run the daemon
under systemd, install it *without* `-e` (`pip install .`) so the code is
copied into the venv and the unit does not depend on the drive being mounted at
login. The cost is having to reinstall the package after every edit.

### Running it by hand

```sh
~/.venvs/sysmon/bin/python -m sysmon_daemon_linux --verbose
```

| Flag          | Description                                                        |
|---------------|--------------------------------------------------------------------|
| `--interval`  | send period in seconds (default `0.5`)                             |
| `--port`      | a specific port (e.g. `/dev/ttyACM0`) — skips autodetection        |
| `--disk-path` | mount point the DISK row reports on (default `/`)                  |
| `--verbose`   | debug logging, to stderr                                           |

If the device is absent or disappears, the daemon reconnects on its own
(1–5 s backoff). There is no need to kill or restart it.

`--disk-path` has no macOS counterpart, and exists because a Linux machine
frequently keeps its interesting free space somewhere other than `/` — point it
at `/home` or a data mount if that is where the number you care about lives.

## Starting it automatically with systemd

A **user** unit, not a system one: the daemon needs no root, and a user unit
inherits the session that owns the port.

1. Install the unit — systemd expands `%h`, so unlike the macOS plist this file
   needs no editing:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp sysmon-daemon.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now sysmon-daemon
   ```

   If your venv is not at `~/.venvs/sysmon`, fix the `ExecStart` path (that is
   also where extra flags such as `--disk-path /home` go).

2. Logs and status:

   ```sh
   journalctl --user -u sysmon-daemon -f
   systemctl --user status sysmon-daemon
   ```

   `status=203/EXEC` means systemd could not find the interpreter — almost
   always a wrong `ExecStart` path.

3. To keep it running when you are not logged in graphically (a headless box,
   or a machine you reach over SSH):

   ```sh
   loginctl enable-linger $USER
   ```

   Note that `uaccess` in the udev rule only grants the port to a *seated*
   user, so with linger you also need the `dialout`/`uucp` group membership.

To stop and disable:

```sh
systemctl --user disable --now sysmon-daemon
```

## Where the numbers come from

| Metric | Source |
| ------ | ------ |
| CPU | `psutil.cpu_percent()`, i.e. `/proc/stat` deltas, averaged over all cores |
| RAM | `MemTotal − MemAvailable` used, `MemAvailable` free — the used/available pair of `free -h` |
| Disk | `statvfs` on `--disk-path`: used = `(f_blocks − f_bfree) × f_frsize`, free = `f_bavail × f_frsize` — the Used and Avail columns of `df` |
| Network | `/proc/net/dev` counters via psutil, differentiated over the sampling interval |
| Temperature | `psutil.sensors_temperatures()` (hwmon / thermal zones under `/sys`) |
| Interface type | the default route's device from `/proc/net/route`, classified against `/sys/class/net` |

Details worth knowing:

- **Temperature** picks a chip in a fixed order — `coretemp` (Intel),
  `k10temp`/`zenpower` (AMD), `cpu_thermal`/`soc_thermal` (ARM SoCs), `acpitz`
  last — and inside a chip prefers a `Package`/`Tctl`/`Tdie` label over the
  per-core readings, falling back to the hottest core. A machine that exposes
  no CPU sensor at all (many VMs and containers) sends `-`.
- **Thermal state** is derived from that same temperature: as a fraction of the
  sensor's own `critical` (or `high`) threshold where it publishes one,
  otherwise from fixed bands (`nominal` below 70 °C, `fair` below 85,
  `serious` below 95, `critical` above). No temperature means no state.
- **Disk** free is `f_bavail`, not `f_bfree`, so the root-reserved blocks
  (5 % of an ext4 filesystem by default) do not count as free. used + free is
  therefore smaller than the partition size. Decimal GB throughout, like
  `df --si`.
- **Interface type**: among several default routes the lowest metric wins; a
  device with `wireless/` or `phy80211/` under `/sys/class/net` is `WI-FI`; a
  tunnel name (`tun*`, `tap*`, `wg*`, `ppp*`, `ipsec*`, `nordlynx*`,
  `tailscale*`, `zt*`) means an active VPN and becomes `VPN`; anything else
  carrying the default route — including bridges and virtio devices — is
  `ETH`. If there is no IPv4 default route, `/proc/net/ipv6_route` is tried.
- The interface type and the disk figures are **cached for ~30 s**, so the
  500 ms loop touches the filesystem only twice a minute.

## The S3 protocol (for debugging)

The host sends one line per sample, `\n`-terminated, fields separated by `|`,
`-` for not available:

```
S3|<cpu%>|<ram_used_mb>|<ram_free_mb>|<ram_pressure%>|<net_up_kbps>|<net_down_kbps>|<disk_used_gb>|<disk_free_gb>|<temp_c>|<thermal_state>|<net_iface>
```

| Field           | Format                                     |
|-----------------|--------------------------------------------|
| `cpu%`          | integer 0–100, averaged over all cores     |
| `ram_used_mb`, `ram_free_mb` | integers, MiB (÷1024²)        |
| `ram_pressure%` | integer 0–100, or `-`                      |
| `net_up_kbps`, `net_down_kbps` | KB/s, one decimal place     |
| `disk_used_gb`, `disk_free_gb` | **decimal** GB (÷10⁹), one decimal place |
| `temp_c`        | °C with one decimal place, or `-`          |
| `thermal_state` | `nominal`/`fair`/`serious`/`critical`/`-`  |
| `net_iface`     | 1–7 chars of `[A-Z0-9-]` (`WI-FI`/`ETH`/`VPN`), or `-` |

Example: `S3|23|8432|7952|45|123.4|2048.7|346.3|138.5|58.3|nominal|WI-FI`

S3 replaces the total fields of the older S1/S2 with used/free pairs, so
`ram_used_mb + ram_free_mb` is the installed RAM. The firmware accepts S1 and
S2 as well, converting their total fields; the daemon always sends S3.
`net_iface`, `temp_c` and `thermal_state` are parsed but not drawn — the
dongle's bottom half has no room for them.

Handshake: `PING` → `SYSMON1`. Unknown lines are ignored silently. With no
packets for more than 3 s the dongle replaces the bottom-half values with `--`;
the top half carries on, since the keyboard does not depend on the daemon.

To poke at it by hand — stop the daemon first, the port takes a single process:

```sh
screen /dev/ttyACM0 115200
# type PING and Enter: the sysmon port answers SYSMON1
# then paste an S3|... line from the example above
# to leave screen: Ctrl-A, then K
```

## Tests

The suite injects every external input (clock, counters, statvfs, file reads,
sensors), so it neither reads the real `/proc` nor opens a port — it passes on
a non-Linux machine too:

```sh
~/.venvs/sysmon/bin/pip install -e '.[dev]'
~/.venvs/sysmon/bin/python -m pytest tests/
```
