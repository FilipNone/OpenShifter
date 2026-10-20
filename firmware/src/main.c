/* OpenShift: open firmware for the Turtle Beach VelocityOne Multi-Shift.
 * SPDX-License-Identifier: MIT
 *
 * Execution model:
 *   SysTick  (priority 0)  1 ms time base, liveness check, pends PendSV.
 *   USB      (priority 1)  enumeration, EP0, EP1 IN/OUT.
 *   PendSV   (priority 3)  all periodic application work (stock 1 ms tick).
 *   thread                 blocking work: sensor init, flash, USB attach.
 *
 * Recovery into the vendor bootloader (USB 10f5:7097, re-flashable):
 *   - both buttons (PA4 + PA5) held while the unit powers up;
 *   - any fault exception;
 *   - SysTick sees PendSV or the main loop stalled for 3 s;
 *   - the host reset the bus 6+ times without ever configuring the device;
 *   - the PLL could not be configured.
 */
#include "shifter.h"
#include "board.h"
#include "flash.h"
#include "sensor.h"
#include "periph.h"
#include "usb.h"
#include "regs.h"

#define PRIO_PENDSV 3u

static shifter_t sh;
static uint16_t st_loaded[SH_SETTINGS_HALFWORDS];    /* as read at boot */
static uint16_t st_pending[SH_SETTINGS_HALFWORDS];   /* snapshot for the next write */
static volatile uint8_t save_pending, boot_pending;

static volatile uint8_t sensor_ready, sensor_paused, sensor_off_timer, sensor_off_ticks;
static volatile uint8_t asleep, led_dirty;
static uint16_t suspend_ms, wake_ms;

/* IN reports due on EP1. Both are due every 2 ms and EP1 sends one per ms.
 * Up to 0.3.0 they went through a small queue. A host polling only every 2 ms
 * kept it full, report 1 (queued first) took each free slot and report 0x24
 * almost never went out. Now each report is a flag, the two take turns, and
 * each is built when it is sent. */
#define IN_R1  0x01u
#define IN_R36 0x02u
static uint8_t in_due, in_next = IN_R1;

static volatile uint32_t hb_pendsv, hb_main;

/* ---- helpers ---------------------------------------------------------- */

static void in_send_one(void)
{
    if (!in_due || !usb_in_ready())
        return;
    uint8_t which = (in_due & in_next) ? in_next : in_due;
    uint8_t r[64];
    if (which & IN_R1) {
        shifter_report1(&sh, r);
        usb_in_send(r, 5);
        in_due &= (uint8_t)~IN_R1;
        in_next = IN_R36;
    } else {
        shifter_report36(&sh, r);
        usb_in_send(r, 64);
        in_due &= (uint8_t)~IN_R36;
        in_next = IN_R1;
    }
}

static void sensor_off(void)
{
    sensor_paused = 1;
    sensor_power_off();
    sensor_off_ticks = 0;
    sensor_off_timer = 1;
}

unsigned usb_get_report(uint8_t report_id, uint8_t *out)
{
    if (report_id == 0x01) {
        shifter_report1(&sh, out);
        return 5;
    }
    if (report_id == 0x24) {
        shifter_report36(&sh, out);
        return 64;
    }
    return 0;
}

static void apply_actions(unsigned act)
{
    if (act & SH_ACT_LED)
        led_dirty = 1;
    if (act & (SH_ACT_SAVE | SH_ACT_BOOT)) {
        for (unsigned i = 0; i < SH_SETTINGS_HALFWORDS; i++)
            st_pending[i] = st_loaded[i];
        shifter_save(&sh, st_pending);
        save_pending = 1;
    }
    if (act & SH_ACT_BOOT) {
        led_dirty = 1;
        boot_pending = 1;
    }
}

/* ---- 1 ms application tick ---------------------------------------------- */

