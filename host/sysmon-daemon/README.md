**English** · [Русский](README.ru.md)

# sysmon-daemon

A macOS daemon: it samples system metrics (CPU, RAM, network, disk,
temperature / thermal state) and pushes them every 500 ms over USB CDC-ACM
(serial) to a dongle with an ST7789V 240×240 panel. The protocol is plain
line-based text (`S3|...`).

## Flashing the dongle

The firmware is built by GitHub Actions in your own config repository — see the
top-level README. Download the artifact, double-tap reset on the board (a
`NICENANO` drive appears) and copy `zmk.uf2` onto it.

The dongle exposes **two** USB functions: HID (keyboard/mouse) and a serial port
(`/dev/cu.usbmodem*`). The daemon finds the right one by handshake: it sends
`PING` and the sysmon port answers `SYSMON1`. Neither the VID/PID nor the port
number matters.

## Installing the daemon

Needs Python ≥ 3.9. From `host/sysmon-daemon`:

```sh
python3 -m venv ~/.venvs/sysmon
~/.venvs/sysmon/bin/pip install -e .
```

If the repository lives on an **external drive** and you intend to run the
daemon under launchd, install it *without* `-e` (`pip install .`): the code is
then copied into a venv on the internal drive, and the agent depends neither on
macOS granting removable-volume access nor on the drive being mounted at login
(see [Removable-volume access](#removable-volume-access-editable-install-from-an-external-drive)
below). The cost is having to reinstall the package after every edit.

### Running it by hand

```sh
~/.venvs/sysmon/bin/python -m sysmon_daemon --verbose
```

| Flag         | Description                                                        |
|--------------|--------------------------------------------------------------------|
| `--interval` | send period in seconds (default `0.5`)                             |
| `--port`     | a specific port (e.g. `/dev/cu.usbmodem14201`) — skips autodetection |
| `--verbose`  | debug logging, to stderr                                           |

If the device is absent or disappears, the daemon reconnects on its own
(1–5 s backoff). There is no need to kill or restart it.

## Starting it automatically with launchd

1. Copy the template, substituting your home directory — launchd expands
   neither `~` nor environment variables, so paths have to be absolute:

   ```sh
   mkdir -p ~/Library/LaunchAgents ~/Library/Logs
   sed "s|/Users/YOURUSER|$HOME|g" com.user.sysmon-daemon.plist \
     > ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
   ```

   If your venv is not at `~/.venvs/sysmon`, fix the `python` path in the
   resulting file too.

2. Load and start the agent:

   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
   launchctl kickstart -k gui/$(id -u)/com.user.sysmon-daemon
   ```

3. Logs and status:

   ```sh
   tail -f ~/Library/Logs/sysmon-daemon.log
   launchctl print gui/$(id -u)/com.user.sysmon-daemon | grep -E 'state|last exit'
   ```

   `last exit code = 78: EX_CONFIG` means launchd could not find the
   executable, which is almost always an unsubstituted path in the plist. You
   will not get a log either in that case, since the log path comes from the
   same file.

To stop and unload:

```sh
launchctl bootout gui/$(id -u)/com.user.sysmon-daemon
```

### Removable-volume access (editable install from an external drive)

If the package was installed with `pip install -e` and the repository sits on an
external drive, the launchd agent runs into TCC: macOS wants "Removable
Volumes" permission, and there is nowhere sensible to show that prompt to a
background agent. The symptoms are `--` on the display and this in the log:

```
PermissionError: [Errno 1] Operation not permitted: '/Volumes/.../sysmon_daemon/__init__.py'
```

Until the prompt is answered the process simply hangs in `open()` — empty log,
port never opened. After a denial it dies with the error above, and the prompt
does not come back.

Two ways out:

- **The reliable one**: reinstall without `-e`. The code moves to the internal
  drive and the permission is never needed.
- **Grant access**: reset the TCC decision, restart the agent, then click
  Allow. `tccutil` only understands bundle IDs, and for a CLI python binary the
  record is keyed by a hash of its path, so the whole service has to be reset —
  other applications will ask again next time they need it:

  ```sh
  launchctl bootout gui/$(id -u)/com.user.sysmon-daemon
  tccutil reset SystemPolicyRemovableVolumes
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.sysmon-daemon.plist
  ```

  The grant is tied to the exact path of the python binary, so after a Homebrew
  python upgrade — the path carries the version, `.../Cellar/python@3.14/3.14.6/...`
  — the prompt returns.

## Limitations on Apple Silicon

- **Temperature**: there is no unprivileged source of CPU temperature
  (`pmset -g therm` reports "CPU die temperature" on Intel only), so `-` is
  sent instead.
- **Thermal state**: inferred heuristically from `pmset -g therm` (a
  `CPU_Speed_Limit` line means throttling; "No thermal warning…" means
  `nominal`), falling back to `sysctl kern.thermalpressurelevel`. If neither
  parses, `-`.
- **RAM pressure**: `sysctl kern.memorystatus_vm_pressure_level` only gives
  coarse levels (1/2/4) rather than a percentage, so what is sent as pressure
  is `psutil.virtual_memory().percent` (used relative to available memory).

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

S3 replaces the total fields of the older S1/S2 with used/free pairs:

- **RAM**: `ram_used_mb` = total − available (roughly "Memory Used" in Activity
  Monitor), `ram_free_mb` = available (genuinely available memory, inactive
  included). used + free is the amount of RAM installed.
- **DISK**: decimal GB, the way macOS itself reports it. `disk_free_gb` is the
  space available **including purgeable** (what Finder and System Settings
  show; read via `osascript` →
  `NSURLVolumeAvailableCapacityForImportantUsageKey`, cached for 30 s, falling
  back to psutil on error). `disk_used_gb` is what the system volume group
  occupies (the `/` and `/System/Volumes/Data` volumes), i.e. the same "used"
  Finder shows. So used + free does **not** add up to the size of the disk:
  purgeable space and the housekeeping volumes (VM, Preboot, …) are in neither.

The firmware accepts the older S1 and S2 as well, converting their total
fields; the daemon always sends S3. `net_iface`, `temp_c` and `thermal_state`
are parsed but not drawn — the dongle's bottom half has no room for them.

The interface type comes from `route -n get default` (the device of the default
route) plus `networksetup -listallhardwareports` (a "Wi-Fi" port becomes
`WI-FI`, other hardware ports `ETH`); a default route through a tunnel
(`utun*` and friends, i.e. an active VPN) becomes `VPN`. The result is cached
for about 30 s, and any failure yields `-`.

Handshake: `PING` → `SYSMON1`. Unknown lines are ignored silently. With no
packets for more than 3 s the dongle replaces the bottom-half values with `--`;
the top half carries on, since the keyboard does not depend on the daemon.

To poke at it by hand — stop the daemon first, the port takes a single process:

```sh
screen /dev/cu.usbmodemXXX 115200
# type PING and Enter: the sysmon port answers SYSMON1
# then paste an S3|... line from the example above
# to leave screen: Ctrl-A, then K
```

## Tests

```sh
~/.venvs/sysmon/bin/pip install -e '.[dev]'
~/.venvs/sysmon/bin/python -m pytest tests/
```
