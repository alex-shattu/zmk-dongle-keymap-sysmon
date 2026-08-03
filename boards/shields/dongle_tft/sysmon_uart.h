/*
 * Sysmon CDC-ACM RX link. Self-contained: initialized via SYS_INIT, feeds
 * parsed S1 lines into sysmon_state and answers the PING/SYSMON1 handshake.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

/* Protocol constants (host -> device request, device -> host reply). */
#define SYSMON_UART_PING "PING"
#define SYSMON_UART_HELLO "SYSMON1\n"