void pendsv_handler(void)
{
    uint32_t ms = g_ms;
    unsigned act = 0;
    uint8_t buf[USB_CMD_LEN];
    unsigned n;

    hb_pendsv++;

    if (!(ms % 2) && sensor_ready && !sensor_paused && !boot_pending) {
        uint8_t frame[8];
        sensor_read(frame);
        if (shifter_sensor_frame(&sh, frame) == SH_FRAME_POWER_OFF)
            sensor_off();
    }

    if (!(ms % 20))
        act |= shifter_buttons(&sh, pin_pb10_low(), pin_pa5_low(), pin_pa4_low());

    while ((n = usb_cmd_pop(buf)) != 0)
        act |= shifter_command(&sh, buf, n);
    if (act)
        apply_actions(act);

    if (usb_configured) {
        if (!(ms % 2))
            in_due = IN_R1 | IN_R36;
        in_send_one();
    } else {
        in_due = 0;
        if (!(ms % 5)) {
            uint8_t p[16];
            shifter_uart_packet(&sh, p);
            uart_send(p, sizeof p);
        }
    }

    if (!(ms % 50) && led_dirty) {
        uint8_t grb[9];
        shifter_led_grb(&sh, asleep, grb);
        if (leds_show(grb))
            led_dirty = 0;
    }

    if (!(ms % 100) && sensor_off_timer && ++sensor_off_ticks >= 20) {
        /* 2 s after power-off: let the main loop bring the sensor back */
        sensor_off_timer = 0;
        sensor_off_ticks = 0;
        sensor_ready = 0;
        sensor_paused = 0;
    }

    /* Host suspend: after 1 s, sensor and LEDs off. */
    if (usb_configured && usb_suspended) {
        wake_ms = 0;
        if (suspend_ms < 1000 && ++suspend_ms == 1000 && !asleep) {
            asleep = 1;
            sensor_off();
            led_dirty = 1;
        }
    } else {
        suspend_ms = 0;
        if (asleep && ++wake_ms >= 50) {
            asleep = 0;
            wake_ms = 0;
            sensor_off();   /* re-initialised 2 s later */
            led_dirty = 1;
        }
    }
}

/* ---- time base and liveness ----------------------------------------------- */

static volatile uint8_t watch_on;

void systick_handler(void)
{
    static uint32_t seen_pendsv, seen_main, t_pendsv, t_main;
    uint32_t ms = ++g_ms;

    if (watch_on) {
        if (hb_pendsv != seen_pendsv) {
            seen_pendsv = hb_pendsv;
            t_pendsv = ms;
        }
        if (hb_main != seen_main) {
            seen_main = hb_main;
            t_main = ms;
        }
        if (ms - t_pendsv > 3000 || ms - t_main > 3000)
            enter_bootloader();
    } else {
        t_pendsv = t_main = ms;
    }
    SCB_ICSR = ICSR_PENDSVSET;
}

void usb_lp_handler(void)
{
    usb_irq_handler();
}

/* ---- startup -------------------------------------------------------------- */

static int buttons_held_at_power_up(void)
{
    for (int i = 0; i < 100; i++) {
        if (!(pin_pa4_low() && pin_pa5_low()))
            return 0;
        delay_ms(1);
    }
    return 1;
}

int main(void)
{
    /* Exception priorities: SysTick 0 (highest), USB 1, PendSV 3. */
    SCB_SHPR3 = (PRIO_PENDSV << 4 << 16) | (0u << 24);

    if (clock_init())
        enter_bootloader();
    systick_init();
    irq_enable();
    board_pins_init();

    if (buttons_held_at_power_up())
        enter_bootloader();

    settings_read(st_loaded);
    shifter_init(&sh);
    shifter_load(&sh, st_loaded);

    leds_init();
    led_dirty = 1;
    watch_on = 1;

    usb_reconnect();
    usb_init();
    uint32_t attach_ms = g_ms;

    uint32_t reset_watch_ms = 0;
    for (;;) {
        hb_main++;

        /* Like stock: once configured, or 3 s after attaching without a host. */
        if (!sensor_ready && !sensor_paused && !asleep && (usb_configured || g_ms - attach_ms >= 3000)) {
            sensor_init();
            sensor_ready = 1;
        }

        if (usb_configured)
            uart_disable();
        else
            uart_enable();

        if (save_pending) {
            uint16_t st[SH_SETTINGS_HALFWORDS];
            irq_disable();
            for (unsigned i = 0; i < SH_SETTINGS_HALFWORDS; i++)
                st[i] = st_pending[i];
            save_pending = 0;
            irq_enable();
            settings_write(st);
        }

        if (boot_pending && !save_pending) {
            /* "24 00": show yellow, then hand over to the bootloader. */
            uint32_t t0 = g_ms;
            while ((led_dirty || leds_busy()) && g_ms - t0 < 200)
                hb_main++;
            enter_bootloader();
        }

        /* Host keeps resetting the bus but never configures us: our USB
         * stack is not working with it, so give the bootloader the bus. */
        if (!usb_ever_configured && usb_reset_count >= 6) {
            if (!reset_watch_ms)
                reset_watch_ms = g_ms | 1;
            else if (g_ms - reset_watch_ms > 5000)
                enter_bootloader();
        }

        __asm volatile("wfi");
    }
}
