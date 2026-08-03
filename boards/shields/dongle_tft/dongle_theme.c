/*
 * Colour themes for the dongle screen, and the button that cycles them.
 *
 * Wiring for the button is the Snake Dongle's action button, P0.31, active low
 * with a pull-up. It is reached through Zephyr's input subsystem rather than
 * ZMK's kscan-sideband machinery: this is not a key, it never produces a
 * keycode, and a gpio-keys node plus one callback is the whole of it.
 *
 * SPDX-License-Identifier: MIT
 */

#include "dongle_theme.h"

#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

/* Compile-time lv_color_t from 0xRRGGBB; lv_color_hex() is not constant. */
#define RGB(hex)                                                                                   \
    LV_COLOR_MAKE((uint8_t)(((hex) >> 16) & 0xFF), (uint8_t)(((hex) >> 8) & 0xFF),                  \
                  (uint8_t)((hex) & 0xFF))

static const struct dongle_theme themes[DONGLE_THEME_COUNT] = {
    {
        /* The palette the screen was designed in. */
        .name = "dark",
        .bg = RGB(0x000000),
        .divider = RGB(0x1D2329),
        .key_bg = RGB(0x1E242B),
        .key_text = RGB(0xE8E6E1),
        .mod_off = RGB(0x2F363D),
        .mod_on = RGB(0xE8E6E1),
        .caps_on = RGB(0xF2B45C),
        .layer = RGB(0x7FD1C1),
        .usb_off = RGB(0x3A4149),
        .usb_on = RGB(0xD8B25A),
        .bt_connected = RGB(0x4C9DFB),
        .bt_open = RGB(0xF2B45C),
        .bt_off = RGB(0x333B43),
        .profile_connected = RGB(0x3DDC97),
        .profile_open = RGB(0xF2B45C),
        .profile_none = RGB(0x3A4149),
        .bat_empty = RGB(0x0B0D10),
        .bat_border = RGB(0x7A848E),
        .bat_text = RGB(0xC8CFD6),
        .bat_ok = RGB(0x3DDC97),
        .bat_low = RGB(0xF2B45C),
        .bat_crit = RGB(0xE2574C),
        .cpu_label = RGB(0x8B959F),
        .accent = RGB(0x7FD1C1),
        .rule = RGB(0x2B333B),
        .used = RGB(0xE2574C),
        .free = RGB(0x3DDC97),
    },
    {
        /* Same hues, darkened, on a near-white background. */
        .name = "light",
        .bg = RGB(0xF4F6F8),
        .divider = RGB(0xC2CBD3),
        .key_bg = RGB(0xE1E7EC),
        .key_text = RGB(0x15191E),
        .mod_off = RGB(0xBAC3CB),
        .mod_on = RGB(0x15191E),
        .caps_on = RGB(0xA96A00),
        .layer = RGB(0x0C7266),
        .usb_off = RGB(0xAAB3BB),
        .usb_on = RGB(0x8A5F00),
        .bt_connected = RGB(0x0E58B4),
        .bt_open = RGB(0xA96A00),
        .bt_off = RGB(0xAAB3BB),
        .profile_connected = RGB(0x0B6E47),
        .profile_open = RGB(0xA96A00),
        .profile_none = RGB(0xA0A9B1),
        .bat_empty = RGB(0xFFFFFF),
        .bat_border = RGB(0x66707A),
        .bat_text = RGB(0x28303A),
        .bat_ok = RGB(0x0B6E47),
        .bat_low = RGB(0xA96A00),
        .bat_crit = RGB(0xB02218),
        .cpu_label = RGB(0x56606C),
        .accent = RGB(0x0C7266),
        .rule = RGB(0xC2CBD3),
        .used = RGB(0xB02218),
        .free = RGB(0x0B6E47),
    },
    {
        /* Amber family on black; used/free stay a warm red and a lime so the
         * monitor keeps its polarity. */
        .name = "amber",
        .bg = RGB(0x000000),
        .divider = RGB(0x3A2A0C),
        .key_bg = RGB(0x241A08),
        .key_text = RGB(0xFFC46B),
        .mod_off = RGB(0x6B5220),
        .mod_on = RGB(0xFFC46B),
        .caps_on = RGB(0xFFE082),
        .layer = RGB(0xFFA726),
        .usb_off = RGB(0x6B5220),
        .usb_on = RGB(0xFFD54F),
        .bt_connected = RGB(0xFFCC80),
        .bt_open = RGB(0xFFB300),
        .bt_off = RGB(0x5A4718),
        .profile_connected = RGB(0xFFB300),
        .profile_open = RGB(0xFFD54F),
        .profile_none = RGB(0x6B5220),
        .bat_empty = RGB(0x140E04),
        .bat_border = RGB(0x8A6B3A),
        .bat_text = RGB(0xFFC46B),
        .bat_ok = RGB(0xA8D048),
        .bat_low = RGB(0xFFB300),
        .bat_crit = RGB(0xFF5C3C),
        .cpu_label = RGB(0x8A6B3A),
        .accent = RGB(0xFFA726),
        .rule = RGB(0x3A2A0C),
        .used = RGB(0xFF5C3C),
        .free = RGB(0xA8D048),
    },
    {
        /*
         * The dark palette at roughly half luminance. The backlight is not
         * dimmable (see the README), so this is the way to stop the panel
         * lighting up a dark room.
         */
        .name = "night",
        .bg = RGB(0x000000),
        .divider = RGB(0x11151A),
        .key_bg = RGB(0x0F1317),
        .key_text = RGB(0x7A7874),
        .mod_off = RGB(0x1A1F24),
        .mod_on = RGB(0x7A7874),
        .caps_on = RGB(0x7E5E2E),
        .layer = RGB(0x426E65),
        .usb_off = RGB(0x1E2226),
        .usb_on = RGB(0x6F5C2E),
        .bt_connected = RGB(0x28527F),
        .bt_open = RGB(0x7E5E2E),
        .bt_off = RGB(0x1A1F24),
        .profile_connected = RGB(0x20724F),
        .profile_open = RGB(0x7E5E2E),
        .profile_none = RGB(0x1E2226),
        .bat_empty = RGB(0x05070A),
        .bat_border = RGB(0x3F454B),
        .bat_text = RGB(0x666C72),
        .bat_ok = RGB(0x20724F),
        .bat_low = RGB(0x7E5E2E),
        .bat_crit = RGB(0x762E26),
        .cpu_label = RGB(0x474D53),
        .accent = RGB(0x426E65),
        .rule = RGB(0x171B20),
        .used = RGB(0x762E26),
        .free = RGB(0x20724F),
    },
};

