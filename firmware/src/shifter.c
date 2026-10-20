/* OpenShift: portable shifter logic. SPDX-License-Identifier: MIT
 *
 * The stock firmware does this with soft-float/soft-double; the Cortex-M3 has
 * no FPU and this project ships no libgcc, so everything here is integer.
 * The integer forms were checked against the stock double expressions:
 *   s12  (Y * 0.60725 / 6.55, truncated): exact for all 65536 inputs
 *   s14  ((Z - 32768) / 10.2, truncated): exact for all 256 inputs
 *   x/y  (s12 * 6.55 * sin/cos(phi)): CORDIC, |error| < 0.01 count
 * See tests/test_shifter.py for the checks.
 *
 * Gear learning on an uncalibrated unit compares floats against products of
 * floats, so that part keeps the stock single-precision values bit for bit
 * with a minimal round-to-nearest-even soft-float (sf_*), non-negative
 * operands only, which is all the stock code feeds it.
 */
#include "shifter.h"

#define S12_MUL   398186091u    /* floor-exact Q32 of 0.60725 / 6.55 */
#define PHI_Q     4292789931u   /* X -> binary angle (2^32/turn), Q16; vendor uses pi = 3.14 */
#define TAN_T_Q40 10995482551LL /* tan(0.01f) in Q40 */
#define CORDIC_K  652032874     /* CORDIC gain compensation, Q30 */

static const int32_t cordic_atan[30] = {
    536870912, 316933406, 167458907, 85004756, 42667331, 21354465, 10679838,
    5340245, 2670163, 1335087, 667544, 333772, 166886, 83443, 41722, 20861,
    10430, 5215, 2608, 1304, 652, 326, 163, 81, 41, 20, 10, 5, 3, 1,
};

/* Loader defaults (stock 0x111e) for an unconfigured unit. */
static const int16_t default_gear_x[8] = { 1986, 2400, 40, 70, -1956, -2576, -866, -1116 };
static const int16_t default_gear_y[8] = { 2400, -1886, 3175, -2536, 1720, -1856, 170, -236 };

