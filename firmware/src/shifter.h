/* OpenShift: open firmware for the Turtle Beach VelocityOne Multi-Shift.
 * SPDX-License-Identifier: MIT
 *
 * Portable shifter logic: sensor frame -> position -> gear/sequential/handbrake
 * decision, buttons, calibration commands, settings image and HID/UART report
 * encoding. No hardware access; the same file is compiled for the MCU and for
 * the host tests.
 *
 * The starting point was the behaviour of the stock application
 * (VelocityOneTuner_Shift_Firmware.bin, sha256 96da5e6b...c0d1), worked out
 * from its disassembly; no stock code is copied. Where a rule still follows
 * stock, the stock code address (offset from 0x08010000) is cited.
 */
#ifndef OPENSHIFT_SHIFTER_H
#define OPENSHIFT_SHIFTER_H

#include <stdint.h>

#define SH_MODE_HPATTERN   0
#define SH_MODE_SEQUENTIAL 1
#define SH_MODE_HANDBRAKE  2

#define SH_SETTINGS_HALFWORDS 100       /* stock settings block size */
#define SH_SETTINGS_MAGIC     0x1254u   /* halfword at byte offset 0xc6 */

/* Result of shifter_sensor_frame(). */
#define SH_FRAME_OK        0
#define SH_FRAME_EMPTY     1  /* all-zero frame, ignored */
#define SH_FRAME_POWER_OFF 2  /* 100 empty frames in a row: power-cycle sensor */

/* Side-effect flags returned by commands / buttons. */
#define SH_ACT_SAVE  0x01u    /* persist settings */
#define SH_ACT_LED   0x02u    /* LED colours changed */
#define SH_ACT_BOOT  0x04u    /* reboot into the vendor bootloader */

typedef struct {
    /* --- outputs ("G" block, stock 0x200017d8) --- */
    uint8_t  gears;          /* G+0: H-pattern gear bitmask (bit n = gear n+1) */
    uint8_t  seq_down;       /* G+1 */
    uint8_t  seq_up;         /* G+2 */
    uint8_t  btn_a;          /* G+3: PA4 button */
    uint8_t  btn_b;          /* G+4: PA5 button */
    uint8_t  sw_h;           /* G+5: PB10 H/SEQ switch (1 = H-pattern) */
    uint16_t handbrake;      /* G+6 */
    int16_t  x, y;           /* G+8, G+0xa: processed position */
    int16_t  hb_x_rest, hb_x_pulled;   /* G+0xc, G+0xe */
    int16_t  hb_y_rest, hb_y_pulled;   /* G+0x10, G+0x12 */
    int16_t  gear_x[8], gear_y[8];     /* G+0x14 + 6k: calibrated gear points */
    uint8_t  cfg[8];                   /* G+0x44: reported radius/8 per gear */

    /* --- gear matching tables --- */
    int16_t  match_x[8];     /* 0x20001824 */
    int16_t  match_y[8];     /* 0x20000108 */
    int16_t  match_r[8];     /* 0x20001836 */
    int16_t  seq_up_thr;     /* 0x20000020 */
    int16_t  seq_down_thr;   /* 0x2000001e */

    /* --- configuration / flags --- */
    uint8_t  mode;           /* 0x2000008f */
    uint8_t  aux_flag;       /* 0x20000091: toggled by PA4, persisted, otherwise unused */
    uint8_t  settings_valid; /* 0x2000007a */
    uint8_t  g1_left_of_g3;  /* 0x20000078 */
    uint8_t  cal_bits;       /* 0x2000001d */
    uint8_t  hb_rest_cal;    /* 0x2000001c */
    uint8_t  hb_pulled_cal;  /* 0x2000001b */
    uint16_t led_rgb[3];     /* 0x20000088 */
    uint8_t  aux13[13];      /* 0x200000a6: opaque host data, persisted */

    /* --- decision state ("pc" block, stock 0x200000cc) --- */
    uint8_t  deb_prev;       /* pc+0 */
    uint8_t  cand;           /* pc+1 */
    uint8_t  deb_count;      /* pc+2 */
    uint8_t  deb_limit;      /* pc+3: 20 samples */
    uint8_t  empty_frames;   /* pc+4 */
    uint8_t  neutral_flags;  /* pc+5 */
    uint8_t  latched;        /* pc+8 */
    int16_t  neutral_x;      /* pc+0xa */
    int16_t  neutral_y;      /* pc+0xc */
    int16_t  hb_lo, hb_hi;   /* pc+0xe, pc+0x10 */

    /* --- gear learning on an uncalibrated unit (stock 0x1f54/0x1ab4) --- */
    int16_t  tilt;           /* pc+0x1e: trunc(atan2(s14, s12) * 1000), float path */
    int16_t  tilt_ref;       /* pc+0x20 */
    uint16_t stable;         /* pc+0x22: frames within +-7 of tilt_ref */
    uint32_t ref_r;          /* pc+0x24: float bits, reference radius (1100.0f = unset) */
    uint32_t rf;             /* pc+0x38: float bits, (float)sqrt(r2) */
    int16_t  neutral_r;      /* 0x20001834 */

    /* --- last sensor sample --- */
    uint16_t raw_t, raw_x, raw_y, raw_z;
    uint16_t s12;            /* in-plane magnitude */
    int16_t  s14;            /* out-of-plane component */
    int32_t  r2;             /* s12^2 + s14^2 */
    uint16_t r;              /* floor(sqrt(r2)) */
    uint8_t  theta_lt;       /* atan2(s14, s12) <  0.01f */
    uint8_t  theta_le;       /* atan2(s14, s12) <= 0.01f */

    /* --- buttons (stock 0x20000062) --- */
    uint8_t  sw_prev;        /* 99 at boot: forces an initial mode sync */
    uint8_t  a_prev;
    uint16_t a_count, b_count;
    uint16_t a_thr, b_thr;   /* 1 scan */
} shifter_t;

void     shifter_init(shifter_t *s);
void     shifter_load(shifter_t *s, const uint16_t settings[SH_SETTINGS_HALFWORDS]);
/* Writes the shifter-owned fields into a copy of the current settings block;
 * all other halfwords (bootloader flag, unknown fields) are left untouched. */
void     shifter_save(const shifter_t *s, uint16_t settings[SH_SETTINGS_HALFWORDS]);

/* One 2 ms sensor sample: 8 bytes read after the 0x4F (read measurement)
 * command: status, T[2], X[2], Y[2], Z hi. Runs decision + neutral tracking. */
int      shifter_sensor_frame(shifter_t *s, const uint8_t frame[8]);

/* One 20 ms button scan; arguments are 1 when the pin reads low. */
unsigned shifter_buttons(shifter_t *s, int pb10_low, int pa5_low, int pa4_low);

/* HID OUT report (report id 0x24 first). Returns SH_ACT_* flags. */
unsigned shifter_command(shifter_t *s, const uint8_t *buf, unsigned len);

void     shifter_report1(const shifter_t *s, uint8_t out[5]);
void     shifter_report36(const shifter_t *s, uint8_t out[64]);
void     shifter_uart_packet(const shifter_t *s, uint8_t out[16]);
/* Wire order G,R,B for the three WS2812 LEDs; all zero when asleep. */
void     shifter_led_grb(const shifter_t *s, int asleep, uint8_t out[9]);

/* Exposed for tests. */
uint32_t sh_isqrt(uint32_t v);
void     sh_sincos_q30(uint32_t angle, int32_t *sin_q30, int32_t *cos_q30);

#endif
