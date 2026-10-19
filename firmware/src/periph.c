/* OpenShift: LED chain and UART link. SPDX-License-Identifier: MIT */
#include "periph.h"
#include "board.h"
#include "regs.h"

#define LED_COUNT  3
#define LED_RESET  96   /* trailing low periods, >= 50 us */
#define LED_DMA_CH 3
#define UART_DMA_CH 4

static uint8_t led_buf[1 + LED_COUNT * 24 + LED_RESET];
static uint8_t led_one, led_zero;

void leds_init(void)
{
    RCC_APB1ENR |= APB1_TIM3;
    RCC_AHBENR |= AHB_DMA1;
    RCC_APB2ENR |= APB2_IOPB;

    /* 800 kHz bit clock from the TIM3 kernel clock (2 x PCLK1 when APB1 is
     * divided). Stock at 72 MHz-equivalent: period 90, '1' 58, '0' 25. */
    uint32_t tclk = g_pclk1_hz == g_sysclk_hz ? g_pclk1_hz : 2 * g_pclk1_hz;
    uint32_t period = tclk / 800000;
    led_one = (uint8_t)(period * 58 / 90);
    led_zero = (uint8_t)(period * 25 / 90);

    TIM3_CR1 = 0;
    TIM3_PSC = 0;
    TIM3_ARR = period - 1;
    TIM3_CCR3 = 0;
    TIM3_CCMR2 = (6u << 4) | (1u << 3);   /* OC3: PWM mode 1, preload */
    TIM3_CCER = 1u << 8;                  /* CC3E */
    TIM3_DIER = 1u << 8;                  /* UDE: DMA request on update */
    TIM3_EGR = 1;
    TIM3_CR1 = (1u << 7) | 1u;            /* ARPE, CEN */

    DMA_CCR(LED_DMA_CH) = 0;
    DMA_CPAR(LED_DMA_CH) = TIM3_CCR3_ADDR;
    gpio_mode(GPIOB, 0, GPIO_AF_PP_50);
}

int leds_busy(void)
{
    return (DMA_CCR(LED_DMA_CH) & DMA_CCR_EN) && DMA_CNDTR(LED_DMA_CH) != 0;
}

int leds_show(const uint8_t grb[9])
{
    if (leds_busy())
        return 0;
    unsigned n = 0;
    led_buf[n++] = 0;   /* line idles low for the first period */
    for (unsigned i = 0; i < LED_COUNT * 3; i++)
        for (int b = 7; b >= 0; b--)
            led_buf[n++] = (grb[i] >> b) & 1 ? led_one : led_zero;
    for (unsigned i = 0; i < LED_RESET; i++)
        led_buf[n++] = 0;

    DMA_CCR(LED_DMA_CH) = 0;
    DMA1_IFCR = 0xFu << (4 * (LED_DMA_CH - 1));
    DMA_CMAR(LED_DMA_CH) = (uint32_t)led_buf;
    DMA_CNDTR(LED_DMA_CH) = n;
    /* memory byte -> peripheral halfword, memory increment */
    DMA_CCR(LED_DMA_CH) = DMA_CCR_DIR | DMA_CCR_MINC | DMA_CCR_PSIZE16 | DMA_CCR_EN;
    return 1;
}

static uint8_t uart_buf[64];
static uint8_t uart_on;

void uart_enable(void)
{
    if (uart_on)
        return;
    RCC_APB2ENR |= APB2_USART1 | APB2_IOPA;
    RCC_AHBENR |= AHB_DMA1;
    USART1_CR1 = 0;
    USART1_BRR = (g_pclk2_hz + 921600 / 2) / 921600;
    USART1_CR3 = 1u << 7;                 /* DMAT */
    USART1_CR1 = (1u << 13) | (1u << 3);  /* UE, TE */
    DMA_CCR(UART_DMA_CH) = 0;
    DMA_CPAR(UART_DMA_CH) = USART1_DR_ADDR;
    gpio_mode(GPIOA, 9, GPIO_AF_PP_50);
    gpio_mode(GPIOA, 10, GPIO_IN_FLOAT);
    uart_on = 1;
}

void uart_disable(void)
{
    if (!uart_on)
        return;
    DMA_CCR(UART_DMA_CH) = 0;
    USART1_CR1 = 0;
    gpio_mode(GPIOA, 9, GPIO_IN_FLOAT);
    gpio_mode(GPIOA, 10, GPIO_IN_FLOAT);
    uart_on = 0;
}

int uart_send(const uint8_t *p, unsigned n)
{
    if (!uart_on || n > sizeof uart_buf)
        return 0;
    if ((DMA_CCR(UART_DMA_CH) & DMA_CCR_EN) && DMA_CNDTR(UART_DMA_CH) != 0)
        return 0;
    for (unsigned i = 0; i < n; i++)
        uart_buf[i] = p[i];
    DMA_CCR(UART_DMA_CH) = 0;
    DMA1_IFCR = 0xFu << (4 * (UART_DMA_CH - 1));
    DMA_CMAR(UART_DMA_CH) = (uint32_t)uart_buf;
    DMA_CNDTR(UART_DMA_CH) = n;
    DMA_CCR(UART_DMA_CH) = DMA_CCR_DIR | DMA_CCR_MINC | DMA_CCR_EN;
    return 1;
}
