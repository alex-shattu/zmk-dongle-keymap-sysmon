/*
 * ZMK state feeding the top half of the dongle screen.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

/*
 * Prime every widget listener with the current state. Call once, after
 * dongle_ui_create(), from the display work queue.
 */
void dongle_zmk_status_init(void);
