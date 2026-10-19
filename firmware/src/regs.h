/* OpenShift: register map (STM32F103-compatible AIR32F103). SPDX-License-Identifier: MIT */
#ifndef OPENSHIFT_REGS_H
#define OPENSHIFT_REGS_H

#include <stdint.h>

#define REG32(a) (*(volatile uint32_t *)(a))
#define REG16(a) (*(volatile uint16_t *)(a))

/* Cortex-M3 system */
#define SYST_CSR   REG32(0xE000E010)
#define SYST_RVR   REG32(0xE000E014)
#define SYST_CVR   REG32(0xE000E018)
#define SCB_ICSR   REG32(0xE000ED04)
#define SCB_VTOR   REG32(0xE000ED08)
#define SCB_AIRCR  REG32(0xE000ED0C)
#define SCB_SHPR3  REG32(0xE000ED20)
#define SCB_SHCSR  REG32(0xE000ED24)
#define NVIC_ISER(n) REG32(0xE000E100 + 4 * (n))
#define NVIC_ICER(n) REG32(0xE000E180 + 4 * (n))
#define NVIC_ICPR(n) REG32(0xE000E280 + 4 * (n))
#define NVIC_IPR8(n) (*(volatile uint8_t *)(0xE000E400 + (n)))
#define ICSR_PENDSVSET (1u << 28)
#define AIRCR_RESET    0x05FA0004u

/* RCC */
#define RCC_CR       REG32(0x40021000)
#define RCC_CFGR     REG32(0x40021004)
#define RCC_CIR      REG32(0x40021008)
#define RCC_APB2RSTR REG32(0x4002100C)
#define RCC_APB1RSTR REG32(0x40021010)
#define RCC_AHBENR   REG32(0x40021014)
#define RCC_APB2ENR  REG32(0x40021018)
#define RCC_APB1ENR  REG32(0x4002101C)
#define RCC_CSR      REG32(0x40021024)
#define RCC_CR_HSION   (1u << 0)
#define RCC_CR_HSIRDY  (1u << 1)
#define RCC_CR_HSEON   (1u << 16)
#define RCC_CR_HSERDY  (1u << 17)
#define RCC_CR_HSEBYP  (1u << 18)
#define RCC_CR_PLLON   (1u << 24)
#define RCC_CR_PLLRDY  (1u << 25)
#define RCC_CSR_RMVF   (1u << 24)
#define RCC_CSR_PORRSTF (1u << 27)
#define APB2_AFIO   (1u << 0)
#define APB2_IOPA   (1u << 2)
#define APB2_IOPB   (1u << 3)
#define APB2_USART1 (1u << 14)
#define APB1_TIM3   (1u << 1)
#define APB1_USB    (1u << 23)
#define APB1_PWR    (1u << 28)
#define AHB_DMA1    (1u << 0)

/* Flash interface */
#define FLASH_ACR  REG32(0x40022000)
#define FLASH_KEYR REG32(0x40022004)
#define FLASH_SR   REG32(0x4002200C)
#define FLASH_CR   REG32(0x40022010)
#define FLASH_AR   REG32(0x40022014)
#define FLASH_SR_BSY      (1u << 0)
#define FLASH_SR_PGERR    (1u << 2)
#define FLASH_SR_WRPRTERR (1u << 4)
#define FLASH_SR_EOP      (1u << 5)
#define FLASH_CR_PG   (1u << 0)
#define FLASH_CR_PER  (1u << 1)
#define FLASH_CR_STRT (1u << 6)
#define FLASH_CR_LOCK (1u << 7)

/* GPIO */
#define GPIOA 0x40010800u
#define GPIOB 0x40010C00u
#define GPIO_CRL(p)  REG32((p) + 0x00)
#define GPIO_CRH(p)  REG32((p) + 0x04)
#define GPIO_IDR(p)  REG32((p) + 0x08)
#define GPIO_ODR(p)  REG32((p) + 0x0C)
#define GPIO_BSRR(p) REG32((p) + 0x10)
#define GPIO_BRR(p)  REG32((p) + 0x14)
/* CNF/MODE nibbles */
#define GPIO_IN_ANALOG  0x0u
#define GPIO_IN_FLOAT   0x4u
#define GPIO_IN_PULL    0x8u   /* pull direction from ODR */
#define GPIO_OUT_PP_2   0x2u
#define GPIO_OUT_OD_2   0x6u
#define GPIO_OUT_PP_50  0x3u
#define GPIO_AF_PP_50   0xBu

/* EXTI */
#define EXTI_IMR REG32(0x40010400)
#define EXTI_EMR REG32(0x40010404)
#define EXTI_PR  REG32(0x40010414)

/* USART1 */
#define USART1_SR  REG32(0x40013800)
#define USART1_DR_ADDR 0x40013804u
#define USART1_BRR REG32(0x40013808)
#define USART1_CR1 REG32(0x4001380C)
#define USART1_CR3 REG32(0x40013814)

/* DMA1, channels 1..7 */
#define DMA1_ISR   REG32(0x40020000)
#define DMA1_IFCR  REG32(0x40020004)
#define DMA_CCR(c)   REG32(0x40020008 + 20 * ((c) - 1))
#define DMA_CNDTR(c) REG32(0x4002000C + 20 * ((c) - 1))
#define DMA_CPAR(c)  REG32(0x40020010 + 20 * ((c) - 1))
#define DMA_CMAR(c)  REG32(0x40020014 + 20 * ((c) - 1))
#define DMA_CCR_EN    (1u << 0)
#define DMA_CCR_DIR   (1u << 4)   /* memory -> peripheral */
#define DMA_CCR_MINC  (1u << 7)
#define DMA_CCR_PSIZE16 (1u << 8)

/* TIM3 */
#define TIM3_CR1   REG32(0x40000400)
#define TIM3_DIER  REG32(0x4000040C)
#define TIM3_SR    REG32(0x40000410)
#define TIM3_EGR   REG32(0x40000414)
#define TIM3_CCMR2 REG32(0x4000041C)
#define TIM3_CCER  REG32(0x40000420)
#define TIM3_PSC   REG32(0x40000428)
#define TIM3_ARR   REG32(0x4000042C)
#define TIM3_CCR3_ADDR 0x4000043Cu
#define TIM3_CCR3  REG32(TIM3_CCR3_ADDR)

/* USB full-speed device */
#define USB_EPR(n)  REG32(0x40005C00 + 4 * (n))
#define USB_CNTR    REG32(0x40005C40)
#define USB_ISTR    REG32(0x40005C44)
#define USB_FNR     REG32(0x40005C48)
#define USB_DADDR   REG32(0x40005C4C)
#define USB_BTABLE  REG32(0x40005C50)
#define USB_AIR_DPPU REG32(0x40005C54)  /* AIR32 internal D+ pull-up */
#define USB_PMA_BASE 0x40006000u

#define USB_IRQ_LP 20

#endif
