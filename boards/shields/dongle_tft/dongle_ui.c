/*
 * Dongle screen (ST7789V 240x240), LVGL 9.
 *
 * The design splits the panel in two halves of 120 px. Coordinates below are
 * the mock-up's (which is drawn at 2x) divided by two.
 *
 *   TOP — keyboard
 *     y   3..18   status row: USB and Bluetooth icons (state is carried by
 *                 their colour alone), the one-based profile number, and the
 *                 two split batteries — a 19x10 gauge plus its percentage,
 *                 set in the same size as the profile number
 *     y  24..97   keymap of the active layer, 3 rows of 5+5 keys 20x17 on a
 *                 2 px gap with 12 px between the hands, then the 3+3 thumbs
 *                 (20x14) pushed inboard against the centre gap. Every slot
 *                 is filled #1E242B whether it is bound or not, so the grid
 *                 reads as a whole. Legends show what the key would type
 *                 right now, so they follow shift and caps lock as well as
 *                 the layer
 *     y 102..117  the five Apple modifier glyphs (inactive #2F363D, active
 *                 white, caps lock amber) and the layer name, right-aligned
 *     y 120       1 px divider #1D2329
 *
 *   BOTTOM — system monitor
 *     y 124..139  "CPU" and the percentage on one baseline
 *     y 142..185  58 bars, 2 px wide on an exact 4 px pitch, over 230 px
 *     y 188       baseline rule
 *     y 194..196  RAM bar (used red on a green free track)
 *     y 200..212  used row, red:   RAM used / disk used / upload
 *     y 215..227  free row, green: RAM free / disk free / download
 *                 (three fixed left-aligned columns, so the up and down
 *                  arrows stay in one place as the rates change width)
 *     y 231..233  disk bar, same shape as the RAM one
 *
 * All numbers are formatted with integer math: values arrive as x10 integers
 * and newlib-nano printf may lack %f.
 *
 * SPDX-License-Identifier: MIT
 */

#include "dongle_ui.h"
#include "keymap_legend.h"
#include "mdi_icons.h"

#include <stdint.h>
#include <string.h>

#include <zephyr/sys/util.h>

/* --- palette (mock-up) ---------------------------------------------------- */

/*
 * Both halves are pure black rather than the two near-blacks of the mock-up.
 * The panel is a backlit IPS, so black is the one colour it renders without
 * any wash, and the 1 px divider is what keeps the two halves apart.
 */
#define COL_BG 0x000000
#define COL_DIVIDER 0x1D2329

/*
 * Every key slot is filled, bound or not. The mock-up sank unbound keys into
 * the background, but a complete grid is a fixed frame of reference: it makes
 * the count of empty slots between the edge and the first bound key readable
 * at a glance. An unbound key is simply a slot with no legend.
 */
#define COL_KEY_BG 0x1E242B
#define COL_KEY_TEXT 0xE8E6E1

/* Unfilled part of a battery gauge. */
#define COL_BAT_EMPTY 0x0B0D10

#define COL_MOD_OFF 0x2F363D
#define COL_MOD_ON 0xE8E6E1
#define COL_CAPS_ON 0xF2B45C
#define COL_LAYER 0x7FD1C1

#define COL_USB_OFF 0x3A4149
#define COL_USB_ON 0xD8B25A
#define COL_BT_CONNECTED 0x4C9DFB
#define COL_BT_OPEN 0xF2B45C
#define COL_BT_OFF 0x333B43

#define COL_PROFILE_CONNECTED 0x3DDC97
#define COL_PROFILE_OPEN 0xF2B45C
#define COL_PROFILE_NONE 0x3A4149

#define COL_BAT_BORDER 0x7A848E
#define COL_BAT_TEXT 0xC8CFD6
#define COL_BAT_OK 0x3DDC97
#define COL_BAT_LOW 0xF2B45C
#define COL_BAT_CRIT 0xE2574C

#define COL_CPU_LABEL 0x8B959F
#define COL_ACCENT 0x7FD1C1
#define COL_RULE 0x2B333B
#define COL_USED 0xE2574C
#define COL_FREE 0x3DDC97

/* Battery gauge thresholds, in percent. */
#define BAT_OK_PCT 50U
#define BAT_LOW_PCT 20U

