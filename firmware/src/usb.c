/* OpenShifter: USB full-speed HID device. SPDX-License-Identifier: MIT
 *
 * Minimal driver for the STM32F1-style USB peripheral. The device and
 * configuration descriptors and the reports are the stock ones, so the
 * vendor tool sees the same device, with two exceptions: iConfiguration
 * names this firmware, and the report descriptor is rewritten (see
 * usb_report_desc, whose length the HID descriptor carries) and declares 13
 * buttons instead of 11: button 1 is the optional neutral button, the 12
 * stock report bits are buttons 2 to 13 (13 is the handbrake button in
 * H-pattern).
 *
 * PMA layout (byte offsets): BTABLE 0x00, EP0 TX 0x40, EP0 RX 0x80,
 * EP1 TX 0xC0, EP1 RX 0x100; 64 bytes each.
 */
#include "usb.h"
#include "board.h"
#include "regs.h"
#include "version.h"

/* ---- descriptors ---------------------------------------------------- */

const uint8_t usb_device_desc[18] = {
    0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
    0xf5, 0x10, 0x96, 0x70, 0x11, 0x01, 0x01, 0x02, 0x03, 0x01,
};

/* The same reports as stock, described so that the built-in HID drivers of
 * Linux, Windows and macOS see a joystick. Stock nests application
 * collections without a usage, so Linux maps the buttons to BTN_0.. and udev
 * does not tag the device as a joystick; it also gives the buttons 16 bits
 * for 11 usages, and puts report 0x24 on usage 0x01 of the Generic Desktop
 * page, which Linux turns into a REL_MISC axis.
 *
 * Button 1 is report bit 12, the neutral button (off unless turned on with
 * 24 35 1). Linux (joydev, SDL) numbers buttons from 0 in usage order, so
 * gear 1 is button 1 there; Windows numbers from 1, where gear 1 is button 2. */
const uint8_t usb_report_desc[89] = {
    0x05, 0x01,                     /* Usage Page (Generic Desktop) */
    0x09, 0x04,                     /* Usage (Joystick) */
    0xa1, 0x01,                     /* Collection (Application) */
    0x85, 0x01,                     /*   Report ID 1 */
    0x05, 0x09,                     /*   Usage Page (Button) */
    0x19, 0x02, 0x29, 0x0d,         /*   Usage 2 to 13 */
    0x15, 0x00, 0x25, 0x01,         /*   Logical 0 to 1 */
    0x75, 0x01, 0x95, 0x0c,         /*   12 x 1 bit */
    0x81, 0x02,                     /*   Input (Data, Var, Abs) */
    0x09, 0x01,                     /*   Usage 1 */
    0x95, 0x01,                     /*   1 x 1 bit: neutral */
    0x81, 0x02,                     /*   Input (Data, Var, Abs) */
    0x95, 0x03,                     /*   3 x 1 bit */
    0x81, 0x03,                     /*   Input (Const): padding */
    0x05, 0x01,                     /*   Usage Page (Generic Desktop) */
    0x09, 0x32,                     /*   Usage (Z): handbrake */
    0x15, 0x00, 0x27, 0xff, 0xff, 0x00, 0x00,   /* Logical 0 to 65535 */
    0x35, 0x00, 0x47, 0xff, 0xff, 0x00, 0x00,   /* Physical 0 to 65535 */
    0x75, 0x10, 0x95, 0x01,         /*   1 x 16 bits */
    0x81, 0x02,                     /*   Input (Data, Var, Abs) */
    0x85, 0x24,                     /*   Report ID 0x24 */
    0x06, 0x00, 0xff,               /*   Usage Page (Vendor 0xFF00) */
    0x09, 0x01,                     /*   Usage (1) */
    0x15, 0x00, 0x26, 0xff, 0x00,   /*   Logical 0 to 255 */
    0x75, 0x08, 0x95, 0x3f,         /*   63 x 8 bits */
    0x81, 0x02,                     /*   Input (Data, Var, Abs): status */
    0x09, 0x01,                     /*   Usage (1) */
    0x91, 0x02,                     /*   Output (Data, Var, Abs): commands */
    0x85, 0x25,                     /*   Report ID 0x25 */
    0x09, 0x02,                     /*   Usage (2) */
    0x95, 0x0f,                     /*   15 x 8 bits */
    0xb1, 0x02,                     /*   Feature (Data, Var, Abs): version */
    0xc0,                           /* End Collection */
};

