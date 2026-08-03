/*
 * ZMK state feeding the top half of the dongle screen.
 *
 * Each concern is a ZMK_DISPLAY_WIDGET_LISTENER: the event manager callback
 * runs wherever the event was raised and only snapshots state under a mutex,
 * then a work item on the display queue does the LVGL work. Nothing here
 * touches widgets directly.
 *
 * SPDX-License-Identifier: MIT
 */

#include "zmk_status.h"
#include "dongle_ui.h"

#include <zephyr/kernel.h>

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#include <dt-bindings/zmk/modifiers.h>
#include <zmk/display.h>
#include <zmk/endpoints.h>
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>
#include <zmk/events/endpoint_changed.h>
#include <zmk/events/keycode_state_changed.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/hid.h>
#include <zmk/keymap.h>

#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <zmk/ble.h>
#include <zmk/events/ble_active_profile_changed.h>
#endif

#if IS_ENABLED(CONFIG_ZMK_USB)
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/usb.h>
#endif

#if IS_ENABLED(CONFIG_ZMK_HID_INDICATORS)
#include <zmk/events/hid_indicators_changed.h>
#include <zmk/hid_indicators.h>

/* HID keyboard LED report: bit 0 num lock, bit 1 caps lock, bit 2 scroll. */
#define HID_INDICATOR_CAPS_LOCK BIT(1)
#endif

/* --- output: USB / Bluetooth icons and the profile number ----------------- */

static struct dongle_ui_output output_get_state(const zmk_event_t *eh) {
    ARG_UNUSED(eh);

    struct dongle_ui_output state = {
        .usb_selected = zmk_endpoint_get_selected().transport == ZMK_TRANSPORT_USB,
    };

#if IS_ENABLED(CONFIG_ZMK_BLE)
    state.profile = (uint8_t)zmk_ble_active_profile_index();
    state.ble_connected = zmk_ble_active_profile_is_connected();
    state.ble_open = zmk_ble_active_profile_is_open();
#endif

    return state;
}

static void output_update_cb(struct dongle_ui_output state) { dongle_ui_set_output(state); }

ZMK_DISPLAY_WIDGET_LISTENER(dongle_output, struct dongle_ui_output, output_update_cb,
                            output_get_state)
ZMK_SUBSCRIPTION(dongle_output, zmk_endpoint_changed);
#if IS_ENABLED(CONFIG_ZMK_BLE)
ZMK_SUBSCRIPTION(dongle_output, zmk_ble_active_profile_changed);
#endif
#if IS_ENABLED(CONFIG_ZMK_USB)
ZMK_SUBSCRIPTION(dongle_output, zmk_usb_conn_state_changed);
#endif

/* --- split peripheral batteries ------------------------------------------- */

#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)

#include <zmk/split/central.h>

/*
 * Deliberately not a ZMK_DISPLAY_WIDGET_LISTENER: that macro keeps a single
 * slot for the event payload, and k_work_submit() on an already pending item
 * is a no-op. Both halves report within milliseconds of each other when they
 * connect, so the second event would overwrite the first and one gauge would
 * never be painted. It would also stay unpainted: a peripheral pushes its
 * level only when it changes (zmk/app/src/battery.c), not on every sample,
 * so the lost reading is not repeated for as long as the charge holds.
 *
 * ZMK keeps every peripheral's last level in an array of its own, so the
 * work item ignores the event payload and repaints both gauges from that.
 */
static void dongle_battery_update(struct k_work *work) {
    ARG_UNUSED(work);

    for (uint8_t source = 0; source < ZMK_SPLIT_BLE_PERIPHERAL_COUNT; source++) {
        uint8_t level;

        /* 0 is "nothing reported yet" in ZMK's array as well as here. */
        if (zmk_split_central_get_peripheral_battery_level(source, &level) == 0 && level > 0U) {
            dongle_ui_set_battery(source, level);
        }
    }
}

K_WORK_DEFINE(dongle_battery_work, dongle_battery_update);

static int battery_listener(const zmk_event_t *eh) {
    ARG_UNUSED(eh);

    if (zmk_display_is_initialized()) {
        k_work_submit_to_queue(zmk_display_work_q(), &dongle_battery_work);
    }

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(dongle_battery, battery_listener);
ZMK_SUBSCRIPTION(dongle_battery, zmk_peripheral_battery_state_changed);

#endif /* CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING */

/* --- active layer: the keymap grid and the layer name --------------------- */

struct layer_state {
    uint8_t index;
    const char *name;
};

static struct layer_state layer_get_state(const zmk_event_t *eh) {
    ARG_UNUSED(eh);

    zmk_keymap_layer_index_t index = zmk_keymap_highest_layer_active();

    return (struct layer_state){
        .index = index,
        .name = zmk_keymap_layer_name(zmk_keymap_layer_index_to_id(index)),
    };
}

static void layer_update_cb(struct layer_state state) {
    dongle_ui_set_layer(state.index, state.name);
}

ZMK_DISPLAY_WIDGET_LISTENER(dongle_layer, struct layer_state, layer_update_cb, layer_get_state)
ZMK_SUBSCRIPTION(dongle_layer, zmk_layer_state_changed);

/* --- held modifiers and caps lock ----------------------------------------- */

struct mods_state {
    uint8_t mask;
};

static struct mods_state mods_get_state(const zmk_event_t *eh) {
    ARG_UNUSED(eh);

    zmk_mod_flags_t mods = zmk_hid_get_explicit_mods();
    uint8_t mask = 0;

    if (mods & (MOD_LSFT | MOD_RSFT)) {
        mask |= DONGLE_UI_MOD_SHIFT;
    }
    if (mods & (MOD_LCTL | MOD_RCTL)) {
        mask |= DONGLE_UI_MOD_CTRL;
    }
    if (mods & (MOD_LALT | MOD_RALT)) {
        mask |= DONGLE_UI_MOD_OPT;
    }
    if (mods & (MOD_LGUI | MOD_RGUI)) {
        mask |= DONGLE_UI_MOD_CMD;
    }

#if IS_ENABLED(CONFIG_ZMK_HID_INDICATORS)
    if (zmk_hid_indicators_get_current_profile() & HID_INDICATOR_CAPS_LOCK) {
        mask |= DONGLE_UI_MOD_CAPS;
    }
#endif

    return (struct mods_state){.mask = mask};
}

static void mods_update_cb(struct mods_state state) { dongle_ui_set_mods(state.mask); }

ZMK_DISPLAY_WIDGET_LISTENER(dongle_mods, struct mods_state, mods_update_cb, mods_get_state)
ZMK_SUBSCRIPTION(dongle_mods, zmk_keycode_state_changed);
#if IS_ENABLED(CONFIG_ZMK_HID_INDICATORS)
ZMK_SUBSCRIPTION(dongle_mods, zmk_hid_indicators_changed);
#endif

/* -------------------------------------------------------------------------- */

void dongle_zmk_status_init(void) {
    dongle_output_init();
    dongle_layer_init();
    dongle_mods_init();

#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
    /* Already on the display queue, so paint directly. This also picks up
     * halves that reported before the screen existed. */
    dongle_battery_update(NULL);
#endif
}