/* --- layout --------------------------------------------------------------- */

#define SCREEN_W 240
#define PAD_X 5

/* status row */
#define ICON_Y 4
#define USB_ICON_X PAD_X
#define BT_ICON_X 21
#define PROFILE_X 38
#define TEXT_Y 3
#define BAT_BODY_W 19
#define BAT_BODY_H 10
#define BAT_BODY_Y 6
#define BAT_TIP_W 2
#define BAT_TIP_H 4
#define BAT_LABEL_W 27
#define BAT0_BODY_X 125
#define BAT0_LABEL_X 150
#define BAT1_BODY_X 183
#define BAT1_LABEL_X 208

/* keymap */
#define KEY_W 20
#define KEY_H 17
#define THUMB_H 14
#define KEY_GAP 2
#define HAND_GAP 12
#define GRID_X 6
#define ROW0_Y 24
#define ROW_PITCH 20
#define THUMB_Y 84
#define KEY_RADIUS 3

/* modifier + layer row */
#define MODS_Y 103
#define MOD_PITCH 16
#define LAYER_Y 102

/* system monitor */
#define DIVIDER_Y 120
#define CPU_VALUE_Y 124
#define CPU_LABEL_Y 127
#define CHART_Y 142
#define CHART_H 44
#define CHART_W 230
#define RULE_Y 188
#define RAM_BAR_Y 194
#define BAR_H 3
#define ROW_USED_Y 200
#define ROW_FREE_Y 215
#define DISK_BAR_Y 231

/*
 * The three value columns are left-aligned at fixed offsets instead of being
 * spread edge to edge like the mock-up. Memory and, above all, the network
 * rates change width with every sample, and anything but a fixed left edge
 * makes the up/down arrows slide horizontally rather than sit one above the
 * other. Each column is placed with room for its widest plausible value:
 * "65535MB", "9999.9GB" and an arrow plus "9999.9MB/s", the last of which
 * still ends clear of the 235 px right margin.
 */
#define COL_MEM_X PAD_X
#define COL_DISK_X 88
/* The network arrow is a separate label so it never moves at all. */
#define COL_NET_ICON_X 160
#define COL_NET_X 172

/*
 * 58 bars over 230 px. lv_chart's draw_series_bar divides with integers:
 * block_w = (w - (n-1) * gap) / n and x(i) = (w - block_w) * i / (n - 1).
 * With w=230, n=58, gap=2 that is block_w = 116/58 = 2 and
 * x(i) = 228*i/57 = 4*i exactly, so every one of the 57 gaps is 2 px.
 */
#define CPU_POINTS 58

/* Data arrives at ~2 Hz; push into the chart at most once per second. */
#define CHART_PUSH_MS 900

/* --- widgets -------------------------------------------------------------- */

static lv_obj_t *usb_icon;
static lv_obj_t *bt_icon;
static lv_obj_t *profile_label;

struct battery_gauge {
    lv_obj_t *body;
    lv_obj_t *fill;
    lv_obj_t *label;
};

static struct battery_gauge batteries[2];

static lv_obj_t *keys[KEYMAP_LEGEND_KEYS];

/* Modifier indicators, in the order the design lays them out. */
enum mod_slot {
    MOD_SLOT_SHIFT,
    MOD_SLOT_CTRL,
    MOD_SLOT_OPT,
    MOD_SLOT_CMD,
    MOD_SLOT_CAPS,
    MOD_SLOT_COUNT,
};

static const char *const mod_slot_icon[MOD_SLOT_COUNT] = {
    [MOD_SLOT_SHIFT] = MDI_APPLE_KEYBOARD_SHIFT,
    [MOD_SLOT_CTRL] = MDI_APPLE_KEYBOARD_CONTROL,
    [MOD_SLOT_OPT] = MDI_APPLE_KEYBOARD_OPTION,
    [MOD_SLOT_CMD] = MDI_APPLE_KEYBOARD_COMMAND,
    [MOD_SLOT_CAPS] = MDI_APPLE_KEYBOARD_CAPS,
};

