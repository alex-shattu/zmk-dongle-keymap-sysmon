**English** · [Русский](README.ru.md)

# zmk-dongle-keymap-sysmon

A ZMK module that turns a split-keyboard dongle into two things at once: a
keymap display and a host system monitor, on one 240×240 SPI panel.

The dongle already knows which layer is active, which modifiers are held and
how full each half's battery is. It is also plugged into the computer, so it
can be told anything the computer knows. This module puts the first on the top
half of the screen and the second on the bottom.

| Design | On the dongle | Example of a 6×4 grid |
| :----: | :-----------: | :-------------------: |
| <img src="images/dongle-screen.png" alt="The intended layout: keymap on top, system monitor below" width="250"> | <img src="images/dongle-screen-live_5x3.jpg" alt="The same screen running on the hardware" width="250"> | <img src="images/dongle-screen-live_6x4.jpg" alt="The same screen with a 6×4 keymap grid" width="250"> |

Reading it from the top:

| Row | What it shows |
| --- | ------------- |
| status | USB and Bluetooth state — carried by the icon **colour**, not by extra text — the BLE profile number, and both halves' battery levels |
| keymap | the active layer. Legends are what each key would type *right now*, so they follow shift and caps lock |
| modifiers | the modifiers currently held, caps lock in a colour of its own, and the layer name |
| CPU | the current percentage over 58 samples of history |
| memory / disk / network | used on the red row, free on the green one, between two usage bars in the same two colours |

The keyboard half is event-driven and entirely self-contained: it keeps working
with nothing running on the host. The monitor half needs a small daemon on the
computer (included, macOS and Linux); without it those rows read `--`.

