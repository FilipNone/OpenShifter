/* OpenShift: settings page and bootloader hand-off. SPDX-License-Identifier: MIT
 *
 * The page size of this part is not known for certain (stock assumes 2 KB).
 * The write path works for either: the whole 2 KB is copied, the page at
 * SETTINGS_ADDR erased, then only halfwords that now read 0xFFFF and should
 * not be are programmed. With 1 KB pages the second KB is never erased and
 * already holds the wanted data.
 */
#include "flash.h"
#include "board.h"
#include "regs.h"

#define PAGE_HALFWORDS 1024

static uint16_t page[PAGE_HALFWORDS];
static volatile uint8_t write_active;

static volatile uint16_t *flash_hw(unsigned i)
{
    return (volatile uint16_t *)(SETTINGS_ADDR + 2 * i);
}

void settings_read(uint16_t st[SH_SETTINGS_HALFWORDS])
{
    for (unsigned i = 0; i < SH_SETTINGS_HALFWORDS; i++)
        st[i] = *flash_hw(i);
}

static int fpec_wait(void)
{
    while (FLASH_SR & FLASH_SR_BSY)
        ;
    uint32_t sr = FLASH_SR;
    FLASH_SR = FLASH_SR_EOP | FLASH_SR_PGERR | FLASH_SR_WRPRTERR;
    return sr & (FLASH_SR_PGERR | FLASH_SR_WRPRTERR) ? -1 : 0;
}

static void fpec_unlock(void)
{
    if (FLASH_CR & FLASH_CR_LOCK) {
        FLASH_KEYR = 0x45670123u;
        FLASH_KEYR = 0xCDEF89ABu;
    }
}

/* Writes page[] (already holding the wanted 2 KB) to flash. */
static int write_page(void)
{
    int err = 0;
    RCC_CR |= RCC_CR_HSION;   /* the FPEC runs from HSI */
    while (!(RCC_CR & RCC_CR_HSIRDY))
        ;
    fpec_unlock();
    fpec_wait();
    FLASH_CR = FLASH_CR_PER;
    FLASH_AR = SETTINGS_ADDR;
    FLASH_CR = FLASH_CR_PER | FLASH_CR_STRT;
    err |= fpec_wait();
    FLASH_CR = 0;
    for (unsigned i = 0; i < PAGE_HALFWORDS; i++) {
        uint16_t cur = *flash_hw(i);
        if (cur == page[i])
            continue;
        if (cur != 0xFFFF) {
            err = -1;
            continue;
        }
        FLASH_CR = FLASH_CR_PG;
        *flash_hw(i) = page[i];
        err |= fpec_wait();
        FLASH_CR = 0;
    }
    FLASH_CR = FLASH_CR_LOCK;
    for (unsigned i = 0; i < PAGE_HALFWORDS; i++)
        if (*flash_hw(i) != page[i])
            err = -1;
    return err;
}

int settings_write(const uint16_t st[SH_SETTINGS_HALFWORDS])
{
    int changed = 0;
    for (unsigned i = 0; i < PAGE_HALFWORDS; i++)
        page[i] = *flash_hw(i);
    for (unsigned i = 0; i < SH_SETTINGS_HALFWORDS; i++) {
        changed |= page[i] != st[i];
        page[i] = st[i];
    }
    if (!changed)
        return 0;
    write_active = 1;
    int err = write_page();
    write_active = 0;
    return err;
}

void enter_bootloader(void)
{
    static volatile uint8_t entered;
    irq_disable();
    if (entered)
        board_reset();   /* faulted inside this path: plain reset */
    entered = 1;

    /* If a settings write was interrupted, page[] holds the intended data;
     * otherwise start from what is in flash. */
    while (FLASH_SR & FLASH_SR_BSY)
        ;
    FLASH_CR = 0;
    if (!write_active)
        for (unsigned i = 0; i < PAGE_HALFWORDS; i++)
            page[i] = *flash_hw(i);
    page[SETTINGS_BOOT_FLAG] = 1;
    write_page();
    board_reset();
}