static atomic_t theme_idx = ATOMIC_INIT(0);

int dongle_theme_active_index(void) {
    return (int)(atomic_get(&theme_idx) % DONGLE_THEME_COUNT);
}

const struct dongle_theme *dongle_theme_get(int index) {
    return &themes[(unsigned int)index % DONGLE_THEME_COUNT];
}

/* --- persistence ---------------------------------------------------------- */

#if IS_ENABLED(CONFIG_SETTINGS)

#include <zephyr/settings/settings.h>

static void theme_save_work_handler(struct k_work *work) {
    ARG_UNUSED(work);

    uint8_t idx = (uint8_t)dongle_theme_active_index();
    int err = settings_save_one("dongle_tft/theme", &idx, sizeof(idx));

    if (err != 0) {
        LOG_WRN("failed to save the dongle_tft theme (%d)", err);
    }
}

static K_WORK_DELAYABLE_DEFINE(theme_save_work, theme_save_work_handler);

static int theme_settings_set(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg) {
    if (strcmp(key, "theme") != 0) {
        return -ENOENT;
    }

    uint8_t idx;

    if (len != sizeof(idx)) {
        return -EINVAL;
    }

    ssize_t rc = read_cb(cb_arg, &idx, sizeof(idx));

    if (rc < 0) {
        return (int)rc;
    }

    atomic_set(&theme_idx, idx % DONGLE_THEME_COUNT);
    return 0;
}

SETTINGS_STATIC_HANDLER_DEFINE(dongle_tft, "dongle_tft", NULL, theme_settings_set, NULL, NULL);

#endif /* CONFIG_SETTINGS */

/* --- the button ----------------------------------------------------------- */

#if DT_NODE_EXISTS(DT_NODELABEL(dongle_tft_theme_button))

#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/input/input.h>

/*
 * Depending on the input mode this can run in interrupt context, so it does
 * nothing but an atomic increment and an (ISR-safe) work reschedule. The save
 * is debounced, so cycling through the themes causes one flash write.
 */
static void theme_button_cb(struct input_event *evt, void *user_data) {
    ARG_UNUSED(user_data);

    if (evt->type != INPUT_EV_KEY || evt->code != INPUT_KEY_0 || evt->value != 1) {
        return;
    }

    atomic_inc(&theme_idx);

#if IS_ENABLED(CONFIG_SETTINGS)
    k_work_reschedule(&theme_save_work, K_SECONDS(1));
#endif
}

INPUT_CALLBACK_DEFINE(DEVICE_DT_GET(DT_PARENT(DT_NODELABEL(dongle_tft_theme_button))),
                      theme_button_cb, NULL);

#endif /* DT_NODE_EXISTS(dongle_tft_theme_button) */