> [!IMPORTANT]
> **The legends are hand-written, and this repository ships the author's
> keymap.** ZMK keeps no printable label for a binding at runtime, so what each
> key says cannot be derived from your `.keymap`. It comes from a C table,
> [`keymap_legend.c`](boards/shields/dongle_tft/keymap_legend.c), which today
> holds a six-layer 5×3+3 Charybdis layout.
>
> That table is compiled into the module and nothing in your zmk-config can
> override it, so **plan on forking this repository** rather than pointing
> `west.yml` at it — an unmodified build puts someone else's legends on your
> screen. From then on the table is yours to keep in sync by hand; no part of
> the build watches your keymap for you.
>
> Two of the three ways to get it wrong fail the build rather than the panel: a
> table whose geometry disagrees with `CONFIG_DONGLE_TFT_*`, and a
> configuration that disagrees with your `zmk,physical-layout`. The third is
> silent — a layer your keymap has and the table does not is drawn as an empty
> grid.
>
> [Adapting it to your keymap](#adapting-it-to-your-keymap) has the rules. The
> rest of the screen — status row, modifiers, battery, the whole monitor half —
> needs none of this and works untouched.

## Hardware

### Bill of materials

The hardware is the [Snake Dongle](https://github.com/joaopedropio/snake-dongle)
— board, screen and enclosure. Use **its** bill of materials, which is kept up
to date with working part links; the parts that matter here are:

| Qty | Part | Notes |
| --- | ---- | ----- |
| 1 | Pro Micro nRF52840 | a nice!nano v2 works. Other boards need their own pin overlay |
| 1 | 1.54" TFT 240×240 ST7789 module | the display from the Snake Dongle BOM. The [Waveshare 1.54" LCD](https://www.waveshare.com/1.54inch-LCD-Module.htm) is an alternative, with its own case variant |
| — | thin wire | 1.27 mm flat ribbon per that BOM; a larger gauge is easier to solder but harder to fit |
| 1 | printed enclosure | see [Case](#case) |

The panel must be **240×240 and ST7789V**: the layout is placed to the pixel and
other sizes will not fit.

You do **not** need the Snake Dongle's buzzer or its push buttons — this
firmware drives neither. Everything else in its BOM applies.

### Wiring

Signals and pins are exactly what [`dongle_tft.overlay`](boards/shields/dongle_tft/dongle_tft.overlay)
declares — if you re-pin anything, change both.

| Signal | Display pin | nRF52840 | Pro Micro pad |
| ------ | ----------- | -------- | ------------- |
| Power | `VCC` | — | `3V3` |
| Ground | `GND` | — | `GND` |
| SPI clock | `SCL` / `CLK` / `SCK` | P0.17 | `D2` |
| SPI data | `SDA` / `DIN` / `MOSI` | P0.20 | `D3` |
| Chip select | `CS` | P0.11 | `D7` |
| Data / command | `DC` | P0.24 | `D5` |
| Reset | `RES` / `RST` | P0.22 | `D4` |
| Backlight | `BLK` / `BL` | P1.04 | `D8` |
| Theme button | — | P0.31 | `D21`/`A3` |

Notes on that table:

- The pin choice follows the [Snake Dongle](https://github.com/joaopedropio/snake-dongle),
  which is why its enclosures fit.
- **The backlight is not dimmed.** ZMK switches `zmk,display-led` fully on at
  startup and only ever calls `led_off()` when it blanks the display, which this
  shield disables. So `D8` behaves exactly like `VCC` today — wiring `BL`
  straight to `VCC` works just as well. The pin is kept on a PWM channel, and
  exposed as `pwm-leds`, only so that dimming stays a possibility:
  `led_set_brightness()` would work, but nothing calls it.
- **`D2`/`D3` are also the Pro Micro I2C pads.** The module disables `i2c0`
  because on nRF52840 that peripheral shares its instance with `SPIM0` *and*
  sits on those exact pins. You therefore cannot combine `dongle_tft` with a
  shield that wants the I2C bus, such as an SSD1306 OLED.
- The display is driven **write-only** — there is no MISO wire.
- Module pin counts vary between sellers, and so does whether `CS` is broken out
  at all. Wire the display the way the
  [Snake Dongle wiring diagrams](https://github.com/joaopedropio/snake-dongle#wiring-diagram-)
  show it; the table above is the same pin-out in text form. Either of its two
  diagrams works — the "WITH Backlight Control" one matches this table, and the
  other ties `BL` to `VCC`, which as noted above makes no practical difference
  here.

### Themes

A short press on the Snake Dongle's action button cycles four palettes:

| Theme | |
| ----- | --- |
| `dark` | the palette the screen was designed in |
| `light` | the same hues darkened, on a near-white background |
| `amber` | amber on black; used and free stay red and lime, so the monitor keeps its polarity |
| `night` | `dark` at roughly half luminance. The backlight is not dimmable, so this is how you stop the panel lighting up a dark room |

The choice is saved to flash and survives a reboot and a reflash. Editing a
palette, or adding one, is a single table in
[`dongle_theme.c`](boards/shields/dongle_tft/dongle_theme.c).

The button is optional: drop the `dongle_tft_theme_button` node in your own
overlay and the callback disappears, leaving whichever theme was saved last.

### Case

Print one of the enclosures from **[felixJR123/Snake-Dongle-Case](https://github.com/felixJR123/Snake-Dongle-Case)**
— follow that repository's build guide for orientation, screws and heat-set
inserts. Pick the variant that matches your screen:

| Screen | Folder in that repo |
| ------ | ------------------- |
| Waveshare 1.54" | `Files/Waveshare Screen` |
| Original Snake Dongle screen, tactile switch | `Files/Tactile Switches` |
| Original Snake Dongle screen, mouse switches | `Files/Mouse Switches` |

There is also a `Files/Monitor Mount` stand, which is a good fit here: a system
monitor you never look at is not much use.

That guide is written for the Snake Dongle firmware, which uses a buzzer and an
action button. **This module uses neither** — skip those parts of the BOM and
their wiring. Everything else (body, screen holder, back cover, screws) applies
unchanged.

## Firmware

### Add the module to your zmk-config

In your config repository's `config/west.yml`, add the repo as a project. Point
it at **your fork**, not at this one — the legend table lives inside the module,
so customising it means owning the copy west pulls:

```yaml
manifest:
  remotes:
    - name: zmkfirmware
      url-base: https://github.com/zmkfirmware
    - name: alex-shattu
      url-base: https://github.com/alex-shattu
  projects:
    - name: zmk
      remote: zmkfirmware
      revision: main
      import: app/west.yml
    - name: zmk-dongle-keymap-sysmon
      remote: alex-shattu
      revision: master
  self:
    path: config
```

Then add `dongle_tft` next to your keyboard's own dongle shield in
`build.yaml`:

```yaml
include:
  - board: nice_nano//zmk
    shield: my_keyboard_dongle dongle_tft
    artifact-name: my_keyboard_dongle_tft
```

Push, and download the artifact from the Actions run. That is all the wiring-up
there is — the shield brings its own Kconfig defaults (colour depth, LVGL
buffers, a custom status screen, the second USB serial function). What it does
*not* bring is your legends: see
[Adapting it to your keymap](#adapting-it-to-your-keymap).

Requirements on the shield you pair it with: it has to be a **split central**
(`CONFIG_ZMK_SPLIT_ROLE_CENTRAL=y`) with two peripherals if you want both
battery gauges. [`boards/shields/example_dongle/`](boards/shields/example_dongle/)
is a minimal one you can copy.

### Flash

Double-tap reset on the board — a `NICENANO` drive appears — and copy
`zmk.uf2` onto it. The drive disappears on its own; a "disk not ejected
properly" warning at that point is normal.

## Host daemon

The bottom half is fed over a USB serial port that this firmware exposes
alongside the HID interfaces. Two Python daemons are included; they sample CPU,
memory, disk and network twice a second and write one line per sample, in the
same wire format, so the firmware cannot tell which host it is talking to:

| Daemon | Autostart | Notes |
| ------ | --------- | ----- |
| [`host/sysmon-daemon/`](host/sysmon-daemon/) (macOS) | launchd agent | shells out to `pmset`, `route` and `osascript`; no CPU temperature on Apple Silicon |
| [`host/sysmon-daemon-linux/`](host/sysmon-daemon-linux/) (Linux) | systemd user unit | reads only `/proc` and `/sys` — no subprocesses; needs a udev rule to keep ModemManager off the port |

Each has its own README ([macOS](host/sysmon-daemon/README.md),
[Linux](host/sysmon-daemon-linux/README.md)) covering installation, the
autostart unit, what each number is measured from, and the line protocol.

The protocol is deliberately trivial (one `|`-separated line of text, a `PING`
→ `SYSMON1` handshake so the daemon can find the right port), so a daemon for
a third OS is a short script. Nothing else has to change on the firmware side.

## Adapting it to your keymap

**ZMK keeps no printable label for a binding at runtime** — a behaviour is a
device pointer plus two integers. So the legends cannot be derived from your
keymap; they live in a table you edit:
[`keymap_legend.c`](boards/shields/dongle_tft/keymap_legend.c).

Rules for that table:

- One row of 36 entries per layer, in binding order: three rows of 5+5, then
  the 3+3 thumbs. Layers in the same order as your keymap node, and **one row
  per layer you have** — the layer count is the one thing not checked at build
  time, so a layer the table is missing is drawn as an empty grid.
- `NULL` for `&none` — the key is drawn as an empty slot.
- Spell `&trans` out with the legend it falls through to.
- Hold-taps get their tap side: `&hml LSHIFT A` is `"a"`.
- A single ASCII character is taken to be *what the key types unmodified*, so
  write it unshifted — `"a"`, `"1"`, `","`. Shift and caps lock are applied at
  draw time, so those keys change on screen as you hold them. Keys that already
  send a shifted keycode (`&kp EXCL`) are written as the character they produce
  and are left alone.
- Anything longer is drawn as-is in a small font; two or three characters fit a
  key.
- For an icon, use the `MDI_*` macros from
  [`mdi_icons.h`](boards/shields/dongle_tft/mdi_icons.h).

The layer *name* comes from ZMK's `display-name`, so that needs nothing.

### Checking the table against your keymap

The layer count is the one thing the build cannot check, and a table that has
drifted from the keymap shows up only on the screen — usually as a grid of
legends belonging to the layer next door. `tools/check_legends.py` compares the
two directly:

```bash
python3 tools/check_legends.py ../my-zmk-config/config/my.keymap
```

It reports every position where the two disagree: a missing or extra layer,
`NULL` against a real binding, a hold-tap labelled with its hold instead of its
tap, a `&trans` spelled with the wrong fall-through. It also works out which
thumb cluster is occupied while a layer is held — from the keymap's own
`&mo`/`&lt` positions, its combos and its conditional layers — and expects
those keys to be blank, since the thumb holding the layer cannot reach them.

Legends it cannot derive are counted as unverified rather than called wrong
(`-v` lists them); the tables at the top of the script are where you teach it
your own conventions. It exits non-zero when something is off, so it works as a
pre-commit hook.

### Grid shape

Three Kconfig values describe your split, and the screen lays itself out from
them — key size, thumb cluster placement, where the divider falls, and how much
height is left for the CPU chart:

```
CONFIG_DONGLE_TFT_COLUMNS=6   # per hand, 4-6
CONFIG_DONGLE_TFT_ROWS=4      # 3 or 4
CONFIG_DONGLE_TFT_THUMBS=5    # per hand, 1-5
```

Columns are 4–6 per hand, rows 3 or 4, thumbs 1–5 per hand, and thumbs may not
exceed columns. The screen is 240 px wide and 240 tall whatever you pick, so a
bigger grid buys itself narrower keys and a shorter chart:

| Split | Keys | Key size | Thumb | CPU chart | |
| ----- | ---: | -------- | ----- | --------: | --- |
| 4×3+3 | 30 | 25×17 | 25×14 | 44 px | |
| **5×3+3** | **36** | **20×17** | **20×14** | **44 px** | default; in daily use |
| 5×3+5 | 40 | 20×17 | 20×14 | 44 px | |
| 6×3+3 | 42 | 16×17 | 16×14 | 44 px | |
| 6×3+5 | 46 | 16×17 | 16×14 | 44 px | |
| 5×4+3 | 46 | 20×15 | 20×12 | 34 px | |
| 6×4+3 | 54 | 16×15 | 16×12 | 34 px | tried on hardware: small but readable |
| 6×4+5 | 58 | 16×15 | 16×12 | 34 px | |

A fourth row costs 10 px of chart history, which is where the height comes from —
everything below the chart is anchored to the bottom edge. Legend fonts are
picked against the key height, so the shorter keys of a four-row grid drop to a
smaller font by themselves.

Only `5×3+3` has real mileage on it; `6×4+3` has been flashed once and read
fine. The rest follow from the same arithmetic but have not been looked at.

The LVGL object pool grows with the grid — one label per key, and running out
of pool is a silent hang rather than an error — so nothing else needs tuning.

Two things are checked while compiling, so a wrong number is a build error
rather than a scrambled screen:

- the legend table's own `LEGEND_TABLE_*` has to agree with the configuration —
  otherwise the keys you forgot to add would just be blank;
- and the configuration has to agree with the **chosen `zmk,physical-layout`**,
  which is where the real key count lives.

What is *not* derived is the arrangement: a grid of two hands plus one thumb
cluster each, thumbs pushed inboard. Anything genuinely different — a numpad
column, a fifth row, staggered thumbs — needs geometry work in `dongle_ui.c`.
There is no reading it out of the physical layout either: on a Charybdis all
three left thumbs sit at the same coordinates and differ only by rotation.

## Icons

Every icon is a [Material Design Icon](https://pictogrammers.com/library/mdi/).
LVGL cannot read an SVG icon set at runtime, so the ~30 glyphs used are baked
into a font subset by [`tools/gen_mdi_font.py`](tools/gen_mdi_font.py):

```sh
python3 tools/gen_mdi_font.py     # needs network and node (for lv_font_conv)
```

`mdi_font_10.c`, `mdi_font_12.c` and `mdi_icons.h` are its **output** — to add
an icon, put its MDI name in that script's `ICONS` list and re-run it, rather
than editing the generated files.

## Building locally

Optional — CI is enough for normal use. The one trap: this repository is a
Zephyr *module*, and a module's Kconfig is looked up at `<module>/zephyr/Kconfig`.
If you also make it a west topdir, `west update` clones Zephyr into that same
`zephyr/` and configuration dies with `recursive 'source' of 'Kconfig.zephyr'`.
Keep the workspace outside the checkout:

```sh
export KEYMAP_SYSMON=/path/to/zmk-dongle-keymap-sysmon

mkdir -p ~/keymap-sysmon-west/config
ln -s "$KEYMAP_SYSMON/config/west.yml" ~/keymap-sysmon-west/config/west.yml
git -C ~/keymap-sysmon-west/config init -q . && git -C ~/keymap-sysmon-west/config add -A
git -C ~/keymap-sysmon-west/config commit -qm "west manifest"

cd ~/keymap-sysmon-west
west init -l config && west update && west zephyr-export
python3.13 -m venv .venv          # Zephyr 4.1 wants Python 3.10-3.13
.venv/bin/pip install -r zephyr/scripts/requirements-base.txt

source .venv/bin/activate
west build -p -d build/example -s zmk/app -b nice_nano//zmk -- \
  -DZMK_CONFIG="$KEYMAP_SYSMON/config" -DZMK_EXTRA_MODULES="$KEYMAP_SYSMON" \
  -DSHIELD="example_dongle dongle_tft"
```

`-DZMK_EXTRA_MODULES` takes a `;`-separated list, so to build against your own
keyboard module too:

```sh
-DZMK_EXTRA_MODULES="/path/to/your-keyboard;$KEYMAP_SYSMON"
```

You will also need the Zephyr SDK 0.17.x with `arm-zephyr-eabi`.

## How it is put together

| File | Role |
| ---- | ---- |
| `dongle_tft.overlay` | display, backlight and the second CDC-ACM function |
| `custom_status_screen.c` | ZMK entry point; also swaps in an RGB565 byte-swapping flush callback, which the ST7789V needs |
| `zmk_status.c` | ZMK event listeners → UI setters |
| `dongle_ui.c` | all of the drawing and the pixel geometry |
| `keymap_legend.c` | the per-layer legend table |
| `tools/check_legends.py` | compares that table with a keymap, position by position |
| `sysmon_uart.c`, `sysmon_state.c` | the serial line protocol; no ZMK dependency, so they also work in a plain Zephyr app |

Two details worth knowing if you go editing:

- Battery levels are read from ZMK's own array via
  `zmk_split_central_get_peripheral_battery_level()` rather than from the event
  payload. `ZMK_DISPLAY_WIDGET_LISTENER` keeps a single state slot and
  `k_work_submit()` on a pending item is a no-op, so with two halves reporting
  at once one gauge would silently never be painted — and a peripheral pushes
  its level only when it *changes*, so it would stay blank for hours.
- `CONFIG_ZMK_DISPLAY_BLANK_ON_IDLE` is forced off. A monitor that blanks after
  the idle timeout is not a monitor.

## Credits

- [ZMK](https://github.com/zmkfirmware/zmk).
- [joaopedropio/snake-dongle](https://github.com/joaopedropio/snake-dongle) —
  the hardware this pin-out and its display devicetree come from.
- [felixJR123/Snake-Dongle-Case](https://github.com/felixJR123/Snake-Dongle-Case) —
  the enclosure.
- [englmaxi/zmk-dongle-display](https://github.com/englmaxi/zmk-dongle-display) —
  the module and widget-listener patterns this follows.
- [Material Design Icons](https://pictogrammers.com/library/mdi/), Apache-2.0.

## Licence

MIT, see [LICENSE](LICENSE). The bundled MDI glyph subset is Apache-2.0 from
the Pictogrammers project.
