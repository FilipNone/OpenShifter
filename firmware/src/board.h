/* OpenShifter: clocks, pins, time base, reset. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFTER_BOARD_H
#define OPENSHIFTER_BOARD_H

#include <stdint.h>

extern volatile uint32_t g_ms;     /* SysTick, 1 kHz */
extern uint32_t g_sysclk_hz;       /* 72 MHz (HSE) or 48 MHz (HSI fallback) */
extern uint32_t g_pclk1_hz, g_pclk2_hz;

/* 0 on success, -1 if the PLL could not be set up (caller falls back to the
 * vendor bootloader, which configures its own clock). */
int  clock_init(void);
void board_pins_init(void);
void systick_init(void);
void delay_ms(uint32_t ms);

/* Stock calibrates its SysTick delay for SystemCoreClock = 72 MHz but runs
 * the AIR32 PLL at 216 MHz, so its delay_ms(n) lasts n/3 ms on the unit.
 * OpenShifter keeps those effective timings. */
#define STOCK_MS(n) (((n) + 2u) / 3u)
void delay_cycles(uint32_t n);
__attribute__((noreturn)) void board_reset(void);

/* Inputs read low when pressed / switch in H position. */
static inline int pin_pb10_low(void);
static inline int pin_pa5_low(void);
static inline int pin_pa4_low(void);

#include "regs.h"
static inline int pin_pb10_low(void) { return !(GPIO_IDR(GPIOB) & (1u << 10)); }
static inline int pin_pa5_low(void)  { return !(GPIO_IDR(GPIOA) & (1u << 5)); }
static inline int pin_pa4_low(void)  { return !(GPIO_IDR(GPIOA) & (1u << 4)); }

void gpio_mode(uint32_t port, unsigned pin, uint32_t nibble);

static inline void irq_disable(void) { __asm volatile("cpsid i" ::: "memory"); }
static inline void irq_enable(void)  { __asm volatile("cpsie i" ::: "memory"); }

#endif
