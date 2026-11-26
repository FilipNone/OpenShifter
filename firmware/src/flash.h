/* OpenShifter: settings page and bootloader hand-off. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFTER_FLASH_H
#define OPENSHIFTER_FLASH_H

#include <stdint.h>
#include "shifter.h"

#define SETTINGS_ADDR 0x0800F000u   /* shared with the vendor bootloader */
#define SETTINGS_BOOT_FLAG 0        /* halfword 0: 1 = stay in bootloader */

void settings_read(uint16_t st[SH_SETTINGS_HALFWORDS]);
/* Read-modify-write of the settings page; only the first
 * SH_SETTINGS_HALFWORDS halfwords change. Returns 0 if nothing changed (no
 * erase), 1 after a good write, -1 on a flash error. */
int  settings_write(const uint16_t st[SH_SETTINGS_HALFWORDS]);

/* Set the bootloader flag and reset. The vendor bootloader then stays in
 * update mode (USB 10f5:7097) until a new image is written. Safe to call from
 * any context, including fault handlers. If the flag cannot be written after
 * one retry, the unit leaves USB and blinks red instead of resetting. */
__attribute__((noreturn)) void enter_bootloader(void);

#endif
