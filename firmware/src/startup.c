/* OpenShift: vector table and reset. SPDX-License-Identifier: MIT
 *
 * The vendor bootloader jumps here with its own peripherals, clocks and
 * interrupts possibly still live. Everything it may have left running is
 * stopped before main().
 */
#include <stdint.h>
#include "regs.h"
#include "flash.h"

extern uint32_t _stack_top, _data_start, _data_end, _data_load, _bss_start, _bss_end;
extern int main(void);
void systick_handler(void);
void pendsv_handler(void);
void usb_lp_handler(void);
void reset_handler(void);
void reset_c(void);

/* Faults: drop into the vendor bootloader so the unit can be re-flashed
 * (stock or a fixed image) over USB. */
static void fault_handler(void)
{
    enter_bootloader();
}

/* Unexpected interrupt: mask it and carry on. */
static void default_handler(void)
{
    uint32_t ipsr;
    __asm volatile("mrs %0, ipsr" : "=r"(ipsr));
    if (ipsr >= 16) {
        uint32_t n = ipsr - 16;
        NVIC_ICER(n / 32) = 1u << (n % 32);
        NVIC_ICPR(n / 32) = 1u << (n % 32);
    }
}

#define N_IRQ 68

__attribute__((section(".vectors"), used))
void (*const vectors[16 + N_IRQ])(void) = {
    [0] = (void (*)(void))&_stack_top,
    [1] = reset_handler,
    [2] = fault_handler,      /* NMI */
    [3] = fault_handler,      /* HardFault */
    [4] = fault_handler,      /* MemManage */
    [5] = fault_handler,      /* BusFault */
    [6] = fault_handler,      /* UsageFault */
    [11] = default_handler,   /* SVCall */
    [12] = default_handler,   /* DebugMon */
    [14] = pendsv_handler,
    [15] = systick_handler,
    [16 + 0 ... 16 + N_IRQ - 1] = default_handler,
    [16 + USB_IRQ_LP] = usb_lp_handler,
};

__attribute__((naked)) void reset_handler(void)
{
    __asm volatile(
        "cpsid i\n"
        "ldr r0, =_stack_top\n"
        "msr msp, r0\n"
        "movs r0, #0\n"
        "msr control, r0\n"   /* privileged, MSP */
        "isb\n"
        "b reset_c\n");
}

void reset_c(void)
{
    SCB_VTOR = (uint32_t)vectors;

    /* Stop what the bootloader may have started. */
    SYST_CSR = 0;
    for (int i = 0; i < 3; i++) {
        NVIC_ICER(i) = 0xFFFFFFFFu;
        NVIC_ICPR(i) = 0xFFFFFFFFu;
    }
    SCB_ICSR = (1u << 27) | (1u << 25);   /* PENDSVCLR, PENDSTCLR */
    for (int c = 1; c <= 7; c++)
        DMA_CCR(c) = 0;
    EXTI_IMR = 0;
    EXTI_EMR = 0;
    EXTI_PR = 0xFFFFFu;
    RCC_APB1RSTR = APB1_USB | APB1_TIM3;
    RCC_APB2RSTR = APB2_USART1;
    RCC_APB1RSTR = 0;
    RCC_APB2RSTR = 0;

    uint32_t *src = &_data_load, *dst = &_data_start;
    while (dst < &_data_end)
        *dst++ = *src++;
    for (dst = &_bss_start; dst < &_bss_end;)
        *dst++ = 0;

    /* Fault exceptions on their own vectors, all routed to the same path. */
    SCB_SHCSR |= (1u << 16) | (1u << 17) | (1u << 18);
    __asm volatile("dsb\nisb" ::: "memory");
    main();
    enter_bootloader();
}
