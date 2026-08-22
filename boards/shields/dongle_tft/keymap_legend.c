/*
 * Static per-layer key legends for the 3x5+3 keymap drawn on the dongle.
 *
 * >>> THIS IS THE ONE FILE YOU HAVE TO EDIT FOR YOUR OWN KEYMAP. <<<
 *
 * ZMK keeps no printable label for a binding at runtime — a behaviour is just
 * a device pointer plus two integers — so the legends have to be a table, and
 * that table has to be kept in sync with your .keymap by hand. Rows are in
 * binding order (3 rows of 5+5, then the 3+3 thumbs) and layers are in the
 * order they appear in your keymap node.
 *
 * The table below is the author's Charybdis keymap, kept as a worked example.
 * See "Adapting it to your keymap" in the README.
 *
 * Conventions:
 *   - NULL is an unbound key (&none); the UI draws it as an empty slot.
 *   - &trans is spelled out with the legend it falls through to, which on
 *     this keymap is always the Base layer (every layer above Base is
 *     momentary).
 *   - hold-taps show their tap side: `&hml LSHIFT A` reads "A", `&lt Nav
 *     ENTER` reads as Enter.
 *   - a Material Design Icon is used wherever one fits the key; everything
 *     else is a two or three letter abbreviation that fits a 20x17 key.
 *     dongle_ui.c picks the font per legend, so the two can be mixed freely.
 *   - a legend that is a single ASCII character is what the key types with
 *     no modifier held, so it is written *unshifted* — lowercase letters,
 *     "1" not "!", "," not "<". dongle_ui.c applies shift and caps lock to
 *     those at draw time. Keys that already send a shifted keycode (&kp EXCL
 *     and friends on Num/Sym) are written as the character they produce and
 *     are left alone, which is right: holding shift does not change them.
 *
 * SPDX-License-Identifier: MIT
 */

#include "keymap_legend.h"

#include <zephyr/devicetree.h>

#include "mdi_icons.h"

/*
 * The geometry this table was written for. Changing CONFIG_DONGLE_TFT_*
 * without rewriting the table would leave the extra keys silently blank, so
 * say it out loud instead.
 */
#define LEGEND_TABLE_COLUMNS 5
#define LEGEND_TABLE_ROWS 3
#define LEGEND_TABLE_THUMBS 3

BUILD_ASSERT(LEGEND_TABLE_COLUMNS == CONFIG_DONGLE_TFT_COLUMNS &&
                 LEGEND_TABLE_ROWS == CONFIG_DONGLE_TFT_ROWS &&
                 LEGEND_TABLE_THUMBS == CONFIG_DONGLE_TFT_THUMBS,
             "keymap_legend.c holds a 5x3+3 table. Rewrite it for your keymap and set "
             "LEGEND_TABLE_* to match CONFIG_DONGLE_TFT_*.");

/*
 * And the configuration has to describe the keyboard, not just the table: the
 * chosen physical layout knows how many keys there really are.
 */
#if DT_HAS_CHOSEN(zmk_physical_layout)
BUILD_ASSERT(DT_PROP_LEN(DT_CHOSEN(zmk_physical_layout), keys) == KEYMAP_LEGEND_KEYS,
             "CONFIG_DONGLE_TFT_COLUMNS/ROWS/THUMBS do not add up to the number of keys in "
             "the chosen zmk,physical-layout.");
#endif

#define K_ENTER MDI_KEYBOARD_RETURN
#define K_BSPC MDI_BACKSPACE_OUTLINE
#define K_DEL MDI_BACKSPACE_REVERSE_OUTLINE
#define K_SPACE MDI_KEYBOARD_SPACE
#define K_TAB MDI_KEYBOARD_TAB
#define K_ESC MDI_KEYBOARD_ESC
#define K_CAPS MDI_APPLE_KEYBOARD_CAPS
#define K_UP MDI_ARROW_UP
#define K_DOWN MDI_ARROW_DOWN
#define K_LEFT MDI_ARROW_LEFT
#define K_RIGHT MDI_ARROW_RIGHT
#define K_HOME MDI_ARROW_COLLAPSE_LEFT
#define K_END MDI_ARROW_COLLAPSE_RIGHT
#define K_PG_UP MDI_CHEVRON_DOUBLE_UP
#define K_PG_DN MDI_CHEVRON_DOUBLE_DOWN
#define K_SHIFT MDI_APPLE_KEYBOARD_SHIFT
#define K_CTRL MDI_APPLE_KEYBOARD_CONTROL
#define K_OPT MDI_APPLE_KEYBOARD_OPTION
#define K_CMD MDI_APPLE_KEYBOARD_COMMAND
#define K_PREV MDI_SKIP_PREVIOUS
#define K_PLAY MDI_PLAY_PAUSE
#define K_NEXT MDI_SKIP_NEXT
#define K_MUTE MDI_VOLUME_OFF
#define K_VOL_DN MDI_VOLUME_MEDIUM
#define K_VOL_UP MDI_VOLUME_HIGH
#define K_BRI_DN MDI_BRIGHTNESS_5
#define K_BRI_UP MDI_BRIGHTNESS_7