/* Feature report 0x25: the firmware version, NUL padded. WebHID cannot read
 * iConfiguration, so this is how a browser tells OpenShifter versions apart. */
#define VERSION_REPORT_ID  0x25
#define VERSION_REPORT_LEN 16
_Static_assert(sizeof OPENSHIFTER_VERSION - 1 < VERSION_REPORT_LEN,
               "version does not fit feature report 0x25");

static unsigned version_report(uint8_t *out)
{
    static const char v[] = OPENSHIFTER_VERSION;
    out[0] = VERSION_REPORT_ID;
    for (unsigned i = 1; i < VERSION_REPORT_LEN; i++)
        out[i] = i - 1 < sizeof v - 1 ? (uint8_t)v[i - 1] : 0;
    return VERSION_REPORT_LEN;
}

const uint8_t usb_config_desc[41] = {
    0x09, 0x02, 0x29, 0x00, 0x01, 0x01, 0x04, 0x80, 0x64,
    0x09, 0x04, 0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,
    0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22, sizeof usb_report_desc, 0x00,
    0x07, 0x05, 0x81, 0x03, 0x40, 0x00, 0x01,
    0x07, 0x05, 0x01, 0x03, 0x40, 0x00, 0x01,
};

/* Stock reports the product strings with two trailing NUL characters. */
#define USB_STR(s) { s, sizeof s - 1 }
static const struct { const char *s; uint8_t n; } usb_strings[] = {
    { 0, 0 },
    USB_STR("VelocityOne Multi-Shift\0\0"),
    USB_STR("VelocityOne Multi-Shift\0\0"),
    USB_STR("TBRS004-20241224"),
    USB_STR("OpenShifter " OPENSHIFTER_VERSION),
};

/* ---- low level -------------------------------------------------------- */

#define EP_CTR_RX   0x8000u
#define EP_SETUP    0x0800u
#define EP_CTR_TX   0x0080u
#define EP_CONTROL  0x0200u
#define EP_INTERRUPT 0x0600u
#define STAT_STALL  1u
#define STAT_NAK    2u
#define STAT_VALID  3u

#define ISTR_CTR   0x8000u
#define ISTR_WKUP  0x1000u
#define ISTR_SUSP  0x0800u
#define ISTR_RESET 0x0400u
#define ISTR_SOF   0x0200u
#define ISTR_ESOF  0x0100u
#define ISTR_DIR   0x0010u

#define CNTR_CTRM  0x8000u
#define CNTR_WKUPM 0x1000u
#define CNTR_SUSPM 0x0800u
#define CNTR_RESETM 0x0400u
#define CNTR_FSUSP 0x0008u
#define CNTR_LPMODE 0x0004u
#define CNTR_PDWN  0x0002u
#define CNTR_FRES  0x0001u

#define PMA_EP0_TX 0x40u
#define PMA_EP0_RX 0x80u
#define PMA_EP1_TX 0xC0u
#define PMA_EP1_RX 0x100u
#define COUNT_RX_64 0x8400u   /* BL_SIZE=1, NUM_BLOCK=1: 64 bytes */

static volatile uint32_t *pma(unsigned off)
{
    return (volatile uint32_t *)(USB_PMA_BASE + 2 * off);
}

static void pma_write(unsigned off, const uint8_t *p, unsigned n)
{
    for (unsigned i = 0; i < n; i += 2) {
        uint16_t v = p[i];
        if (i + 1 < n)
            v |= (uint16_t)(p[i + 1] << 8);
        *pma(off + i) = v;
    }
}

static void pma_read(unsigned off, uint8_t *p, unsigned n)
{
    for (unsigned i = 0; i < n; i += 2) {
        uint16_t v = (uint16_t)*pma(off + i);
        p[i] = (uint8_t)v;
        if (i + 1 < n)
            p[i + 1] = (uint8_t)(v >> 8);
    }
}

