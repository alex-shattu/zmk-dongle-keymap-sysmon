/*
 * Static per-layer key legends for the 3x5+3 keymap drawn on the dongle.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <stdint.h>

/* 3 rows of 10 + 6 thumbs, in keymap binding order. */
#define KEYMAP_LEGEND_KEYS 36

/* Number of layers the table covers. */
uint8_t keymap_legend_layer_count(void);

/*
 * Legend for `position` on `layer`, or NULL when the key is unbound (&none):
 * an unbound key is drawn as an empty slot. Out-of-range arguments give NULL.
 * Strings are either plain text or a single LVGL symbol.
 */
const char *keymap_legend_get(uint8_t layer, uint8_t position);