static const uint8_t mod_slot_bit[MOD_SLOT_COUNT] = {
    [MOD_SLOT_SHIFT] = DONGLE_UI_MOD_SHIFT, [MOD_SLOT_CTRL] = DONGLE_UI_MOD_CTRL,
    [MOD_SLOT_OPT] = DONGLE_UI_MOD_OPT,     [MOD_SLOT_CMD] = DONGLE_UI_MOD_CMD,
    [MOD_SLOT_CAPS] = DONGLE_UI_MOD_CAPS,
};

static lv_obj_t *mod_icons[MOD_SLOT_COUNT];
static lv_obj_t *layer_label;

static lv_obj_t *cpu_label;
static lv_obj_t *cpu_value;
static lv_obj_t *cpu_chart;
static lv_chart_series_t *cpu_series;

static lv_obj_t *ram_bar;
static lv_obj_t *disk_bar;

static lv_obj_t *used_mem, *used_disk, *used_net;
static lv_obj_t *free_mem, *free_disk, *free_net;

/*
 * Keymap state. The grid shows what each key would type right now, so it
 * depends on the held modifiers as well as on the layer; `active_*` is what
 * we have been told, `drawn_*` what is on screen.
 */
static uint8_t active_layer;
static bool active_shift;
static bool active_caps;

static int drawn_layer = -1;
static bool drawn_shift;
static bool drawn_caps;

/* Modifier mask currently drawn, so unchanged glyphs are not repainted. */
static uint8_t drawn_mods;

/* Timestamp of the last snapshot applied (dedup) and pushed (1 Hz throttle). */
static int64_t last_sample_ms = -1;
static int64_t last_push_ms = -1;

/* --- helpers -------------------------------------------------------------- */

/* Plain rectangle: no padding, no border, no scrolling. */
static lv_obj_t *make_rect(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h,
                           uint32_t color) {
    lv_obj_t *obj = lv_obj_create(parent);

    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(obj, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(obj, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(obj, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), LV_PART_MAIN);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    return obj;
}

static lv_obj_t *make_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color,
                            const char *text, int32_t x, int32_t y) {
    lv_obj_t *label = lv_label_create(parent);

    lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(color), LV_PART_MAIN);
    lv_label_set_text(label, text);
    lv_obj_set_pos(label, x, y);
    return label;
}

/* Right-aligned label: its right edge stays put as the text changes. */
static lv_obj_t *make_label_right(lv_obj_t *parent, const lv_font_t *font, uint32_t color,
                                  const char *text, int32_t right_pad, int32_t y) {
    lv_obj_t *label = lv_label_create(parent);

    lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(label, lv_color_hex(color), LV_PART_MAIN);
    lv_label_set_text(label, text);
    lv_obj_align(label, LV_ALIGN_TOP_RIGHT, -right_pad, y);
    return label;
}