static void bt_set(unsigned ep, unsigned field, uint16_t v)
{
    *pma(8 * ep + 2 * field) = v;
}

static unsigned rx_count(unsigned ep)
{
    return *pma(8 * ep + 6) & 0x3FFu;
}

static void ep_clear_ctr_rx(unsigned ep)
{
    USB_EPR(ep) = (USB_EPR(ep) & 0x0F0Fu) | EP_CTR_TX;
}

static void ep_clear_ctr_tx(unsigned ep)
{
    USB_EPR(ep) = (USB_EPR(ep) & 0x0F0Fu) | EP_CTR_RX;
}

static void ep_stat_tx(unsigned ep, unsigned v)
{
    USB_EPR(ep) = ((USB_EPR(ep) & 0x8FBFu) ^ (v << 4)) | EP_CTR_RX | EP_CTR_TX;
}

static void ep_stat_rx(unsigned ep, unsigned v)
{
    USB_EPR(ep) = ((USB_EPR(ep) & 0xBF8Fu) ^ (v << 12)) | EP_CTR_RX | EP_CTR_TX;
}

/* Set type/address, both data toggles to 0 and both STAT fields. */
static void ep_setup(unsigned ep, uint16_t type, unsigned tx, unsigned rx)
{
    uint32_t r = USB_EPR(ep);
    uint32_t want = (rx << 12) | (tx << 4);
    USB_EPR(ep) = type | ep | ((r ^ want) & 0x7070u);
}

/* ---- state ------------------------------------------------------------ */

volatile uint8_t  usb_configured;
volatile uint8_t  usb_suspended;
volatile uint8_t  usb_ever_configured;
volatile uint16_t usb_reset_count;

static volatile uint8_t ep1_busy;
static uint8_t pending_addr;
static uint8_t config_value;
static uint8_t idle_rate;
static uint8_t protocol;          /* stock answers GET_PROTOCOL with 0 until set */

/* EP0 transfer state */
static const uint8_t *ctl_ptr;
static uint16_t ctl_left;
static uint8_t ctl_zlp;
static uint8_t ctl_buf[64];
static uint8_t ctl_out_buf[USB_CMD_LEN];
static uint16_t ctl_out_len, ctl_out_want;
static uint8_t ctl_out_active;

/* OUT report ring (USB IRQ produces, PendSV consumes) */
#define CMD_SLOTS 8
static uint8_t cmd_data[CMD_SLOTS][USB_CMD_LEN];
static uint8_t cmd_len[CMD_SLOTS];
static volatile uint8_t cmd_head, cmd_tail;

static void cmd_push(const uint8_t *p, unsigned n)
{
    uint8_t h = cmd_head, next = (uint8_t)((h + 1) % CMD_SLOTS);
    if (next == cmd_tail || n == 0)
        return;   /* full: drop, like a busy device */
    if (n > USB_CMD_LEN)
        n = USB_CMD_LEN;
    for (unsigned i = 0; i < n; i++)
        cmd_data[h][i] = p[i];
    cmd_len[h] = (uint8_t)n;
    cmd_head = next;
}

unsigned usb_cmd_pop(uint8_t out[USB_CMD_LEN])
{
    uint8_t t = cmd_tail;
    if (t == cmd_head)
        return 0;
    unsigned n = cmd_len[t];
    for (unsigned i = 0; i < n; i++)
        out[i] = cmd_data[t][i];
    cmd_tail = (uint8_t)((t + 1) % CMD_SLOTS);
    return n;
}

/* ---- EP0 -------------------------------------------------------------- */

static void ctl_send_next(void)
{
    unsigned n = ctl_left > 64 ? 64 : ctl_left;
    pma_write(PMA_EP0_TX, ctl_ptr, n);
    bt_set(0, 1, (uint16_t)n);
    ctl_ptr += n;
    ctl_left = (uint16_t)(ctl_left - n);
    if (n < 64)
        ctl_zlp = 0;   /* short packet ends the transfer */
    ep_stat_tx(0, STAT_VALID);
}

