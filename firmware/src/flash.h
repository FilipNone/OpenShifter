/* OpenShift: settings page and bootloader hand-off. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFT_FLASH_H
#define OPENSHIFT_FLASH_H

#include <stdint.h>
#include "shifter.h"

#define SETTINGS_ADDR 0x0800F000u   /* shared with the vendor bootloader */
#define SETTINGS_BOOT_FLAG 0        /* halfword 0: 1 = stay in bootloader */

void settings_read(uint16_t st[SH_SETTINGS_HALFWORDS]);
/* Read-modify-write of the settings page; only the first
 * SH_SETTINGS_HALFWORDS halfwords change. Returns 0 on success (or nothing
 * to do), -1 on a flash error. */
int  settings_write(const uint16_t st[SH_SETTINGS_HALFWORDS]);

/* Set the bootloader flag and reset. The vendor bootloader then stays in
 * update mode (USB 10f5:7097) until a new image is written. Safe to call from
 * any context, including fault handlers. */
__attribute__((noreturn)) void enter_bootloader(void);

#endif
