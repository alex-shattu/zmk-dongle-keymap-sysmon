/*
 * SPDX-License-Identifier: MIT
 */

#include "sysmon_state.h"

#include <zephyr/kernel.h>
#include <zephyr/spinlock.h>

static struct sysmon_state state = {
    .valid = false,
    .ram_pressure = SYSMON_PRESSURE_NA,
    .temp_c_x10 = SYSMON_TEMP_NA,
    .thermal = "-",
    .net_iface = "-",
};

static struct k_spinlock state_lock;

void sysmon_state_set(const struct sysmon_state *st) {
    k_spinlock_key_t key = k_spin_lock(&state_lock);
    state = *st;
    state.last_rx_ms = k_uptime_get();
    k_spin_unlock(&state_lock, key);
}

void sysmon_state_get(struct sysmon_state *out) {
    k_spinlock_key_t key = k_spin_lock(&state_lock);
    *out = state;
    k_spin_unlock(&state_lock, key);
}
