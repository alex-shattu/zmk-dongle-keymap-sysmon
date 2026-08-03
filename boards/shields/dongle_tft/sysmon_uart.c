/*
 * Sysmon CDC-ACM RX link.
 *
 * Deliberately free of any ZMK dependency, so the same file can back a plain
 * Zephyr application as well as this shield. Keep it that way.
 *
 * Data flow: UART ISR drains the FIFO into a ring buffer and submits a
 * k_work item to the system workqueue. The work item assembles lines
 * (LF-terminated, tolerating CRLF, <= 256 bytes — longer lines are
 * discarded up to the next LF), parses "S1|..."/"S2|..."/"S3|..." metric
 * lines into sysmon_state and answers "PING" with "SYSMON1\n"
 * (uart_poll_out from work context, never from the ISR). Unknown lines
 * are silently ignored; malformed S1/S2/S3 lines are dropped without
 * touching the state.
 *
 * Protocol (fixed field order, '|' separator, '-' = N/A where noted):
 *   S1|cpu|ram_used_mb|ram_total_mb|ram_pressure|net_up|net_down|
 *      disk_free_gb|disk_total_gb|temp_c|thermal_state
 *   S2|<the 10 S1 fields>|net_iface
 *   S3|cpu|ram_used_mb|ram_free_mb|ram_pressure|net_up|net_down|
 *      disk_used_gb|disk_free_gb|temp_c|thermal|net_iface
 * The state stores used/free pairs. S3 carries them natively; for S1/S2
 * the wire carries used+TOTAL (ram) and free+TOTAL (disk), converted
 * after parse: ram_free = total - used, disk_used = total - free (both
 * clamped at 0). net_*, disk_* and temp_c carry one decimal digit and
 * are parsed into x10 integers without floating point. net_iface is a
 * 1..7 char token of [A-Z0-9-] ("WI-FI", "ETH", ...); plain "-" =
 * unknown. S1 lines imply net_iface = "-".
 *
 * SPDX-License-Identifier: MIT
 */

#include "sysmon_uart.h"
#include "sysmon_log.h"
#include "sysmon_state.h"

#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/ring_buffer.h>

LOG_MODULE_REGISTER(sysmon, SYSMON_LOG_LEVEL);

#define SYSMON_LINE_MAX 256
#define SYSMON_RX_RING_SIZE 512

static const struct device *const sysmon_dev = DEVICE_DT_GET(DT_NODELABEL(sysmon_uart));

RING_BUF_DECLARE(sysmon_rx_ring, SYSMON_RX_RING_SIZE);

/* Line assembly state — only touched from the work handler. */
static char line_buf[SYSMON_LINE_MAX + 1];
static size_t line_len;
static bool line_overflow;

/*
 * Parse an unsigned decimal integer; the whole field must be digits.
 */
static bool parse_u32(const char *s, uint32_t *out) {
    if (*s < '0' || *s > '9') {
        return false;
    }

    uint32_t v = 0;

    for (; *s != '\0'; s++) {
        if (*s < '0' || *s > '9') {
            return false;
        }
        uint32_t digit = (uint32_t)(*s - '0');
        if (v > (UINT32_MAX - digit) / 10U) {
            return false;
        }
        v = v * 10U + digit;
    }

    *out = v;
    return true;
}

/*
 * Parse "<int>" or "<int>.<d>" (exactly one decimal digit) into value*10,
 * without floating point.
 */
static bool parse_u32_x10(const char *s, uint32_t *out) {
    if (*s < '0' || *s > '9') {
        return false;
    }

    uint32_t whole = 0;

    while (*s >= '0' && *s <= '9') {
        uint32_t digit = (uint32_t)(*s - '0');
        if (whole > (UINT32_MAX - digit) / 10U) {
            return false;
        }
        whole = whole * 10U + digit;
        s++;
    }

    uint32_t frac = 0;

    if (*s == '.') {
        s++;
        if (*s < '0' || *s > '9') {
            return false;
        }
        frac = (uint32_t)(*s - '0');
        s++;
    }

    if (*s != '\0') {
        return false;
    }

    if (whole > (UINT32_MAX - frac) / 10U) {
        return false;
    }

    *out = whole * 10U + frac;
    return true;
}