uint32_t sh_isqrt(uint32_t v)
{
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v)
        bit >>= 2;
    while (bit) {
        if (v >= r + bit) {
            v -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    return r;
}

void sh_sincos_q30(uint32_t angle, int32_t *sin_q30, int32_t *cos_q30)
{
    /* Fold into [-90, 90] degrees; CORDIC rotation mode converges there.
     * Unsigned: the half-turn shift must wrap, not overflow. */
    int neg = 0;
    if (angle > 0x40000000u && angle < 0xC0000000u) {
        angle += 0x80000000u;
        neg = 1;
    }
    int32_t z = (int32_t)angle;
    int32_t x = CORDIC_K, y = 0;
    for (int i = 0; i < 30; i++) {
        int32_t dx = y >> i, dy = x >> i;
        if (z >= 0) {
            x -= dx;
            y += dy;
            z -= cordic_atan[i];
        } else {
            x += dx;
            y -= dy;
            z += cordic_atan[i];
        }
    }
    *sin_q30 = neg ? -y : y;
    *cos_q30 = neg ? -x : x;
}


/* ---- minimal IEEE single precision, values >= 0 (sign only in sf_mul) ---- */

#define F_0_24  0x3e75c28fu
#define F_0_6   0x3f19999au
#define F_0_65  0x3f266666u
#define F_0_9   0x3f666666u
#define F_6     0x40c00000u
#define F_9     0x41100000u
#define F_10    0x41200000u
#define F_1000  0x447a0000u
#define F_1100  0x44898000u   /* stock "reference radius not set" */

/* m * 2^e, plus a non-zero tail below m's lsb if sticky; m needs >= 25
 * significant bits whenever sticky is set. Rounds to nearest even. */
static uint32_t sf_pack(uint64_t m, int e, int sticky)
{
    if (!m)
        return 0;
    while (m >= (1ull << 25)) {
        sticky |= (int)(m & 1);
        m >>= 1;
        e++;
    }
    while (m < (1ull << 24)) {
        m <<= 1;
        e--;
    }
    unsigned guard = (unsigned)(m & 1);
    m >>= 1;
    e++;
    if (guard && (sticky || (m & 1))) {
        if (++m == (1ull << 24)) {
            m >>= 1;
            e++;
        }
    }
    return (uint32_t)(e + 23 + 127) << 23 | ((uint32_t)m & 0x7fffffu);
}

static uint32_t sf_mant(uint32_t f)
{
    return (f & 0x7fffffu) | 0x800000u;
}

static int sf_exp(uint32_t f)
{
    return (int)((f >> 23) & 0xff) - 150;
}

static uint32_t sf_mul(uint32_t a, uint32_t b)
{
    uint32_t sign = (a ^ b) & 0x80000000u;
    if (!(a & 0x7fffffffu) || !(b & 0x7fffffffu))
        return 0;
    return sign | sf_pack((uint64_t)sf_mant(a) * sf_mant(b), sf_exp(a) + sf_exp(b), 0);
}

static uint32_t sf_div(uint32_t a, uint32_t b)
{
    if (!a)
        return 0;
    uint32_t ma = sf_mant(a), mb = sf_mant(b), rem = ma, q = 0;
    for (int i = 0; i <= 26; i++) {   /* q = floor(ma * 2^26 / mb) */
        q <<= 1;
        if (rem >= mb) {
            rem -= mb;
            q |= 1;
        }
        rem <<= 1;
    }
    return sf_pack(q, sf_exp(a) - sf_exp(b) - 26, rem != 0);
}

static uint32_t sf_from_int(int v)   /* v >= 0 */
{
    return sf_pack((uint32_t)v, 0, 0);
}

static int sf_to_int(uint32_t f)     /* truncates toward zero, |f| < 2^31 */
{
    if (!(f & 0x7fffffffu))
        return 0;
    int e = sf_exp(f);
    uint32_t m = sf_mant(f);
    int v = e >= 0 ? (int)(m << e) : (e > -24 ? (int)(m >> -e) : 0);
    return f & 0x80000000u ? -v : v;
}

static uint32_t sf_twice(uint32_t f)
{
    return f ? f + (1u << 23) : 0;
}

static uint64_t isqrt64(uint64_t v, int *inexact)
{
    uint64_t r = 0, bit = 1ull << 62;
    while (bit > v)
        bit >>= 2;
    while (bit) {
        if (v >= r + bit) {
            v -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    *inexact = v != 0;
    return r;
}

/* (float)sqrt((double)r2): for r2 < 2^32 the double rounding never changes
 * the float result, so a direct correctly rounded sqrt gives the same bits. */
static uint32_t sf_sqrt_int(uint32_t r2)
{
    if (!r2)
        return 0;
    int k = 0, inexact;
    while (((uint64_t)r2 << (2 * k + 2)) < (1ull << 50))
        k++;
    uint64_t m = isqrt64((uint64_t)r2 << (2 * k), &inexact);
    return sf_pack(m, -k, inexact);
}

/* atan(2^-i) in Q61 for i <= 20; beyond that it is 2^(61-i) at this precision. */
static const int64_t atan_q61[21] = {
    1811004864519280711LL, 1069098597953152948LL, 564882337777596249LL,
    286743094836456889LL, 143927976672616092LL, 72034151524184357LL,
    36025865417378411LL, 18014032019027246LL, 9007153442175927LL,
    4503593900760542LL, 2251799097857775LL, 1125899817364151LL,
    562949942236502LL, 281474975312555LL, 140737488180565LL,
    70368744155819LL, 35184372086101LL, 17592186044075LL,
    8796093022165LL, 4398046511099LL, 2199023255551LL,
};

/* Stock: (int16)(int)((float)atan2((double)s14, (double)s12) * 1000.0f).
 * 64-bit CORDIC leaves an angle error far below the float rounding step.
 * Inputs are scaled by 2^44: |v| * gain (1.65) stays below 2^62. */
static int16_t tilt_of(uint16_t s12, int16_t s14)
{
    if (!s14)
        return 0;
    int64_t x = (int64_t)s12 * (1LL << 44), y = (int64_t)s14 * (1LL << 44), z = 0;
    for (int i = 0; i < 56; i++) {
        int64_t a = i <= 20 ? atan_q61[i] : (int64_t)(1ull << (61 - i));
        int64_t xs = x >> i, ys = y >> i;
        if (y > 0) {
            x += ys;
            y -= xs;
            z += a;
        } else {
            x -= ys;
            y += xs;
            z -= a;
        }
    }
    uint32_t theta = sf_pack((uint64_t)(z < 0 ? -z : z), -61, 0) | (z < 0 ? 0x80000000u : 0);
    return (int16_t)sf_to_int(sf_mul(theta, F_1000));
}

static int in_box(int v, int c, int tol)
{
    int d = v - c;
    return d >= -tol && d <= tol;
}

/* r < lim and r <= lim, with r = sqrt(r2). */
static int r_lt(const shifter_t *s, int lim)
{
    return lim > 0 && s->r2 < (int32_t)lim * lim;
}

static int r_le(const shifter_t *s, int lim)
{
    return lim >= 0 && s->r2 <= (int32_t)lim * lim;
}

void shifter_init(shifter_t *s)
{
    uint8_t *p = (uint8_t *)s;
    for (unsigned i = 0; i < sizeof *s; i++)
        p[i] = 0;
    s->deb_limit = 20;
    s->ref_r = F_1100;
    s->neutral_y = -50;
    s->seq_up_thr = 1800;
    s->seq_down_thr = -1800;
    s->match_y[2] = 1600;
    s->match_y[3] = -1600;
    s->color[0] = SH_DEFAULT_R;
    s->color[1] = SH_DEFAULT_G;
    s->color[2] = SH_DEFAULT_B;
    s->gain[0] = SH_DEFAULT_GAIN_R;
    s->gain[1] = SH_DEFAULT_GAIN_G;
    s->gain[2] = SH_DEFAULT_GAIN_B;
    for (int i = 0; i < 3; i++)
        s->led_rgb[i] = s->color[i];
    s->led_test = 0xff;
    s->sw_prev = 99;
    s->a_thr = 1;
    s->b_thr = 1;
    for (int i = 0; i < 13; i++)
        s->aux13[i] = 0xff;
}

void shifter_load(shifter_t *s, const uint16_t st[SH_SETTINGS_HALFWORDS])
{
    s->hb_x_rest = -320;
    s->hb_y_rest = -110;
    s->hb_x_pulled = -36;
    for (int k = 0; k < 8; k++) {
        s->gear_x[k] = default_gear_x[k];
        s->gear_y[k] = default_gear_y[k];
    }
    if (st[4] != 0xffff)
        s->mode = (uint8_t)st[4];
    if (st[0x29] < 2)
        s->aux_flag = (uint8_t)st[0x29];
    s->hb_y_pulled = -1500;

    if (st[0x63] == SH_SETTINGS_MAGIC) {
        s->hb_y_rest = (int16_t)st[5];
        s->hb_y_pulled = (int16_t)st[6];
        for (int k = 0; k < 8; k++) {
            s->gear_x[k] = s->match_x[k] = (int16_t)st[7 + 4 * k];
            s->gear_y[k] = s->match_y[k] = (int16_t)st[8 + 4 * k];
        }
        s->hb_x_rest = (int16_t)st[0x27];
        s->hb_x_pulled = (int16_t)st[0x28];
        for (int i = 0; i < 8; i++) {
            s->match_r[i] = (int16_t)st[0x37 + i];
            s->cfg[i] = (uint8_t)(s->match_r[i] / 8);
        }
        s->seq_up_thr = (int16_t)st[0x3f];
        s->seq_down_thr = (int16_t)st[0x40];
        if ((int16_t)st[6] < s->gear_y[3] - 300)
            s->hb_y_pulled = s->gear_y[3];
        s->settings_valid = 1;
    }
    /* Otherwise the matcher starts empty, as in stock; learn() fills it. */
    if (st[0x2a] != 0xffff && st[0x2a] != 0)
        for (int i = 0; i < 13; i++)
            s->aux13[i] = (uint8_t)st[0x2a + i];
    s->g1_left_of_g3 = s->gear_x[0] < s->gear_x[2];

    if (st[SH_ST_FLAGS] >> 8 == SH_ST_MARK) {
        s->options = (uint8_t)(st[SH_ST_FLAGS] & (SH_OPT_LOGO | SH_OPT_RANGE_TOGGLE | SH_OPT_GAMMA | SH_OPT_NEUTRAL));
        if (st[SH_ST_COLOR] <= 0xff && st[SH_ST_COLOR + 1] <= 0xff && st[SH_ST_COLOR + 2] <= 0xff)
            for (int i = 0; i < 3; i++)
                s->color[i] = (uint8_t)st[SH_ST_COLOR + i];
        if (st[SH_ST_GAIN] <= 0xff && st[SH_ST_GAIN + 1] <= 0xff && st[SH_ST_GAIN + 2] <= 0xff)
            for (int i = 0; i < 3; i++)
                s->gain[i] = (uint8_t)st[SH_ST_GAIN + i];
    }
    for (int i = 0; i < 3; i++)
        s->led_rgb[i] = s->color[i];
}

void shifter_save(const shifter_t *s, uint16_t st[SH_SETTINGS_HALFWORDS])
{
    st[4] = s->mode;
    st[5] = (uint16_t)s->hb_y_rest;
    st[6] = (uint16_t)s->hb_y_pulled;
    for (int k = 0; k < 8; k++) {
        st[7 + 4 * k] = (uint16_t)s->gear_x[k];
        st[8 + 4 * k] = (uint16_t)s->gear_y[k];
    }
    st[0x27] = (uint16_t)s->hb_x_rest;
    st[0x28] = (uint16_t)s->hb_x_pulled;
    st[0x29] = s->aux_flag;
    for (int i = 0; i < 13; i++)
        st[0x2a + i] = s->aux13[i];
    for (int i = 0; i < 8; i++)
        st[0x37 + i] = (uint16_t)s->match_r[i];
    st[0x3f] = (uint16_t)s->seq_up_thr;
    st[0x40] = (uint16_t)s->seq_down_thr;
    if (s->cal_bits == 0xff || s->settings_valid)
        st[0x63] = SH_SETTINGS_MAGIC;
    st[SH_ST_FLAGS] = (uint16_t)(SH_ST_MARK << 8 | s->options);
    for (int i = 0; i < 3; i++)
        st[SH_ST_COLOR + i] = s->color[i];
    for (int i = 0; i < 3; i++)
        st[SH_ST_GAIN + i] = s->gain[i];
}

/* trunc(s12 * 6.55 * q30 / 2^30); nested truncating division is exact for
 * positive divisors, which keeps this off 64-bit division helpers. */
static int32_t proj(uint16_t s12, int32_t q30)
{
    uint64_t m = (uint64_t)s12 * 655u * (uint32_t)(q30 < 0 ? -q30 : q30);
    int32_t v = (int32_t)((uint32_t)(m >> 30) / 100u);
    return q30 < 0 ? -v : v;
}

/* Sensor frame -> s12/s14/r/theta/x/y (stock 0x2574 first half). */
static void sensor_math(shifter_t *s, const uint8_t f[8])
{
    s->raw_t = (uint16_t)(f[1] << 8 | f[2]);
    s->raw_x = (uint16_t)(f[3] << 8 | f[4]);
    s->raw_y = (uint16_t)(f[5] << 8 | f[6]);
    /* Stock reads 2*popcount(0xF) = 8 bytes including the status byte, so the
     * Z low byte is never clocked in. Kept: calibration data depends on it. */
    s->raw_z = (uint16_t)(f[7] << 8);

    s->s12 = (uint16_t)(((uint64_t)s->raw_y * S12_MUL) >> 32);
    s->s14 = (int16_t)(((int32_t)f[7] - 128) * 1280 / 51);
    s->r2 = (int32_t)s->s12 * s->s12 + (int32_t)s->s14 * s->s14;
    s->r = (uint16_t)sh_isqrt((uint32_t)s->r2);
    s->rf = sf_sqrt_int((uint32_t)s->r2);
    s->tilt = tilt_of(s->s12, s->s14);

    /* theta = atan2(s14, s12), compared with 0.01f as signed float bits. */
    if (s->s14 <= 0) {
        s->theta_lt = s->theta_le = 1;
    } else if (s->s12 == 0) {
        s->theta_lt = s->theta_le = 0;
    } else {
        int64_t lhs = (int64_t)s->s14 << 40, rhs = (int64_t)s->s12 * TAN_T_Q40;
        s->theta_lt = lhs < rhs;
        s->theta_le = lhs <= rhs;
    }

    /* x = r cos(theta) sin(phi) 6.55, y = r cos(theta) cos(phi) 6.55,
     * r cos(theta) == s12. Truncation toward zero as in d2iz. */
    uint32_t ang = (uint32_t)(((uint64_t)s->raw_x * PHI_Q) >> 16);
    int32_t sn, cs;
    sh_sincos_q30(ang, &sn, &cs);
    int32_t x = proj(s->s12, sn);
    int32_t y = proj(s->s12, cs);
    if (x < -5000 || x > 5000)
        x = 0;
    if (y < -5000 || y > 5000)
        x = 0;  /* sic: stock clears x, not y */
    s->x = (int16_t)x;
    s->y = (int16_t)y;
    if (s->ref_r < s->rf)   /* non-negative floats order like their bits */
        s->ref_r = s->rf;
}

static int debounce(shifter_t *s)
{
    if (s->cand != s->deb_prev) {
        s->deb_prev = s->cand;
        s->deb_count = 0;
    } else if (s->deb_count < s->deb_limit) {
        s->deb_count++;
    }
    return s->deb_count >= s->deb_limit;
}

/* Neutral between two gears, in 2 ms samples: long enough for the host to
 * get it in a report of its own. */
#define SH_GEAR_GAP 5

static void release(shifter_t *s)
{
    s->latched = 0;
    s->cand = 0;
}

static void gear_search(shifter_t *s)
{
    for (int i = 0; i < 6; i++) {
        int slack = (i == 2 || i == 3) ? 100 : 50;
        if (in_box(s->x, s->match_x[i], 499) && in_box(s->y, s->match_y[i], 499) &&
            r_lt(s, s->match_r[i] + slack)) {
            s->cand = (uint8_t)(i + 1);
            s->latched = 1;
            break;
        }
    }
    for (int i = 6; i < 8; i++) {
        if (s->theta_lt && in_box(s->x, s->match_x[i], 249) && in_box(s->y, s->match_y[i], 99) &&
            r_lt(s, s->match_r[i] + 25)) {
            s->cand = (uint8_t)(i + 1);
            s->latched = 1;
        }
    }
}

static void decide_hpattern(shifter_t *s)
{
    s->handbrake = 0;
    s->seq_down = 0;
    s->seq_up = 0;
    if (!s->latched) {
        s->cand = 0;
        gear_search(s);
    } else if (!in_box(s->y, s->neutral_y, 699)) {
        gear_search(s);
    } else if (s->cand < 7) {
        release(s);
    } else {
        /* Gears 7/8 sit near neutral; hold them while still in their slot. */
        if (!(s->theta_le && r_le(s, s->match_r[6] + 30) && in_box(s->x, s->match_x[6], 250)) && s->cand == 7)
            release(s);
        if (!(s->theta_le && r_le(s, s->match_r[7] + 30) && in_box(s->x, s->match_x[7], 250)) && s->cand == 8)
            release(s);
        if (s->theta_lt && r_lt(s, s->match_r[6] + 25) && in_box(s->x, s->match_x[6], 499) &&
            in_box(s->y, s->match_y[6], 99) && s->cand == 8)
            release(s);
        if (s->theta_lt && r_lt(s, s->match_r[7] + 25) && in_box(s->x, s->match_x[7], 499) &&
            in_box(s->y, s->match_y[7], 99) && s->cand == 7)
            release(s);
    }
    if (debounce(s) && s->cand <= 8) {
        uint8_t g = s->cand ? (uint8_t)(1u << (s->cand - 1)) : 0;
        /* A lever moved through neutral faster than the debounce goes from
         * one gear straight to the next. Report neutral in between, so that
         * hosts see the old gear released before the new one is pressed:
         * Linux sends the changes in a report in button order, and a game
         * that selects neutral on release would end a downshift there. */
        if (s->gears && g && g != s->gears)
            s->gear_gap = SH_GEAR_GAP;
        if (s->gear_gap) {
            s->gear_gap--;
            g = 0;
        }
        s->gears = g;
    }
}

static void decide_sequential(shifter_t *s)
{
    s->handbrake = 0;
    s->gears = 0;
    if (s->y > s->seq_up_thr - 250)
        s->cand = 10;
    else if (s->y < s->seq_down_thr + 250)
        s->cand = 9;
    else if ((s->y < s->seq_up_thr - 500 && s->y > s->seq_down_thr + 500) || in_box(s->y, s->neutral_y, 199))
        s->cand = 0;
    if (!debounce(s))
        return;
    s->seq_up = s->cand == 10;
    s->seq_down = s->cand == 9;
}

static void decide_handbrake(shifter_t *s)
{
    int lo;
    if (s->settings_valid) {
        lo = (int16_t)(s->hb_y_rest - 300);
    } else {
        if (s->hb_y_pulled > s->y && !s->hb_pulled_cal)
            s->hb_y_pulled = s->y;
        lo = s->hb_rest_cal ? (int16_t)(s->hb_y_rest - 300) : (int16_t)(s->neutral_y - 400);
    }
    int hi = (int16_t)(s->hb_y_pulled + 100);
    s->hb_lo = (int16_t)lo;
    s->hb_hi = (int16_t)hi;
    s->gears = 0;
    s->seq_down = 0;
    s->seq_up = 0;

    int fy = s->y;
    if (fy <= hi)
        fy = hi;
    if (!(lo > fy))
        fy = lo;
    int span = lo - hi;
    /* Q12 fixed point stands in for the stock float maths (a float holds
     * ~8 fractional bits at this magnitude); 10 * 0xffff << 12 fits 32 bits. */
    uint32_t target = 0;
    if (span > 0) {
        uint32_t q = (uint32_t)(lo - fy) * 65535u;
        target = (q / (uint32_t)span) << 12 | ((q % (uint32_t)span) << 12) / (uint32_t)span;
    }
    uint32_t cur = (uint32_t)s->handbrake << 12;
    uint32_t diff = target > cur ? target - cur : cur - target;
    uint32_t out = diff > (8000u << 12) ? (cur * 4 + target * 6) / 10 : (cur + target * 9) / 10;
    uint32_t v = out >> 12;
    if (v >= 0xffe1)
        v = 0xffff;
    else if (v <= 0x1e)
        v = 0;
    s->handbrake = (uint16_t)v;
}

static int abs16(int v)
{
    return v < 0 ? -v : v;
}

/* Stock 0x1a44: neutral = the sample closest to zero on each axis. */
static void track_neutral(shifter_t *s)
{
    if (abs16(s->x) < abs16(s->neutral_x) || s->neutral_flags != 3) {
        s->neutral_x = s->x;
        s->neutral_flags |= 1;
    }
    if (abs16(s->y) < abs16(s->neutral_y) || s->neutral_flags != 3) {
        s->neutral_y = s->y;
        s->neutral_flags |= 2;
    }
}


/* rf < ref_r * k */
static int rf_below(const shifter_t *s, uint32_t k)
{
    return s->rf < sf_mul(s->ref_r, k);
}

/* Stock 0x1ab4: classify a steady pose by quadrant and radius, then store it
 * as that gear's match point. Gears with a stored radius only move to a
 * smaller one. The reference radius is the largest seen at neutral. */
static void learn(shifter_t *s)
{
    int x = s->x, y = s->y, g = -1;

    if (x > 1000 && y > 1000 && rf_below(s, F_0_65))
        g = 1;
    else if (x > 1000 && y < -1000 && rf_below(s, F_0_65))
        g = 2;
    else if (x >= -799 && x <= 799 && y > 1200 && rf_below(s, F_0_9))
        g = 3;
    else if (x >= -799 && x <= 799 && y < -1200 && rf_below(s, F_0_9))
        g = 4;
    else if (x < -1000 && y > 1000 && rf_below(s, F_0_6))
        g = 5;
    else if (x < -1000 && y < -1000 && rf_below(s, F_0_6))
        g = 6;
    else if (y > 80 && rf_below(s, F_0_24) && s->tilt < 10)
        g = 7;
    else if (y < -100 && rf_below(s, F_0_24) && s->tilt < 10)
        g = 8;
    else if (x >= -999 && x <= 999 && y >= -999 && y <= 999 && s->tilt > 1100)
        g = 0;

    if (g == 0) {
        s->neutral_r = (int16_t)sf_to_int(s->rf);
        s->ref_r = sf_from_int(s->neutral_r);
        track_neutral(s);
        return;
    }
    if (g < 0)
        return;

    unsigned i = (unsigned)g - 1;
    int16_t m = s->match_r[i];
    if (m != 0 && !(s->rf < sf_from_int(m)))
        return;
    if (m == 0) {
        uint32_t ref;
        if (g <= 2)
            ref = sf_twice(s->rf);
        else if (g <= 4)
            ref = sf_div(sf_mul(s->rf, F_10), F_9);
        else if (g <= 6)
            ref = sf_div(sf_mul(s->rf, F_10), F_6);
        else
            ref = sf_mul(s->rf, F_6);
        if (g == 8)
            s->neutral_y = (int16_t)(y + 250);
        if (s->ref_r < ref || s->ref_r == F_1100)
            s->ref_r = ref;
    }
    m = (int16_t)sf_to_int(s->rf);
    s->match_r[i] = m;
    s->match_x[i] = (int16_t)x;
    s->match_y[i] = (int16_t)y;
    s->cfg[i] = (uint8_t)(m / 8);
}

/* Stock 0x1f54: learn once the tilt has held within +-7 for 31 frames. */
static void learn_gate(shifter_t *s)
{
    int d = s->tilt - s->tilt_ref;
    if (d >= -7 && d <= 7) {
        if (++s->stable > 30) {
            learn(s);
            s->tilt_ref = s->tilt;
            s->stable = 0;
        }
    } else {
        s->stable = 0;
        s->tilt_ref = s->tilt;
    }
}

int shifter_sensor_frame(shifter_t *s, const uint8_t f[8])
{
    int res = SH_FRAME_OK;
    uint8_t any = 0;
    for (int i = 1; i < 8; i++)
        any |= f[i];
    if (!any) {
        if (s->empty_frames < 255)
            s->empty_frames++;
        res = s->empty_frames >= 100 ? SH_FRAME_POWER_OFF : SH_FRAME_EMPTY;
    } else {
        s->empty_frames = 0;
        sensor_math(s, f);
        switch (s->mode) {
        case SH_MODE_HPATTERN:   decide_hpattern(s); break;
        case SH_MODE_SEQUENTIAL: decide_sequential(s); break;
        case SH_MODE_HANDBRAKE:  decide_handbrake(s); break;
        default: break;
        }
    }
    /* Stock runs this after every read, empty or not (0x34e). */
    if (s->settings_valid)
        track_neutral(s);
    else
        learn_gate(s);
    return res;
}

/* Stock 0x0bc6. */
static unsigned mode_cycle(shifter_t *s)
{
    if (s->sw_h) {
        s->mode = SH_MODE_HPATTERN;
    } else {
        s->mode = (uint8_t)((s->mode + 1) % 3);
        if (s->mode == SH_MODE_HPATTERN)
            s->mode = SH_MODE_SEQUENTIAL;
    }
    return SH_ACT_LED | SH_ACT_SAVE;
}

unsigned shifter_buttons(shifter_t *s, int pb10_low, int pa5_low, int pa4_low)
{
    unsigned act = 0;
    uint8_t sw = pb10_low ? 1 : 0, b = pa5_low ? 1 : 0, a = pa4_low ? 1 : 0;

    if (sw != s->sw_prev) {
        s->sw_prev = sw;
        if (sw)
            s->mode = SH_MODE_HPATTERN;
        else if (s->mode != SH_MODE_HANDBRAKE)
            s->mode = SH_MODE_SEQUENTIAL;
        act |= SH_ACT_LED | SH_ACT_SAVE;
    }
    s->sw_h = sw;

    /* Handbrake button: a plain HID button with the switch in H, the
     * sequential/handbrake toggle (stock behaviour) with it in SEQ. */
    if (sw) {
        s->hb_button = b;
        s->b_count = 0;
    } else {
        s->hb_button = 0;
        if (s->btn_b && b) {
            s->b_count++;
        } else {
            if (s->b_count >= s->b_thr)
                act |= mode_cycle(s);
            s->b_count = 0;
        }
    }
    s->btn_b = b;

    /* High/low button in toggle mode: each press flips the latch. */
    if ((s->options & SH_OPT_RANGE_TOGGLE) && a && !s->a_prev) {
        s->range_on = !s->range_on;
        act |= SH_ACT_LED;
    }

    if (s->a_prev && a) {
        s->a_count++;
    } else {
        if (s->a_count >= s->a_thr) {
            s->aux_flag = !s->aux_flag;
            act |= SH_ACT_LED;
        }
        s->a_count = 0;
    }
    s->a_prev = a;
    s->btn_a = a;
    return act;
}

/* Stock 0x0684. */
static void calibrate(shifter_t *s, unsigned t)
{
    s->hb_x_rest = s->neutral_x;
    s->hb_y_rest = s->neutral_y;
    unsigned gear = 0;
    switch (t) {
    case 1: case 2: case 5: case 6: case 7: case 8:
        gear = t;
        break;
    case 3:
        if (s->mode == SH_MODE_SEQUENTIAL)
            s->seq_up_thr = s->y >= 900 ? s->y : 900;
        else if (s->mode == SH_MODE_HPATTERN)
            gear = 3;
        break;
    case 4:
        if (s->mode == SH_MODE_SEQUENTIAL)
            s->seq_down_thr = s->y <= -900 ? s->y : -900;
        else if (s->mode == SH_MODE_HPATTERN)
            gear = 4;
        break;
    case 9:
        s->hb_x_rest = s->neutral_x = s->x;
        s->hb_y_rest = s->neutral_y = s->y;
        s->hb_rest_cal = 1;
        break;
    case 10:
        s->hb_x_pulled = s->x;
        s->hb_y_pulled = s->y;
        s->hb_pulled_cal = 1;
        break;
    }
    if (gear) {
        unsigned i = gear - 1;
        s->gear_x[i] = s->match_x[i] = s->x;
        s->gear_y[i] = s->match_y[i] = s->y;
        s->match_r[i] = (int16_t)s->r;
        s->cal_bits |= (uint8_t)(1u << i);
    }
}

unsigned shifter_command(shifter_t *s, const uint8_t *buf, unsigned len)
{
    uint8_t b[64] = {0};
    if (len > sizeof b)
        len = sizeof b;
    for (unsigned i = 0; i < len; i++)
        b[i] = buf[i];
    if (len < 2 || b[0] != 0x24)
        return 0;

    switch (b[1]) {
    case 0x00:  /* enter bootloader */
        s->booting = 1;
        return SH_ACT_SAVE | SH_ACT_BOOT;
    case 0x20:  /* LED colour, not persisted */
        for (int i = 0; i < 3; i++)
            s->led_rgb[i] = b[6 + i];
        return SH_ACT_LED;
    case 0x21:  /* calibrate target 1..10 */
        if (b[2] < 1 || b[2] > 10)
            return 0;
        calibrate(s, b[2]);
        return SH_ACT_SAVE;
    case 0x22:
        return mode_cycle(s);
    case 0x23:
        for (int i = 0; i < 13; i++)
            s->aux13[i] = b[2 + i];
        return SH_ACT_SAVE;
    case 0x24:  /* reported per-gear radius byte; not persisted (stock) */
        if (b[2] >= 1 && b[2] <= 8)
            s->cfg[b[2] - 1] = b[3];
        return SH_ACT_SAVE;
    /* OpenShift commands */
    case 0x30:  /* LED colour R G B, saved */
        for (int i = 0; i < 3; i++)
            s->color[i] = s->led_rgb[i] = b[2 + i];
        return SH_ACT_LED | SH_ACT_SAVE;
    case 0x31:  /* logo LED: 0 off, 1 on; saved */
        if (b[2] > 1)
            return 0;
        s->options = (uint8_t)((s->options & ~SH_OPT_LOGO) | (b[2] ? SH_OPT_LOGO : 0));
        return SH_ACT_LED | SH_ACT_SAVE;
    case 0x32:  /* high/low button: 0 momentary, 1 toggle; saved */
        if (b[2] > 1)
            return 0;
        s->options = (uint8_t)((s->options & ~SH_OPT_RANGE_TOGGLE) | (b[2] ? SH_OPT_RANGE_TOGGLE : 0));
        s->range_on = 0;
        return SH_ACT_LED | SH_ACT_SAVE;
    case 0x33:  /* white balance gains R G B; saved when the next byte is 1 */
        if (b[5] > 1)
            return 0;
        for (int i = 0; i < 3; i++)
            s->gain[i] = b[2 + i];
        return SH_ACT_LED | (b[5] ? SH_ACT_SAVE : 0);
    case 0x34:  /* gamma correction: 1 on, 0 off (default); saved */
        if (b[2] > 1)
            return 0;
        s->options = (uint8_t)((s->options & ~SH_OPT_GAMMA) | (b[2] ? SH_OPT_GAMMA : 0));
        return SH_ACT_LED | SH_ACT_SAVE;
    case 0x35:  /* neutral button: 1 on, 0 off (default); saved */
        if (b[2] > 1)
            return 0;
        s->options = (uint8_t)((s->options & ~SH_OPT_NEUTRAL) | (b[2] ? SH_OPT_NEUTRAL : 0));
        return SH_ACT_SAVE;
    case 0x3f:  /* light only chain position 0..2 in white; 0xFF ends */
        if (b[2] > 2 && b[2] != 0xff)
            return 0;
        s->led_test = b[2];
        return SH_ACT_LED;
    default:
        return 0;
    }
}

void shifter_report1(const shifter_t *s, uint8_t out[5])
{
    out[0] = 0x01;
    out[1] = s->gears;
    uint8_t range = (s->options & SH_OPT_RANGE_TOGGLE) ? s->range_on : s->btn_a;
    /* Bit 4 is HID button 1: held in H-pattern while no gear is engaged, for
     * games that need a binding to select neutral. */
    uint8_t neutral = (s->options & SH_OPT_NEUTRAL) && s->mode == SH_MODE_HPATTERN && !s->gears;
    out[2] = (uint8_t)(s->seq_down + 2 * s->seq_up + 4 * range + 8 * s->hb_button + 16 * neutral);
    out[3] = (uint8_t)(s->handbrake & 0xff);
    out[4] = (uint8_t)(s->handbrake >> 8);
}

static void be16(uint8_t *p, int16_t v)
{
    p[0] = (uint8_t)((uint16_t)v >> 8);
    p[1] = (uint8_t)v;
}

void shifter_report36(const shifter_t *s, uint8_t out[64])
{
    static const uint8_t gear_at[8] = { 13, 19, 25, 31, 37, 43, 49, 55 };
    static const uint8_t cfg_at[8] = { 17, 29, 30, 35, 36, 41, 42, 47 };
    for (int i = 0; i < 64; i++)
        out[i] = 0;
    out[0] = 0x24;
    out[1] = 0x20;
    be16(&out[2], s->x);
    be16(&out[4], s->y);
    for (int i = 0; i < 3; i++)
        out[6 + i] = (uint8_t)s->led_rgb[i];
    be16(&out[9], s->hb_y_rest);
    be16(&out[11], s->hb_y_pulled);
    for (int k = 0; k < 8; k++) {
        be16(&out[gear_at[k]], s->gear_x[k]);
        be16(&out[gear_at[k] + 2], s->gear_y[k]);
        out[cfg_at[k]] = s->cfg[k];
    }
    out[18] = (uint8_t)(s->btn_a | s->btn_b << 1 | s->sw_h << 2 | (s->mode == SH_MODE_HANDBRAKE) << 3 |
                        s->seq_down << 4 | s->seq_up << 5);
    out[48] = (uint8_t)(s->r >> 3);
    /* OpenShift options; stock leaves this byte at 0. */
    out[23] = (uint8_t)(0x80 | s->options | s->range_on << 2);
    out[24] = s->gain[0];
    out[53] = s->gain[1];
    out[54] = s->gain[2];
    be16(&out[59], s->hb_x_rest);
    be16(&out[61], s->hb_x_pulled);
    out[63] = s->mode;
}

void shifter_uart_packet(const shifter_t *s, uint8_t out[16])
{
    for (int i = 0; i < 16; i++)
        out[i] = 0;
    out[0] = 0x24;
    out[1] = 0x20;
    out[2] = s->gears;
    out[3] = (uint8_t)(s->seq_down + 2 * s->seq_up + 4 * s->sw_h + 8 * (s->mode == SH_MODE_HANDBRAKE) +
                       16 * s->btn_a + 32 * s->btn_b);
    out[4] = (uint8_t)(s->handbrake >> 8);
    out[5] = (uint8_t)s->handbrake;
    for (int i = 0; i < 3; i++)
        out[6 + i] = (uint8_t)s->led_rgb[i];
    out[15] = 0x11;
}

/* round(255 * (i / 255)^2.2), at least 1 for i > 0, for SH_OPT_GAMMA. Off
 * by default: with it on, colours were hard to match by eye on these LEDs. */
static const uint8_t gamma22[256] = {
      0,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,   1,
      1,   1,   1,   1,   1,   1,   1,   1,   1,   2,   2,   2,   2,   2,   2,   2,
      3,   3,   3,   3,   3,   4,   4,   4,   4,   5,   5,   5,   5,   6,   6,   6,
      6,   7,   7,   7,   8,   8,   8,   9,   9,   9,  10,  10,  11,  11,  11,  12,
     12,  13,  13,  13,  14,  14,  15,  15,  16,  16,  17,  17,  18,  18,  19,  19,
     20,  20,  21,  22,  22,  23,  23,  24,  25,  25,  26,  26,  27,  28,  28,  29,
     30,  30,  31,  32,  33,  33,  34,  35,  35,  36,  37,  38,  39,  39,  40,  41,
     42,  43,  43,  44,  45,  46,  47,  48,  49,  49,  50,  51,  52,  53,  54,  55,
     56,  57,  58,  59,  60,  61,  62,  63,  64,  65,  66,  67,  68,  69,  70,  71,
     73,  74,  75,  76,  77,  78,  79,  81,  82,  83,  84,  85,  87,  88,  89,  90,
     91,  93,  94,  95,  97,  98,  99, 100, 102, 103, 105, 106, 107, 109, 110, 111,
    113, 114, 116, 117, 119, 120, 121, 123, 124, 126, 127, 129, 130, 132, 133, 135,
    137, 138, 140, 141, 143, 145, 146, 148, 149, 151, 153, 154, 156, 158, 159, 161,
    163, 165, 166, 168, 170, 172, 173, 175, 177, 179, 181, 182, 184, 186, 188, 190,
    192, 194, 196, 197, 199, 201, 203, 205, 207, 209, 211, 213, 215, 217, 219, 221,
    223, 225, 227, 229, 231, 234, 236, 238, 240, 242, 244, 246, 248, 251, 253, 255,
};

/* One channel: gamma if it is on, then the white balance gain, then stock's
 * brightness cap (stock divides each channel by 1.5f). */
static uint8_t led_level(const shifter_t *s, unsigned c, unsigned v)
{
    if (s->options & SH_OPT_GAMMA)
        v = gamma22[v & 0xff];
    v = v * s->gain[c] / 255;
    return (uint8_t)(v * 2 / 3);
}

static void led_put(const shifter_t *s, uint8_t out[9], unsigned i, unsigned r, unsigned g, unsigned b)
{
    out[3 * i + 0] = led_level(s, 1, g);
    out[3 * i + 1] = led_level(s, 0, r);
    out[3 * i + 2] = led_level(s, 2, b);
}

void shifter_led_grb(const shifter_t *s, int asleep, uint8_t out[9])
{
    unsigned r = s->led_rgb[0], g = s->led_rgb[1], b = s->led_rgb[2];
    for (int i = 0; i < 9; i++)
        out[i] = 0;
    if (asleep)
        return;
    if (s->booting) {
        for (unsigned i = 0; i < 3; i++)
            led_put(s, out, i, 0xff, 0xff, 0);
        return;
    }
    if (s->led_test < 3) {
        led_put(s, out, s->led_test, 0xff, 0xff, 0xff);
        return;
    }
    /* Handbrake button: the colour in H-pattern, off in sequential, green
     * while handbrake mode is on. */
    if (s->mode == SH_MODE_HANDBRAKE)
        led_put(s, out, SH_LED_HANDBRAKE, 0, 0xff, 0);
    else if (s->mode != SH_MODE_SEQUENTIAL)
        led_put(s, out, SH_LED_HANDBRAKE, r, g, b);
    /* High/low button: the colour as a button, green or red as a toggle. */
    if (!(s->options & SH_OPT_RANGE_TOGGLE))
        led_put(s, out, SH_LED_RANGE, r, g, b);
    else if (s->range_on)
        led_put(s, out, SH_LED_RANGE, 0, 0xff, 0);
    else
        led_put(s, out, SH_LED_RANGE, 0xff, 0, 0);
    if (s->options & SH_OPT_LOGO)
        led_put(s, out, SH_LED_LOGO, r, g, b);
}