static void ctl_in(const uint8_t *p, unsigned len, unsigned wlength)
{
    if (len > wlength)
        len = wlength;
    ctl_ptr = p;
    ctl_left = (uint16_t)len;
    ctl_zlp = len < wlength && (len % 64) == 0;   /* includes len == 0 */
    ctl_send_next();
    ep_stat_rx(0, STAT_VALID);   /* status stage OUT */
}

static void ctl_status_in(void)
{
    ctl_ptr = 0;
    ctl_left = 0;
    ctl_zlp = 0;
    bt_set(0, 1, 0);
    ep_stat_tx(0, STAT_VALID);
}

static void ctl_stall(void)
{
    ep_stat_tx(0, STAT_STALL);
    ep_stat_rx(0, STAT_STALL);
}

static unsigned string_desc(unsigned idx, uint8_t *out)
{
    if (idx == 0) {
        out[0] = 4;
        out[1] = 3;
        out[2] = 0x09;
        out[3] = 0x04;
        return 4;
    }
    if (idx >= sizeof usb_strings / sizeof usb_strings[0])
        return 0;
    const char *s = usb_strings[idx].s;
    unsigned n = 2;
    for (unsigned i = 0; i < usb_strings[idx].n && n < 62; i++) {
        out[n++] = (uint8_t)s[i];
        out[n++] = 0;
    }
    out[0] = (uint8_t)n;
    out[1] = 3;
    return n;
}

static void ep1_open(void)
{
    bt_set(1, 0, PMA_EP1_TX);
    bt_set(1, 1, 0);
    bt_set(1, 2, PMA_EP1_RX);
    bt_set(1, 3, COUNT_RX_64);
    ep_setup(1, EP_INTERRUPT, STAT_NAK, STAT_VALID);
    ep1_busy = 0;
}

static void ep1_close(void)
{
    ep_setup(1, EP_INTERRUPT, 0, 0);
    ep1_busy = 0;
}

static void handle_setup(const uint8_t *s)
{
    uint8_t type = s[0], req = s[1];
    uint16_t value = (uint16_t)(s[2] | s[3] << 8);
    uint16_t index = (uint16_t)(s[4] | s[5] << 8);
    uint16_t length = (uint16_t)(s[6] | s[7] << 8);
    unsigned n;
    (void)index;

    ctl_out_active = 0;

    if ((type & 0x60) == 0x00) {   /* standard */
        switch (req) {
        case 0x06: {   /* GET_DESCRIPTOR */
            uint8_t dt = (uint8_t)(value >> 8), di = (uint8_t)value;
            if (dt == 1) {
                ctl_in(usb_device_desc, sizeof usb_device_desc, length);
            } else if (dt == 2) {
                ctl_in(usb_config_desc, sizeof usb_config_desc, length);
            } else if (dt == 3 && (n = string_desc(di, ctl_buf)) != 0) {
                ctl_in(ctl_buf, n, length);
            } else if (dt == 0x21) {
                ctl_in(&usb_config_desc[18], 9, length);
            } else if (dt == 0x22) {
                ctl_in(usb_report_desc, sizeof usb_report_desc, length);
            } else {
                ctl_stall();
            }
            return;
        }
        case 0x05:   /* SET_ADDRESS: applied after the status stage */
            pending_addr = (uint8_t)(value & 0x7F);
            ctl_status_in();
            return;
        case 0x09:   /* SET_CONFIGURATION */
            if (value > 1) {
                ctl_stall();
                return;
            }
            config_value = (uint8_t)value;
            if (value) {
                ep1_open();
                usb_configured = 1;
                usb_ever_configured = 1;
            } else {
                ep1_close();
                usb_configured = 0;
            }
            ctl_status_in();
            return;
        case 0x08:   /* GET_CONFIGURATION */
            ctl_buf[0] = config_value;
            ctl_in(ctl_buf, 1, length);
            return;
        case 0x00:   /* GET_STATUS */
            ctl_buf[0] = 0;
            ctl_buf[1] = 0;
            ctl_in(ctl_buf, 2, length);
            return;
        case 0x01:   /* CLEAR_FEATURE */
            if ((type & 0x1F) == 2 && (index & 0x7F) == 1 && usb_configured)
                ep1_open();   /* ENDPOINT_HALT on EP1: reset toggles */
            ctl_status_in();
            return;
        case 0x03:   /* SET_FEATURE */
        case 0x0B:   /* SET_INTERFACE */
            ctl_status_in();
            return;
        case 0x0A:   /* GET_INTERFACE */
            ctl_buf[0] = 0;
            ctl_in(ctl_buf, 1, length);
            return;
        }
    } else if ((type & 0x60) == 0x20) {   /* HID class */
        switch (req) {
        case 0x01:   /* GET_REPORT; wValue: type << 8 | ID */
            if (value == (0x0300 | VERSION_REPORT_ID))
                n = version_report(ctl_buf);
            else
                n = usb_get_report((uint8_t)value, ctl_buf);
            if (n)
                ctl_in(ctl_buf, n, length);
            else
                ctl_stall();
            return;
        case 0x09:   /* SET_REPORT */
            if (length == 0 || length > USB_CMD_LEN) {
                ctl_stall();
                return;
            }
            ctl_out_active = 1;
            ctl_out_len = 0;
            ctl_out_want = length;
            ep_stat_rx(0, STAT_VALID);
            return;
        case 0x0A:   /* SET_IDLE: stock stalls it; hosts handle that */
            ctl_stall();
            return;
        case 0x02:   /* GET_IDLE */
            ctl_buf[0] = idle_rate;
            ctl_in(ctl_buf, 1, length);
            return;
        case 0x0B:   /* SET_PROTOCOL */
            protocol = (uint8_t)value;
            ctl_status_in();
            return;
        case 0x03:   /* GET_PROTOCOL */
            ctl_buf[0] = protocol;
            ctl_in(ctl_buf, 1, length);
            return;
        }
    }
    ctl_stall();
}

