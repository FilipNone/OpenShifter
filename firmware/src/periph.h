/* OpenShifter: LED chain and UART link. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFTER_PERIPH_H
#define OPENSHIFTER_PERIPH_H

#include <stdint.h>

/* Three WS2812 LEDs on PB0 (TIM3_CH3 PWM, DMA1 channel 3 on update). */
void leds_init(void);
/* Starts sending 9 bytes in wire order (G,R,B per LED). Returns 0 if the
 * previous frame is still being sent (try again later). */
int  leds_show(const uint8_t grb[9]);
int  leds_busy(void);

/* USART1 TX on PA9, 921600 8N1, DMA1 channel 4. Used for the wired link
 * while USB is not configured, as stock. */
void uart_enable(void);
void uart_disable(void);
int  uart_send(const uint8_t *p, unsigned n);   /* 0 if busy */

#endif
