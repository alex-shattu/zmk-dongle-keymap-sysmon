/*
 * Shared snapshot of the Mac system metrics received over the sysmon
 * CDC-ACM link. Written by the UART RX work item, read by the display
 * thread; both sides copy the whole struct under a spinlock.
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

/* temp_c_x10 value meaning "not available" */
#define SYSMON_TEMP_NA INT16_MIN

/* ram_pressure value meaning "not available" */
#define SYSMON_PRESSURE_NA (-1)

/* Fits "nominal", "fair", "serious", "critical" and "-" */
#define SYSMON_THERMAL_LEN 12

/* Network interface token ("WI-FI", "ETH", ...): up to 7 chars + NUL */
#define SYSMON_NET_IFACE_LEN 8

struct sysmon_state {
    /* True once at least one valid S1/S2/S3 line has been applied. */
    bool valid;
    /* k_uptime_get() at the time of the last sysmon_state_set(). */
    int64_t last_rx_ms;

    uint8_t cpu_total;   /* averaged CPU load, 0..100 % */
    uint32_t ram_used_mb;
    uint32_t ram_free_mb; /* available RAM; used + free = total */
    int16_t ram_pressure; /* 0..100 %, SYSMON_PRESSURE_NA if unknown */

    uint32_t net_up_kbps_x10;   /* KB/s * 10 */
    uint32_t net_down_kbps_x10; /* KB/s * 10 */

    uint32_t disk_used_gb_x10;  /* GB * 10 */
    uint32_t disk_free_gb_x10;  /* GB * 10; used + free = capacity */

    int16_t temp_c_x10; /* degrees C * 10, SYSMON_TEMP_NA if unknown */
    char thermal[SYSMON_THERMAL_LEN]; /* "nominal"|"fair"|"serious"|"critical"|"-" */

    /* Active network interface token; "" or "-" = unknown (badge hidden). */
    char net_iface[SYSMON_NET_IFACE_LEN];
};

/*
 * Copy *st into the shared state under the lock. last_rx_ms is stamped
 * from k_uptime_get() here; the caller-provided value is ignored.
 */
void sysmon_state_set(const struct sysmon_state *st);

/* Copy the shared state into *out under the lock. */
void sysmon_state_get(struct sysmon_state *out);
