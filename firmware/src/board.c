/* OpenShift: clocks, pins, time base, reset. SPDX-License-Identifier: MIT */
#include "board.h"

volatile uint32_t g_ms;
uint32_t g_sysclk_hz = 8000000, g_pclk1_hz = 8000000, g_pclk2_hz = 8000000;

void delay_cycles(uint32_t n)
{
    /* ~3 cycles per iteration on the M3 */
    for (n = (n + 2) / 3; n; n--)
        __asm volatile("" ::: "memory");
}

void delay_ms(uint32_t ms)
{
    uint32_t t0 = g_ms;
    while (g_ms - t0 < ms)
        ;
}

static int wait_bits(volatile uint32_t *reg, uint32_t mask, uint32_t want, uint32_t tries)
{
    while (tries--)
        if ((*reg & mask) == want)
            return 0;
    return -1;
}

#define CFGR_SW_MASK   0x3u
#define CFGR_SWS_MASK  0xCu
#define CFGR_PPRE1_DIV2 (4u << 8)
#define CFGR_PLLSRC    (1u << 16)
#define CFGR_PLLMUL(n) ((uint32_t)((n) - 2) << 18)
#define CFGR_USBPRE_1  (1u << 22)
/* PLL and USB prescaler fields, including the AIR32 extensions (bit 28 = 5th
 * PLLMUL bit, bits 23/31 = extra USBPRE bits) that the bootloader or the stock
 * 216 MHz setup may leave set. All must read back as written. */
#define CFGR_CHECK_MASK (0x103F0000u | (1u << 22) | (1u << 23) | (1u << 31) | CFGR_SWS_MASK)

int clock_init(void)
{
    /* Whatever the bootloader left running: back to HSI, PLL off. */
    RCC_CR |= RCC_CR_HSION;
    if (wait_bits(&RCC_CR, RCC_CR_HSIRDY, RCC_CR_HSIRDY, 100000))
        return -1;
    RCC_CFGR &= ~CFGR_SW_MASK;
    if (wait_bits(&RCC_CFGR, CFGR_SWS_MASK, 0, 100000))
        return -1;
    RCC_CR &= ~RCC_CR_PLLON;
    if (wait_bits(&RCC_CR, RCC_CR_PLLRDY, 0, 100000))
        return -1;
    RCC_CIR = 0x009F0000;   /* clock interrupts off, flags cleared */

    /* Flash: 2 wait states + prefetch, valid up to 72 MHz. */
    FLASH_ACR = (FLASH_ACR & ~0x7u) | 0x2u | 0x10u;

    uint32_t cfgr;
    RCC_CR = (RCC_CR & ~RCC_CR_HSEBYP) | RCC_CR_HSEON;
    if (!wait_bits(&RCC_CR, RCC_CR_HSERDY, RCC_CR_HSERDY, 200000)) {
        /* 8 MHz crystal x 9 = 72 MHz, USB 72 / 1.5 = 48 MHz, APB1 36 MHz. */
        cfgr = CFGR_PLLSRC | CFGR_PLLMUL(9) | CFGR_PPRE1_DIV2;
        g_sysclk_hz = 72000000;
    } else {
        /* No crystal: HSI/2 x 12 = 48 MHz, USB /1. Not crystal-accurate but
         * keeps the device reachable. */
        RCC_CR &= ~RCC_CR_HSEON;
        cfgr = CFGR_PLLMUL(12) | CFGR_USBPRE_1 | CFGR_PPRE1_DIV2;
        g_sysclk_hz = 48000000;
    }
    RCC_CFGR = cfgr;   /* SW = HSI for now */
    RCC_CR |= RCC_CR_PLLON;
    if (wait_bits(&RCC_CR, RCC_CR_PLLRDY, RCC_CR_PLLRDY, 200000))
        return -1;
    RCC_CFGR = cfgr | 2u;   /* SYSCLK = PLL */
    if (wait_bits(&RCC_CFGR, CFGR_SWS_MASK, 2u << 2, 100000))
        return -1;
    if ((RCC_CFGR & CFGR_CHECK_MASK) != ((cfgr | (2u << 2)) & CFGR_CHECK_MASK))
        return -1;
    g_pclk1_hz = g_sysclk_hz / 2;
    g_pclk2_hz = g_sysclk_hz;
    return 0;
}

void gpio_mode(uint32_t port, unsigned pin, uint32_t nibble)
{
    volatile uint32_t *cr = pin < 8 ? &GPIO_CRL(port) : &GPIO_CRH(port);
    unsigned sh = (pin & 7) * 4;
    *cr = (*cr & ~(0xFu << sh)) | (nibble << sh);
}

void board_pins_init(void)
{
    RCC_APB2ENR |= APB2_AFIO | APB2_IOPA | APB2_IOPB;
    /* Buttons and H/SEQ switch: inputs with pull-up (stock 0x0b14). */
    GPIO_BSRR(GPIOA) = (1u << 4) | (1u << 5);
    gpio_mode(GPIOA, 4, GPIO_IN_PULL);
    gpio_mode(GPIOA, 5, GPIO_IN_PULL);
    GPIO_BSRR(GPIOB) = 1u << 10;
    gpio_mode(GPIOB, 10, GPIO_IN_PULL);
}

void systick_init(void)
{
    SYST_CSR = 0;
    SYST_RVR = g_sysclk_hz / 1000 - 1;
    SYST_CVR = 0;
    SYST_CSR = 7;   /* processor clock, interrupt, enable */
}

void board_reset(void)
{
    __asm volatile("dsb" ::: "memory");
    SCB_AIRCR = AIRCR_RESET;
    __asm volatile("dsb" ::: "memory");
    for (;;)
        ;
}
