/*
 * Static per-layer key legends for the keymap drawn on the dongle.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <stdint.h>

/*
 * Keys per layer, in binding order: CONFIG_DONGLE_TFT_ROWS rows of
 * COLUMNS + COLUMNS, then THUMBS + THUMBS. That is the order ZMK reads a
 * keymap's bindings in, so a legend table is a transcription of the keymap
 * with no reshuffling.
 */
#define KEYMAP_LEGEND_ROW_KEYS (2 * CONFIG_DONGLE_TFT_COLUMNS)
#define KEYMAP_LEGEND_GRID_KEYS (CONFIG_DONGLE_TFT_ROWS * KEYMAP_LEGEND_ROW_KEYS)
#define KEYMAP_LEGEND_THUMB_KEYS (2 * CONFIG_DONGLE_TFT_THUMBS)
#define KEYMAP_LEGEND_KEYS (KEYMAP_LEGEND_GRID_KEYS + KEYMAP_LEGEND_THUMB_KEYS)

/* Number of layers the table covers. */
uint8_t keymap_legend_layer_count(void);

/*
 * Legend for `position` on `layer`, or NULL when the key is unbound (&none):
 * an unbound key is drawn as an empty slot. Out-of-range arguments give NULL.
 * Strings are either plain text or a single MDI icon.
 */
const char *keymap_legend_get(uint8_t layer, uint8_t position);