/*
 * Return the current '|'-separated field, NUL-terminating it in place, and
 * advance *cursor past the separator (NULL after the last field).
 */
static char *next_field(char **cursor) {
    char *start = *cursor;

    if (start == NULL) {
        return NULL;
    }

    char *sep = strchr(start, '|');

    if (sep != NULL) {
        *sep = '\0';
        *cursor = sep + 1;
    } else {
        *cursor = NULL;
    }

    return start;
}

/* Validate a net_iface token: 1..7 chars, each of [A-Z0-9-]. */
static bool valid_iface_token(const char *s) {
    size_t len = strlen(s);

    if (len == 0 || len >= SYSMON_NET_IFACE_LEN) {
        return false;
    }

    for (; *s != '\0'; s++) {
        char c = *s;

        if ((c < 'A' || c > 'Z') && (c < '0' || c > '9') && c != '-') {
            return false;
        }
    }

    return true;
}

/*
 * Parse the fields following the "S1|"/"S2|"/"S3|" prefix into *st
 * (version 2 and 3 carry one extra trailing net_iface field). The RAM
 * and DISK pairs are parsed as raw a|b values and interpreted per
 * version: S1/S2 = used|total (ram) and free|total (disk), converted to
 * used/free; S3 = used|free natively. Returns false on any malformed,
 * missing or extra field; *st is then not to be used.
 */
static bool parse_metric_fields(char *fields, struct sysmon_state *st, uint8_t version) {
    bool with_iface = version >= 2U;
    char *cursor = fields;
    char *f;
    uint32_t v;

    memset(st, 0, sizeof(*st));

    /* cpu_total: integer 0..100 */
    f = next_field(&cursor);
    if (f == NULL || !parse_u32(f, &v) || v > 100U) {
        return false;
    }
    st->cpu_total = (uint8_t)v;

    /* RAM pair: integers; S1/S2 = used|total, S3 = used|free */
    uint32_t ram_a, ram_b;

    f = next_field(&cursor);
    if (f == NULL || !parse_u32(f, &ram_a)) {
        return false;
    }
    f = next_field(&cursor);
    if (f == NULL || !parse_u32(f, &ram_b)) {
        return false;
    }
    st->ram_used_mb = ram_a;
    if (version < 3U) {
        /* ram_free = total - used, clamped at 0 */
        st->ram_free_mb = (ram_b > ram_a) ? (ram_b - ram_a) : 0U;
    } else {
        st->ram_free_mb = ram_b;
    }

    /* ram_pressure: integer 0..100 or '-' */
    f = next_field(&cursor);
    if (f == NULL) {
        return false;
    }
    if (strcmp(f, "-") == 0) {
        st->ram_pressure = SYSMON_PRESSURE_NA;
    } else if (parse_u32(f, &v) && v <= 100U) {
        st->ram_pressure = (int16_t)v;
    } else {
        return false;
    }

    /* net_up_kbps, net_down_kbps: one decimal, stored x10 */
    f = next_field(&cursor);
    if (f == NULL || !parse_u32_x10(f, &st->net_up_kbps_x10)) {
        return false;
    }
    f = next_field(&cursor);
    if (f == NULL || !parse_u32_x10(f, &st->net_down_kbps_x10)) {
        return false;
    }

    /* DISK pair: one decimal, stored x10; S1/S2 = free|total, S3 = used|free */
    uint32_t disk_a, disk_b;

    f = next_field(&cursor);
    if (f == NULL || !parse_u32_x10(f, &disk_a)) {
        return false;
    }
    f = next_field(&cursor);
    if (f == NULL || !parse_u32_x10(f, &disk_b)) {
        return false;
    }
    if (version < 3U) {
        /* disk_used = total - free, clamped at 0 */
        st->disk_free_gb_x10 = disk_a;
        st->disk_used_gb_x10 = (disk_b > disk_a) ? (disk_b - disk_a) : 0U;
    } else {
        st->disk_used_gb_x10 = disk_a;
        st->disk_free_gb_x10 = disk_b;
    }

    /* temp_c: one decimal (optional sign), stored x10; '-' = N/A */
    f = next_field(&cursor);
    if (f == NULL) {
        return false;
    }
    if (strcmp(f, "-") == 0) {
        st->temp_c_x10 = SYSMON_TEMP_NA;
    } else {
        const char *p = f;
        bool neg = false;

        if (*p == '-') {
            neg = true;
            p++;
        }
        if (!parse_u32_x10(p, &v) || v > (uint32_t)INT16_MAX) {
            return false;
        }
        st->temp_c_x10 = neg ? (int16_t)-(int32_t)v : (int16_t)v;
    }

    /* thermal_state: short token, '-' = N/A */
    f = next_field(&cursor);
    if (f == NULL || f[0] == '\0' || strlen(f) >= sizeof(st->thermal)) {
        return false;
    }
    strcpy(st->thermal, f);

    /* net_iface (S2/S3 only): 1..7 chars of [A-Z0-9-], '-' = unknown */
    if (with_iface) {
        f = next_field(&cursor);
        if (f == NULL || !valid_iface_token(f)) {
            return false;
        }
        strcpy(st->net_iface, f);
    } else {
        strcpy(st->net_iface, "-");
    }

    /* Fixed field order and count: reject trailing extra fields. */
    if (cursor != NULL) {
        return false;
    }

    st->valid = true;
    return true;
}