static void ep0_rx(void)
{
    uint32_t r = USB_EPR(0);
    unsigned n = rx_count(0);
    if (r & EP_SETUP) {
        uint8_t s[8];
        pma_read(PMA_EP0_RX, s, 8);
        ep_clear_ctr_rx(0);
        handle_setup(s);
        return;
    }
    ep_clear_ctr_rx(0);
    if (ctl_out_active) {
        unsigned room = (unsigned)(ctl_out_want - ctl_out_len);
        if (n > room)
            n = room;
        pma_read(PMA_EP0_RX, ctl_out_buf + ctl_out_len, n);
        ctl_out_len = (uint16_t)(ctl_out_len + n);
        if (ctl_out_len >= ctl_out_want || n < 64) {
            ctl_out_active = 0;
            cmd_push(ctl_out_buf, ctl_out_len);
            ctl_status_in();
        } else {
            ep_stat_rx(0, STAT_VALID);
        }
        return;
    }
    /* status stage of an IN transfer */
    ep_stat_rx(0, STAT_VALID);
}

static void ep0_tx(void)
{
    ep_clear_ctr_tx(0);
    if (pending_addr) {
        USB_DADDR = 0x80u | pending_addr;
        pending_addr = 0;
    }
    if (ctl_left || ctl_zlp) {
        if (!ctl_left)
            ctl_zlp = 0;
        ctl_send_next();
    }
}

/* ---- EP1 -------------------------------------------------------------- */

int usb_in_ready(void)
{
    return usb_configured && !ep1_busy;
}

void usb_in_send(const uint8_t *p, unsigned len)
{
    if (!usb_in_ready())
        return;
    ep1_busy = 1;
    pma_write(PMA_EP1_TX, p, len);
    bt_set(1, 1, (uint16_t)len);
    ep_stat_tx(1, STAT_VALID);
}

