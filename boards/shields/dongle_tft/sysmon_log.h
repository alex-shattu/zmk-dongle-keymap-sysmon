/*
 * Log level for the sysmon link code.
 *
 * sysmon_state.c and sysmon_uart.c carry no ZMK dependency on purpose, so
 * they can also back a plain Zephyr application that gives the whole panel
 * to the monitor. There CONFIG_ZMK_LOG_LEVEL does not exist and the
 * application's own CONFIG_SYSMON_LOG_LEVEL does; picking the level here
 * keeps the files drop-in for both.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <zephyr/logging/log.h>

#if defined(CONFIG_SYSMON_LOG_LEVEL)
#define SYSMON_LOG_LEVEL CONFIG_SYSMON_LOG_LEVEL
#elif defined(CONFIG_ZMK_LOG_LEVEL)
#define SYSMON_LOG_LEVEL CONFIG_ZMK_LOG_LEVEL
#else
#define SYSMON_LOG_LEVEL LOG_LEVEL_INF
#endif
