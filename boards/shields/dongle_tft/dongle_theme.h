/*
 * Colour themes for the dongle screen, and the button that cycles them.
 *
 * The button callback may run in interrupt context, so it only bumps an atomic
 * index; the display timer notices the change and repaints. The choice is
 * persisted under the "dongle_tft/theme" settings key.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <lvgl.h>

#define DONGLE_THEME_COUNT 4

struct dongle_theme {
    const char *name;

    lv_color_t bg;      /* both halves */
    lv_color_t divider; /* the 1 px rule between them */

    lv_color_t key_bg;   /* every key slot, bound or not */
    lv_color_t key_text; /* legends */

    lv_color_t mod_off;  /* modifier glyph, not held */
    lv_color_t mod_on;   /* modifier glyph, held */
    lv_color_t caps_on;  /* caps lock, which is a lock rather than a hold */
    lv_color_t layer;    /* layer name */

    lv_color_t usb_off;           /* USB icon when USB is not the transport */
    lv_color_t usb_on;            /* ... and when it is */
    lv_color_t bt_connected;      /* Bluetooth icon, host connected */
    lv_color_t bt_open;           /* ... advertising or looking for its host */
    lv_color_t bt_off;            /* ... idle */
    lv_color_t profile_connected; /* profile number, matching the BT states */
    lv_color_t profile_open;
    lv_color_t profile_none;

    lv_color_t bat_empty;  /* unfilled part of a gauge */
    lv_color_t bat_border; /* gauge outline and its contact nub */
    lv_color_t bat_text;   /* percentage */
    lv_color_t bat_ok;     /* fill >= 50 % */
    lv_color_t bat_low;    /* fill >= 20 % */
    lv_color_t bat_crit;   /* fill below that */

    lv_color_t cpu_label; /* the "CPU" caption */
    lv_color_t accent;    /* CPU percentage and the history bars */
    lv_color_t rule;      /* the chart's baseline */
    lv_color_t used;      /* used row, and the filled part of the two bars */
    lv_color_t free;      /* free row, and the track behind the fill */
};

/* Active theme index, always in [0, DONGLE_THEME_COUNT). */
int dongle_theme_active_index(void);

/* Theme by index; out-of-range indices are reduced modulo the count. */
const struct dongle_theme *dongle_theme_get(int index);