static void ep1_event(void)
{
    uint32_t r = USB_EPR(1);
    if (r & EP_CTR_TX) {
        ep_clear_ctr_tx(1);
        ep1_busy = 0;
    }
    if (r & EP_CTR_RX) {
        uint8_t b[64];
        unsigned n = rx_count(1);
        if (n > 64)
            n = 64;
        pma_read(PMA_EP1_RX, b, n);
        ep_clear_ctr_rx(1);
        cmd_push(b, n);
        ep_stat_rx(1, STAT_VALID);
    }
}

/* ---- bus events -------------------------------------------------------- */

static void bus_reset(void)
{
    USB_BTABLE = 0;
    bt_set(0, 0, PMA_EP0_TX);
    bt_set(0, 1, 0);
    bt_set(0, 2, PMA_EP0_RX);
    bt_set(0, 3, COUNT_RX_64);
    ep_setup(0, EP_CONTROL, STAT_NAK, STAT_VALID);
    ep1_close();
    USB_DADDR = 0x80;
    pending_addr = 0;
    config_value = 0;
    ctl_out_active = 0;
    usb_configured = 0;
    if (usb_reset_count < 0xFFFF)
        usb_reset_count++;
}

void usb_irq_handler(void)
{
    uint32_t istr;
    while ((istr = USB_ISTR) & (ISTR_CTR | ISTR_RESET | ISTR_SUSP | ISTR_WKUP)) {
        if (istr & ISTR_RESET) {
            USB_ISTR = (uint16_t)~ISTR_RESET;
            bus_reset();
            continue;
        }
        if (istr & ISTR_CTR) {
            unsigned ep = istr & 0xF;
            if (ep == 0) {
                uint32_t r = USB_EPR(0);
                if (r & EP_CTR_TX)
                    ep0_tx();
                if (r & EP_CTR_RX)
                    ep0_rx();
            } else if (ep == 1) {
                ep1_event();
            } else {
                USB_EPR(ep) = USB_EPR(ep) & 0x0F0Fu;   /* stray: clear both CTR */
            }
            continue;
        }
        if (istr & ISTR_WKUP) {
            USB_CNTR &= ~(CNTR_FSUSP | CNTR_LPMODE);
            USB_ISTR = (uint16_t)~ISTR_WKUP;
            usb_suspended = 0;
            continue;
        }
        if (istr & ISTR_SUSP) {
            USB_CNTR |= CNTR_FSUSP;
            USB_ISTR = (uint16_t)~ISTR_SUSP;
            usb_suspended = 1;
            continue;
        }
    }
    USB_ISTR = (uint16_t)~(ISTR_SOF | ISTR_ESOF);
}

void usb_reconnect(void)
{
    RCC_APB2ENR |= APB2_IOPA;
    RCC_APB1ENR |= APB1_USB;
    USB_AIR_DPPU = 0;
    USB_CNTR = CNTR_FRES | CNTR_PDWN;
    GPIO_BRR(GPIOA) = 1u << 12;
    gpio_mode(GPIOA, 11, GPIO_OUT_PP_50);
    gpio_mode(GPIOA, 12, GPIO_OUT_PP_50);
    delay_ms(STOCK_MS(500));
    gpio_mode(GPIOA, 11, GPIO_IN_ANALOG);
    gpio_mode(GPIOA, 12, GPIO_IN_ANALOG);
    USB_CNTR = CNTR_FRES;
    delay_ms(STOCK_MS(200));
}

void usb_init(void)
{
    RCC_APB1ENR |= APB1_USB;
    USB_CNTR = CNTR_FRES;
    delay_cycles(g_sysclk_hz / 1000000);   /* tSTARTUP ~1 us */
    USB_CNTR = 0;
    USB_ISTR = 0;
    USB_BTABLE = 0;
    USB_CNTR = CNTR_CTRM | CNTR_RESETM | CNTR_SUSPM | CNTR_WKUPM;
    NVIC_IPR8(USB_IRQ_LP) = 1u << 4;
    NVIC_ICPR(USB_IRQ_LP / 32) = 1u << (USB_IRQ_LP % 32);
    NVIC_ISER(USB_IRQ_LP / 32) = 1u << (USB_IRQ_LP % 32);
    USB_AIR_DPPU = 1;   /* attach */
}
