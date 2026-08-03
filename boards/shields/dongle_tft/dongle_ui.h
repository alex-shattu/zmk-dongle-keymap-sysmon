/*
 * Dongle screen (ST7789V 240x240): keyboard status on top, Mac system
 * monitor at the bottom.
 *
 * Every function here touches LVGL objects and must therefore run on the
 * display work queue: dongle_ui_create() from zmk_display_status_screen(),
 * the setters from the widget listeners and the sysmon lv_timer.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include <lvgl.h>

#include "sysmon_state.h"

/* Modifier bits of dongle_ui_set_mods(), in status-row order. */
#define DONGLE_UI_MOD_SHIFT (1U << 0)
#define DONGLE_UI_MOD_CTRL (1U << 1)
#define DONGLE_UI_MOD_OPT (1U << 2)
#define DONGLE_UI_MOD_CMD (1U << 3)
#define DONGLE_UI_MOD_CAPS (1U << 4)

struct dongle_ui_output {
    /* The keyboard is reporting over the wire right now. */
    bool usb_selected;
    /* Active BLE profile, 0-based; shown one-based. */
    uint8_t profile;
    /* Active profile has a host connected. */
    bool ble_connected;
    /* Active profile has no bond, i.e. it is advertising for one. */
    bool ble_open;
};

/* Build the whole screen. Call once. */
void dongle_ui_create(lv_obj_t *screen);

/* Connection icons and the profile number of the status row. */
void dongle_ui_set_output(struct dongle_ui_output state);

/* Battery gauge for split peripheral `source` (0 or 1), level in percent. */
void dongle_ui_set_battery(uint8_t source, uint8_t level);

/* Redraw the keymap for `layer` and update the layer name. */
void dongle_ui_set_layer(uint8_t layer, const char *name);

/* Highlight the held modifiers; `mods` is a mask of DONGLE_UI_MOD_*. */
void dongle_ui_set_mods(uint8_t mods);

/*
 * Refresh the system monitor. While `connected` is false the values read
 * "--" and the bars sit at zero, so a stopped daemon is obvious; the CPU
 * history keeps whatever it last collected.
 */
void dongle_ui_set_sysmon(const struct sysmon_state *st, bool connected);
