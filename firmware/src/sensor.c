/* OpenShift: 3-axis magnetometer on a bit-banged SPI bus. SPDX-License-Identifier: MIT
 *
 * Pins: CS PB12, SCK PB13, MISO PB14 (pull-up), MOSI PB15, sensor power PA7
 * (open drain, low = on). SPI mode 3, MSB first, as stock 0x2daa..0x2e3c.
 * The command set is MLX90393-style (EX 0x80, WR 0x60, RR 0x50, SB 0x1x,
 * RM 0x4x).
 */
#include "sensor.h"
#include "board.h"
#include "regs.h"

#define CS   12
#define SCK  13
#define MISO 14
#define MOSI 15
#define PWR  7

static inline void pin(unsigned p, int v)
{
    GPIO_BSRR(GPIOB) = v ? 1u << p : 1u << (p + 16);
}

/* Stock clocks the bus through library calls at 216 MHz (~3 MHz SCK); a
 * fixed ~0.4 us half-period keeps this well inside the sensor's 10 MHz. */
static inline void half_period(void)
{
    delay_cycles(30);
}

static void xfer_write(uint8_t v)
{
    for (int i = 7; i >= 0; i--) {
        pin(SCK, 0);
        pin(MOSI, (v >> i) & 1);
        half_period();
        pin(SCK, 1);
        half_period();
    }
}

static uint8_t xfer_read(void)
{
    uint8_t v = 0;
    for (int i = 7; i >= 0; i--) {
        pin(SCK, 0);
        half_period();
        if (GPIO_IDR(GPIOB) & (1u << MISO))
            v |= (uint8_t)(1u << i);
        pin(SCK, 1);
        half_period();
    }
    return v;
}

static void cs(int v)
{
    pin(CS, v);
    half_period();
}

static uint8_t cmd_status(const uint8_t *b, unsigned n)
{
    cs(0);
    for (unsigned i = 0; i < n; i++)
        xfer_write(b[i]);
    uint8_t st = xfer_read();
    cs(1);
    return st;
}

static uint16_t read_reg(uint8_t reg)
{
    cs(0);
    xfer_write(0x50);
    xfer_write((uint8_t)(reg << 2));
    (void)xfer_read();   /* status */
    uint16_t v = (uint16_t)(xfer_read() << 8);
    v |= xfer_read();
    cs(1);
    return v;
}

static void write_reg(uint8_t reg, uint16_t v)
{
    const uint8_t b[4] = { 0x60, (uint8_t)(v >> 8), (uint8_t)v, (uint8_t)(reg << 2) };
    (void)cmd_status(b, 4);
}

static void power_on(void)
{
    RCC_APB2ENR |= APB2_IOPA | APB2_IOPB;
    gpio_mode(GPIOB, CS, GPIO_OUT_PP_2);
    gpio_mode(GPIOB, SCK, GPIO_OUT_PP_2);
    gpio_mode(GPIOB, MOSI, GPIO_OUT_PP_2);
    gpio_mode(GPIOA, PWR, GPIO_OUT_OD_2);
    GPIO_BRR(GPIOA) = 1u << PWR;
    GPIO_BSRR(GPIOB) = 1u << MISO;   /* pull-up */
    gpio_mode(GPIOB, MISO, GPIO_IN_PULL);
    pin(MOSI, 1);
    pin(CS, 1);
    pin(SCK, 1);
}

void sensor_init(void)
{
    static const uint8_t ex = 0x80, sb = 0x1F;
    /* Stock's nominal 50/50/20/20/20 ms, as they elapse on the hardware
     * (see STOCK_MS in board.h). */
    power_on();
    delay_ms(STOCK_MS(50));
    (void)cmd_status(&ex, 1);   /* exit any running mode */
    delay_ms(STOCK_MS(50));
    write_reg(0x1c, 0x1430);
    /* Low 6 bits: burst interval in 20 ms steps, as the MLX90393's
     * BURST_DATA_RATE. Stock writes 2, and the position then changes only
     * every ~42 ms although it is read every 2 ms. 0 measures back to back. */
    write_reg(0x1d, 0x0000);
    write_reg(0x1e, 0x8000);
    (void)read_reg(0x23);
    delay_ms(STOCK_MS(20));
    (void)read_reg(0x0d);
    delay_ms(STOCK_MS(20));
    (void)cmd_status(&sb, 1);   /* burst mode, all axes */
    delay_ms(STOCK_MS(20));
}

void sensor_power_off(void)
{
    gpio_mode(GPIOB, MISO, GPIO_OUT_PP_2);
    pin(MISO, 0);
    GPIO_BSRR(GPIOA) = 1u << PWR;
    pin(MOSI, 0);
    pin(CS, 0);
    pin(SCK, 0);
}

void sensor_read(uint8_t frame[8])
{
    cs(0);
    xfer_write(0x4F);
    for (int i = 0; i < 8; i++)
        frame[i] = xfer_read();
    cs(1);
}