/* Work context only — uart_poll_out may busy-wait, never call from ISR. */
static void send_str(const char *s) {
    while (*s != '\0') {
        uart_poll_out(sysmon_dev, *s++);
    }
}

static void process_line(char *line) {
    if (strcmp(line, SYSMON_UART_PING) == 0) {
        send_str(SYSMON_UART_HELLO);
        return;
    }

    if (line[0] == 'S' && line[1] >= '1' && line[1] <= '3' && line[2] == '|') {
        struct sysmon_state st;
        uint8_t version = (uint8_t)(line[1] - '0');

        if (parse_metric_fields(line + 3, &st, version)) {
            sysmon_state_set(&st);
        } else {
            LOG_DBG("dropped malformed S%c line", line[1]);
        }
        return;
    }

    /* Unknown lines are silently ignored. */
}

static void rx_work_handler(struct k_work *work) {
    ARG_UNUSED(work);

    uint8_t byte;

    while (ring_buf_get(&sysmon_rx_ring, &byte, 1) == 1U) {
        if (byte == '\n') {
            if (!line_overflow && line_len > 0) {
                if (line_buf[line_len - 1] == '\r') {
                    line_len--;
                }
                line_buf[line_len] = '\0';
                if (line_len > 0) {
                    process_line(line_buf);
                }
            }
            line_len = 0;
            line_overflow = false;
        } else if (line_overflow) {
            /* Discard until the next LF. */
        } else if (line_len >= SYSMON_LINE_MAX) {
            LOG_DBG("line too long, discarding until next LF");
            line_len = 0;
            line_overflow = true;
        } else {
            line_buf[line_len++] = (char)byte;
        }
    }
}

static K_WORK_DEFINE(rx_work, rx_work_handler);

static void sysmon_uart_isr(const struct device *dev, void *user_data) {
    ARG_UNUSED(user_data);

    while (uart_irq_update(dev) && uart_irq_rx_ready(dev)) {
        uint8_t buf[32];
        int len = uart_fifo_read(dev, buf, sizeof(buf));

        if (len <= 0) {
            break;
        }

        /* On overflow bytes are lost; the resulting partial line fails
         * parsing and is dropped, so no special handling is needed. */
        ring_buf_put(&sysmon_rx_ring, buf, (uint32_t)len);
        k_work_submit(&rx_work);
    }
}

static int sysmon_uart_init(void) {
    if (!device_is_ready(sysmon_dev)) {
        /* Keep booting: the display keeps running (showing NO DATA)
         * even without the sysmon link. */
        LOG_WRN("sysmon UART not ready, system monitor RX disabled");
        return 0;
    }

    int err = uart_irq_callback_user_data_set(sysmon_dev, sysmon_uart_isr, NULL);

    if (err != 0) {
        LOG_WRN("sysmon UART callback setup failed (%d), RX disabled", err);
        return 0;
    }

    uart_irq_rx_enable(sysmon_dev);
    LOG_INF("sysmon UART RX enabled");
    return 0;
}

SYS_INIT(sysmon_uart_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