static lv_obj_t *make_track_bar(lv_obj_t *parent, int32_t y) {
    lv_obj_t *bar = lv_bar_create(parent);

    lv_obj_set_pos(bar, PAD_X, y);
    lv_obj_set_size(bar, CHART_W, BAR_H);
    lv_bar_set_range(bar, 0, 100);
    lv_bar_set_value(bar, 0, LV_ANIM_OFF);
    lv_obj_set_style_radius(bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(bar, 0, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, LV_PART_INDICATOR);
    /* The track is the free colour and the indicator the used one, so a bar
     * carries the same red-over-green meaning as the two value rows around
     * it: red is what is taken, green what is left. */
    lv_obj_set_style_bg_color(bar, lv_color_hex(COL_FREE), LV_PART_MAIN);
    lv_obj_set_style_bg_color(bar, lv_color_hex(COL_USED), LV_PART_INDICATOR);
    return bar;
}

/*
 * A key is a single label styled as a rounded tile: one object instead of a
 * rectangle plus a child, which matters at 36 of them. Vertical centring is
 * done with a top pad, because a label with a fixed height draws its text at
 * the top.
 */
static const lv_font_t *key_font(const char *text) {
    /* The icons live in the private use area (U+E000..U+EFFF), whose UTF-8
     * always starts with 0xEE; a text legend is plain ASCII and never can. */
    if ((uint8_t)text[0] == 0xEEu) {
        return &mdi_font_12;
    }

    uint8_t code_points = 0;

    for (const char *p = text; *p != '\0'; p++) {
        if (((uint8_t)*p & 0xC0) != 0x80) {
            code_points++;
        }
    }

    return (code_points <= 1) ? &lv_font_montserrat_12 : &lv_font_montserrat_8;
}

/*
 * US layout shift pairs, unshifted character first. Letters are handled
 * separately; everything not listed types the same with shift held.
 */
static const char shift_pairs[] = "`~"
                                  "1!2@3#4$5%6^7&8*9(0)"
                                  "-_=+[{]}\\|;:'\",<.>/?";

/*
 * The legend to draw for a key given the modifiers held right now. Only a
 * single ASCII character is a thing the key actually types; icons and
 * abbreviations pass through untouched. `buf` receives the substitution.
 */
static const char *legend_for_mods(const char *legend, char *buf, bool shift, bool caps) {
    if (legend == NULL || legend[0] == '\0' || legend[1] != '\0' || (uint8_t)legend[0] >= 0x80u) {
        return legend;
    }

    char c = legend[0];

    if (c >= 'a' && c <= 'z') {
        /* Caps lock and shift cancel out, exactly as the host resolves them. */
        if (shift != caps) {
            c = (char)(c - 'a' + 'A');
        }
    } else if (shift) {
        for (size_t i = 0; shift_pairs[i] != '\0'; i += 2) {
            if (shift_pairs[i] == c) {
                c = shift_pairs[i + 1];
                break;
            }
        }
    }

    if (c == legend[0]) {
        return legend;
    }

    buf[0] = c;
    buf[1] = '\0';
    return buf;
}

static void key_set_legend(lv_obj_t *key, const char *legend, int32_t height) {
    if (legend == NULL) {
        legend = "";
    }

    /* Shift is pressed constantly while typing and every press redraws the
     * whole grid, so skip the keys whose legend did not actually change:
     * lv_label_set_text always copies and invalidates. */
    if (strcmp(lv_label_get_text(key), legend) == 0) {
        return;
    }

    /* The slot itself is painted once in make_key(); only the legend moves. */
    if (legend[0] == '\0') {
        lv_label_set_text(key, "");
        return;
    }

    const lv_font_t *font = key_font(legend);
    int32_t pad_top = (height - (int32_t)font->line_height) / 2;

    lv_obj_set_style_text_font(key, font, LV_PART_MAIN);
    lv_obj_set_style_pad_top(key, pad_top > 0 ? pad_top : 0, LV_PART_MAIN);
    lv_label_set_text(key, legend);
}

/* Redraw the grid if the layer or the modifiers that change legends moved. */
static void keymap_sync(void) {
    if ((int)active_layer == drawn_layer && active_shift == drawn_shift &&
        active_caps == drawn_caps) {
        return;
    }

    drawn_layer = active_layer;
    drawn_shift = active_shift;
    drawn_caps = active_caps;

    for (uint8_t i = 0; i < KEYMAP_LEGEND_KEYS; i++) {
        char buf[2];
        const char *legend =
            legend_for_mods(keymap_legend_get(active_layer, i), buf, active_shift, active_caps);

        key_set_legend(keys[i], legend, (i < 30) ? KEY_H : THUMB_H);
    }
}

static lv_obj_t *make_key(lv_obj_t *parent, int32_t x, int32_t y, int32_t h) {
    lv_obj_t *key = lv_label_create(parent);

    lv_label_set_long_mode(key, LV_LABEL_LONG_MODE_CLIP);
    lv_obj_set_pos(key, x, y);
    lv_obj_set_size(key, KEY_W, h);
    lv_obj_set_style_pad_all(key, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(key, KEY_RADIUS, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(key, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(key, lv_color_hex(COL_KEY_BG), LV_PART_MAIN);
    lv_obj_set_style_text_color(key, lv_color_hex(COL_KEY_TEXT), LV_PART_MAIN);
    lv_obj_set_style_text_align(key, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    return key;
}

/* --- integer formatting --------------------------------------------------- */

/* "<v/10>.<v%10><suffix>" */
static void fmt_x10(char *buf, size_t size, uint32_t v_x10, const char *suffix) {
    lv_snprintf(buf, size, "%u.%u%s", v_x10 / 10U, v_x10 % 10U, suffix);
}

/* KB/s x10 -> "123.4KB/s" below 1000.0 KB/s, else "2.0MB/s" (KB -> MB = /1024). */
static void fmt_rate(char *buf, size_t size, uint32_t kbps_x10) {
    if (kbps_x10 < 10000U) {
        fmt_x10(buf, size, kbps_x10, "KB/s");
    } else {
        fmt_x10(buf, size, (kbps_x10 + 512U) / 1024U, "MB/s");
    }
}

/* --- construction --------------------------------------------------------- */

static void create_status_row(lv_obj_t *screen) {
    usb_icon = make_label(screen, &mdi_font_12, COL_USB_OFF, MDI_USB, USB_ICON_X, ICON_Y);
    bt_icon = make_label(screen, &mdi_font_12, COL_BT_OFF, MDI_BLUETOOTH_OFF, BT_ICON_X, ICON_Y);
    profile_label =
        make_label(screen, &lv_font_montserrat_12, COL_PROFILE_NONE, "-", PROFILE_X, TEXT_Y);

    static const int32_t body_x[2] = {BAT0_BODY_X, BAT1_BODY_X};
    static const int32_t label_x[2] = {BAT0_LABEL_X, BAT1_LABEL_X};

    for (int i = 0; i < 2; i++) {
        lv_obj_t *body = make_rect(screen, body_x[i], BAT_BODY_Y, BAT_BODY_W, BAT_BODY_H,
                                   COL_BAT_EMPTY);

        lv_obj_set_style_border_width(body, 1, LV_PART_MAIN);
        lv_obj_set_style_border_color(body, lv_color_hex(COL_BAT_BORDER), LV_PART_MAIN);
        lv_obj_set_style_radius(body, 1, LV_PART_MAIN);

        /* Contact nub on the right, drawn on the screen so it is not clipped
         * by the body's border box. */
        make_rect(screen, body_x[i] + BAT_BODY_W, BAT_BODY_Y + (BAT_BODY_H - BAT_TIP_H) / 2,
                  BAT_TIP_W, BAT_TIP_H, COL_BAT_BORDER);

        lv_obj_t *fill = make_rect(body, 0, 0, 1, BAT_BODY_H - 2, COL_BAT_OK);

        lv_obj_t *label = lv_label_create(screen);

        lv_obj_set_style_text_font(label, &lv_font_montserrat_12, LV_PART_MAIN);
        lv_obj_set_style_text_color(label, lv_color_hex(COL_BAT_TEXT), LV_PART_MAIN);
        lv_obj_set_width(label, BAT_LABEL_W);
        lv_obj_set_pos(label, label_x[i], TEXT_Y);
        lv_label_set_text(label, "--");

        batteries[i] = (struct battery_gauge){.body = body, .fill = fill, .label = label};

        /* Nothing is known about a half until it reports in. */
        lv_obj_add_flag(fill, LV_OBJ_FLAG_HIDDEN);
    }
}

static void create_keymap(lv_obj_t *screen) {
    /* Three rows of five keys per hand. */
    for (int row = 0; row < 3; row++) {
        int32_t y = ROW0_Y + row * ROW_PITCH;

        for (int col = 0; col < 5; col++) {
            int32_t left_x = GRID_X + col * (KEY_W + KEY_GAP);
            int32_t right_x = left_x + 5 * (KEY_W + KEY_GAP) - KEY_GAP + HAND_GAP;

            keys[row * 10 + col] = make_key(screen, left_x, y, KEY_H);
            keys[row * 10 + 5 + col] = make_key(screen, right_x, y, KEY_H);
        }
    }

    /* Thumbs: three per hand, inboard as on the keyboard itself. The left
     * cluster ends flush with the right edge of the left hand and the right
     * one starts flush with the left edge of the right hand, so the two
     * groups straddle the centre gap and leave two empty columns at the
     * outer edges. */
    int32_t left_hand_right = GRID_X + 5 * KEY_W + 4 * KEY_GAP;
    int32_t right_hand_left = left_hand_right + HAND_GAP;

    for (int col = 0; col < 3; col++) {
        int32_t left_x = left_hand_right - (3 - col) * (KEY_W + KEY_GAP) + KEY_GAP;
        int32_t right_x = right_hand_left + col * (KEY_W + KEY_GAP);

        keys[30 + col] = make_key(screen, left_x, THUMB_Y, THUMB_H);
        keys[33 + col] = make_key(screen, right_x, THUMB_Y, THUMB_H);
    }
}

static void create_mod_row(lv_obj_t *screen) {
    /* mdi_font_12 puts its baseline at y+11 and montserrat_12 at y+12, so the
     * glyphs sit one pixel below the layer name to share its baseline. */
    for (int i = 0; i < MOD_SLOT_COUNT; i++) {
        mod_icons[i] = make_label(screen, &mdi_font_12, COL_MOD_OFF, mod_slot_icon[i],
                                  PAD_X + i * MOD_PITCH, MODS_Y);
    }

    layer_label = make_label_right(screen, &lv_font_montserrat_12, COL_LAYER, "", PAD_X, LAYER_Y);
}

static void create_sysmon(lv_obj_t *screen) {
    /* No panel rectangle: the bottom half is the screen background, so the
     * divider is the only thing separating it from the keyboard half. */
    make_rect(screen, 0, DIVIDER_Y, SCREEN_W, 1, COL_DIVIDER);

    /* montserrat_10 puts its baseline at y+9 (line_height 11, base_line 2)
     * and montserrat_12 at y+12, so the caption drops three pixels to share
     * the percentage's baseline. */
    cpu_label = make_label(screen, &lv_font_montserrat_10, COL_CPU_LABEL, "CPU", PAD_X,
                           CPU_LABEL_Y);
    lv_obj_set_style_text_letter_space(cpu_label, 1, LV_PART_MAIN);
    cpu_value = make_label_right(screen, &lv_font_montserrat_12, COL_ACCENT, "--%", PAD_X,
                                 CPU_VALUE_Y);

    cpu_chart = lv_chart_create(screen);
    lv_obj_set_pos(cpu_chart, PAD_X, CHART_Y);
    lv_obj_set_size(cpu_chart, CHART_W, CHART_H);
    lv_obj_remove_flag(cpu_chart, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(cpu_chart, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(cpu_chart, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(cpu_chart, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(cpu_chart, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_pad_column(cpu_chart, 2, LV_PART_MAIN);
    lv_obj_set_style_pad_column(cpu_chart, 0, LV_PART_ITEMS);
    lv_obj_set_style_radius(cpu_chart, 0, LV_PART_ITEMS);

    lv_chart_set_type(cpu_chart, LV_CHART_TYPE_BAR);
    lv_chart_set_point_count(cpu_chart, CPU_POINTS);
    lv_chart_set_update_mode(cpu_chart, LV_CHART_UPDATE_MODE_SHIFT);
    lv_chart_set_div_line_count(cpu_chart, 0, 0);
    lv_chart_set_range(cpu_chart, LV_CHART_AXIS_PRIMARY_Y, 0, 100);
    cpu_series = lv_chart_add_series(cpu_chart, lv_color_hex(COL_ACCENT), LV_CHART_AXIS_PRIMARY_Y);

    make_rect(screen, PAD_X, RULE_Y, CHART_W, 1, COL_RULE);

    ram_bar = make_track_bar(screen, RAM_BAR_Y);

    used_mem = make_label(screen, &lv_font_montserrat_10, COL_USED, "--", COL_MEM_X, ROW_USED_Y);
    used_disk = make_label(screen, &lv_font_montserrat_10, COL_USED, "--", COL_DISK_X, ROW_USED_Y);
    used_net = make_label(screen, &lv_font_montserrat_10, COL_USED, "--", COL_NET_X, ROW_USED_Y);

    free_mem = make_label(screen, &lv_font_montserrat_10, COL_FREE, "--", COL_MEM_X, ROW_FREE_Y);
    free_disk = make_label(screen, &lv_font_montserrat_10, COL_FREE, "--", COL_DISK_X, ROW_FREE_Y);
    free_net = make_label(screen, &lv_font_montserrat_10, COL_FREE, "--", COL_NET_X, ROW_FREE_Y);

    /* mdi_font_10 and montserrat_10 share line_height 11 and base_line 2, so
     * the arrows need no vertical nudge to sit on the value's baseline. */
    make_label(screen, &mdi_font_10, COL_USED, MDI_ARROW_UP, COL_NET_ICON_X, ROW_USED_Y);
    make_label(screen, &mdi_font_10, COL_FREE, MDI_ARROW_DOWN, COL_NET_ICON_X, ROW_FREE_Y);

    disk_bar = make_track_bar(screen, DISK_BAR_Y);
}

void dongle_ui_create(lv_obj_t *screen) {
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(screen, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(screen, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(screen, lv_color_hex(COL_BG), LV_PART_MAIN);

    create_status_row(screen);
    create_keymap(screen);
    create_mod_row(screen);
    create_sysmon(screen);

    /* Base layer until the first zmk_layer_state_changed arrives. */
    dongle_ui_set_layer(0, NULL);
}

/* --- updates -------------------------------------------------------------- */

void dongle_ui_set_output(struct dongle_ui_output state) {
    if (state.usb_selected) {
        lv_obj_set_style_text_color(usb_icon, lv_color_hex(COL_USB_ON), LV_PART_MAIN);
        lv_label_set_text(bt_icon, MDI_BLUETOOTH_OFF);
        lv_obj_set_style_text_color(bt_icon, lv_color_hex(COL_BT_OFF), LV_PART_MAIN);
        lv_obj_set_style_text_color(profile_label, lv_color_hex(COL_PROFILE_NONE), LV_PART_MAIN);
        lv_label_set_text(profile_label, "-");
        return;
    }

    const char *bt_glyph = MDI_BLUETOOTH_OFF;
    uint32_t bt_color = COL_BT_OFF;
    uint32_t profile_color = COL_PROFILE_NONE;

    if (state.ble_connected) {
        bt_glyph = MDI_BLUETOOTH_CONNECT;
        bt_color = COL_BT_CONNECTED;
        profile_color = COL_PROFILE_CONNECTED;
    } else if (state.ble_open) {
        /* Bonded but disconnected keeps the plain glyph too: either way the
         * profile is looking for its host. */
        bt_glyph = MDI_BLUETOOTH;
        bt_color = COL_BT_OPEN;
        profile_color = COL_PROFILE_OPEN;
    }

    lv_obj_set_style_text_color(usb_icon, lv_color_hex(COL_USB_OFF), LV_PART_MAIN);
    lv_label_set_text(bt_icon, bt_glyph);
    lv_obj_set_style_text_color(bt_icon, lv_color_hex(bt_color), LV_PART_MAIN);
    lv_obj_set_style_text_color(profile_label, lv_color_hex(profile_color), LV_PART_MAIN);
    lv_label_set_text_fmt(profile_label, "%u", (unsigned int)state.profile + 1U);
}

void dongle_ui_set_battery(uint8_t source, uint8_t level) {
    if ((size_t)source >= ARRAY_SIZE(batteries)) {
        return;
    }

    struct battery_gauge *gauge = &batteries[source];

    /* A half that has never reported reads 0 %; show it as unknown rather
     * than as an empty battery. */
    if (level == 0U) {
        lv_obj_add_flag(gauge->fill, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(gauge->label, "--");
        return;
    }

    uint32_t color = COL_BAT_CRIT;

    if (level >= BAT_OK_PCT) {
        color = COL_BAT_OK;
    } else if (level >= BAT_LOW_PCT) {
        color = COL_BAT_LOW;
    }

    /* 1 px border on both sides leaves 17 px of travel; never fully empty,
     * so a low battery still reads as a sliver rather than as nothing. */
    int32_t width = (int32_t)((17U * level + 99U) / 100U);

    lv_obj_set_width(gauge->fill, width > 0 ? width : 1);
    lv_obj_set_style_bg_color(gauge->fill, lv_color_hex(color), LV_PART_MAIN);
    lv_obj_remove_flag(gauge->fill, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text_fmt(gauge->label, "%u%%", (unsigned int)level);
}

void dongle_ui_set_layer(uint8_t layer, const char *name) {
    char upper[13];

    if (name != NULL && name[0] != '\0') {
        size_t i = 0;

        for (; name[i] != '\0' && i < sizeof(upper) - 1U; i++) {
            char c = name[i];

            upper[i] = (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c;
        }
        upper[i] = '\0';
        lv_label_set_text(layer_label, upper);
    } else {
        lv_label_set_text_fmt(layer_label, "L%u", (unsigned int)layer);
    }

    active_layer = layer;
    keymap_sync();
}

void dongle_ui_set_mods(uint8_t mods) {
    /* Shift and caps lock change what the keys would type, not just which
     * indicators are lit. */
    active_shift = (mods & DONGLE_UI_MOD_SHIFT) != 0U;
    active_caps = (mods & DONGLE_UI_MOD_CAPS) != 0U;
    keymap_sync();

    uint8_t changed = mods ^ drawn_mods;

    if (changed == 0U) {
        return;
    }
    drawn_mods = mods;

    for (int i = 0; i < MOD_SLOT_COUNT; i++) {
        if ((changed & mod_slot_bit[i]) == 0U) {
            continue;
        }

        uint32_t color = COL_MOD_OFF;

        if (mods & mod_slot_bit[i]) {
            color = (i == MOD_SLOT_CAPS) ? COL_CAPS_ON : COL_MOD_ON;
        }

        lv_obj_set_style_text_color(mod_icons[i], lv_color_hex(color), LV_PART_MAIN);
    }
}

static void sysmon_clear(void) {
    lv_label_set_text(cpu_value, "--%");
    lv_label_set_text(used_mem, "--");
    lv_label_set_text(used_disk, "--");
    lv_label_set_text(used_net, "--");
    lv_label_set_text(free_mem, "--");
    lv_label_set_text(free_disk, "--");
    lv_label_set_text(free_net, "--");
    lv_bar_set_value(ram_bar, 0, LV_ANIM_OFF);
    lv_bar_set_value(disk_bar, 0, LV_ANIM_OFF);
}

void dongle_ui_set_sysmon(const struct sysmon_state *st, bool connected) {
    char buf[24];

    if (!connected || !st->valid) {
        if (last_sample_ms != -1) {
            last_sample_ms = -1;
            sysmon_clear();
        }
        return;
    }

    /* The timer runs faster than the daemon sends; skip repeated snapshots. */
    if (st->last_rx_ms == last_sample_ms) {
        return;
    }
    last_sample_ms = st->last_rx_ms;

    lv_label_set_text_fmt(cpu_value, "%u%%", (unsigned int)st->cpu_total);

    if (last_push_ms < 0 || (st->last_rx_ms - last_push_ms) >= CHART_PUSH_MS) {
        last_push_ms = st->last_rx_ms;
        /* Keep idle bars visible: 2 of 100 is 1 px at 44 px height. */
        lv_chart_set_next_value(cpu_chart, cpu_series,
                                (st->cpu_total < 2U) ? 2 : (int32_t)st->cpu_total);
    }

    lv_label_set_text_fmt(used_mem, "%uMB", (unsigned int)st->ram_used_mb);
    lv_label_set_text_fmt(free_mem, "%uMB", (unsigned int)st->ram_free_mb);

    fmt_x10(buf, sizeof(buf), st->disk_used_gb_x10, "GB");
    lv_label_set_text(used_disk, buf);
    fmt_x10(buf, sizeof(buf), st->disk_free_gb_x10, "GB");
    lv_label_set_text(free_disk, buf);

    fmt_rate(buf, sizeof(buf), st->net_up_kbps_x10);
    lv_label_set_text(used_net, buf);
    fmt_rate(buf, sizeof(buf), st->net_down_kbps_x10);
    lv_label_set_text(free_net, buf);

    uint32_t ram_total = st->ram_used_mb + st->ram_free_mb;

    lv_bar_set_value(ram_bar,
                     ram_total > 0U ? (int32_t)(((uint64_t)st->ram_used_mb * 100U) / ram_total) : 0,
                     LV_ANIM_OFF);

    uint64_t disk_total_x10 = (uint64_t)st->disk_used_gb_x10 + st->disk_free_gb_x10;

    lv_bar_set_value(disk_bar,
                     disk_total_x10 > 0U
                         ? (int32_t)(((uint64_t)st->disk_used_gb_x10 * 100U) / disk_total_x10)
                         : 0,
                     LV_ANIM_OFF);
}
