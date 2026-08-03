/*
 * Custom ZMK status screen for a dongle with an ST7789V 240x240 panel.
 *
 * zmk_display_status_screen() is a weak symbol in ZMK's display/main.c; this
 * strong definition replaces it when CONFIG_ZMK_DISPLAY_STATUS_SCREEN_CUSTOM
 * is set. It runs on the display work queue, before anything is rendered,
 * which is also the only safe place to swap the LVGL flush callback.
 *
 * The keyboard half is event-driven (zmk_status.c). The system-monitor half
 * is polled from an lv_timer, because sysmon_state is filled by a UART work
 * item that knows nothing about LVGL.
 *
 * SPDX-License-Identifier: MIT
 */

#include "dongle_ui.h"
#include "zmk_status.h"

#include <zephyr/kernel.h>

#include <lvgl.h>
#include <lvgl_display.h> /* Zephyr LVGL glue: lvgl_flush_cb_16bit() */

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#include <zmk/display/status_screen.h>

#include "sysmon_state.h"

/* The daemon sends a sample every ~500 ms. */
#define SYSMON_POLL_MS 250

/* No packet for this long -> the monitor half shows placeholders. */
#define SYSMON_STALE_MS 3000

/*
 * Flush callback with an RGB565 byte swap.
 *
 * LVGL's software renderer emits RGB565 as native little-endian uint16_t
 * (low byte GGGBBBBB first in memory), while the ST7789V expects the high
 * byte RRRRRGGG first on the 8-bit SPI wire, and everything in between —
 * the LVGL glue, the st7789v driver, mipi_dbi_spi — passes the buffer
 * through untouched. This LVGL revision predates
 * LV_COLOR_FORMAT_RGB565_SWAPPED, so the swap happens here: convert the
 * rendered area in place (safe, since LV_DISPLAY_RENDER_MODE_PARTIAL fully
 * re-renders every area before its next flush), then hand it to the stock
 * 16-bit glue flush, which copies bytes verbatim to display_write().
 *
 * The swap is open-coded rather than taken from lv_draw_sw_rgb565_swap(),
 * whose header is not reachable through <lvgl.h> in the revision ZMK pins.
 */
static void flush_cb_swapped(lv_display_t *display, const lv_area_t *area, uint8_t *px_map) {
    uint32_t bytes = 2U * (uint32_t)lv_area_get_width(area) * (uint32_t)lv_area_get_height(area);

    for (uint32_t i = 0; i + 1U < bytes; i += 2U) {
        uint8_t low = px_map[i];

        px_map[i] = px_map[i + 1U];
        px_map[i + 1U] = low;
    }

    lvgl_flush_cb_16bit(display, area, px_map);
}

static void sysmon_timer_cb(lv_timer_t *timer) {
    ARG_UNUSED(timer);

    struct sysmon_state st;

    sysmon_state_get(&st);

    bool connected = st.valid && (k_uptime_get() - st.last_rx_ms) <= SYSMON_STALE_MS;

    dongle_ui_set_sysmon(&st, connected);
}

lv_obj_t *zmk_display_status_screen(void) {
    /* The Zephyr glue registered lvgl_flush_cb_16bit at SYS_INIT time;
     * replace it before the first render. Nothing re-registers it later. */
    lv_display_set_flush_cb(lv_display_get_default(), flush_cb_swapped);

    lv_obj_t *screen = lv_obj_create(NULL);

    dongle_ui_create(screen);
    dongle_zmk_status_init();

    lv_timer_create(sysmon_timer_cb, SYSMON_POLL_MS, NULL);

    return screen;
}