/* Base thumbs, repeated wherever a layer leaves them &trans. */
#define THUMBS_BASE_L K_ENTER, K_SPACE, K_BSPC
#define THUMBS_BASE_R K_DEL, K_SPACE, K_ENTER

static const char *const legends[][KEYMAP_LEGEND_KEYS] = {
    /* Base */
    {
        "q", "w", "e", "r", "t",  "y", "u", "i", "o", "p",
        "a", "s", "d", "f", "g",  "h", "j", "k", "l", ";",
        "z", "x", "c", "v", "b",  "n", "m", ",", ".", "/",
        THUMBS_BASE_L, THUMBS_BASE_R,
    },
    /*
     * Num. Structurally Sym, with digits on the top row instead of their
     * shifted forms — so holding shift here turns 1234567890 into !@#$%^&*()
     * on the screen as well as on the wire.
     */
    {
        "1",  "2",  "3",  "4",  "5",   "6",  "7",  "8",  "9",  "0",
        "`",  "\\", "-",  "=",  NULL,  NULL, "[",  "]",  "'",  ";",
        NULL, NULL, NULL, NULL, NULL,  NULL, NULL, ",",  ".",  "/",
        K_ENTER, K_SPACE, K_BSPC, THUMBS_BASE_R,
    },
    /* Nav */
    {
        "F1",   "F2",    "F3",  "F4",  "F5",   "F6",  "F7",    "F8",    "F9",     "F10",
        K_TAB,  K_CTRL,  K_OPT, K_CMD, "F11",  "F12", K_HOME,  K_UP,    K_END,    K_PG_UP,
        K_CAPS, K_ESC,   NULL,  NULL,  NULL,   NULL,  K_LEFT,  K_DOWN,  K_RIGHT,  K_PG_DN,
        THUMBS_BASE_L, THUMBS_BASE_R,
    },
    /* Sym */
    {
        "!",  "@",  "#",  "$",  "%",   "^",  "&",  "*",  "(",  ")",
        "`",  "\\", "-",  "=",  NULL,  NULL, "[",  "]",  "'",  ";",
        NULL, NULL, NULL, NULL, NULL,  NULL, NULL, ",",  ".",  "/",
        THUMBS_BASE_L, THUMBS_BASE_R,
    },
    /* Sys */
    {
        "BLD", NULL, NULL, NULL, "BTC",  "OUT", K_PREV,  K_PLAY,   K_NEXT,   "BLD",
        "B1",  "B2", "B3", "B4", "B5",   NULL,  K_MUTE,  K_VOL_DN, K_VOL_UP, NULL,
        "RST", NULL, NULL, NULL, NULL,   NULL,  NULL,    K_BRI_DN, K_BRI_UP, "RST",
        K_ENTER, NULL, NULL, NULL, NULL, K_ENTER,
    },
    /* Mouse (held by the R+T combo) */
    {
        NULL, NULL, NULL, NULL, NULL,  NULL, NULL,  NULL,  NULL,  NULL,
        NULL, NULL, NULL, NULL, NULL,  NULL, K_CMD, K_OPT, K_CTRL, K_SHIFT,
        NULL, NULL, NULL, NULL, NULL,  NULL, NULL,  NULL,  NULL,  NULL,
        NULL, NULL, NULL, "M3", "M1", "M2",
    },
};

#define LAYER_COUNT ((uint8_t)(sizeof(legends) / sizeof(legends[0])))

uint8_t keymap_legend_layer_count(void) { return LAYER_COUNT; }

const char *keymap_legend_get(uint8_t layer, uint8_t position) {
    if (layer >= LAYER_COUNT || position >= KEYMAP_LEGEND_KEYS) {
        return NULL;
    }

    return legends[layer][position];
}
