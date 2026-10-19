/* Host build of the shifter core for tests/test_shifter.py.
 * Includes the source so the file-local float helpers can be exercised. */
#include "shifter.c"

uint32_t t_sf_mul(uint32_t a, uint32_t b) { return sf_mul(a, b); }
uint32_t t_sf_div(uint32_t a, uint32_t b) { return sf_div(a, b); }
uint32_t t_sf_from_int(int v) { return sf_from_int(v); }
int t_sf_to_int(uint32_t f) { return sf_to_int(f); }
uint32_t t_sf_sqrt_int(uint32_t r2) { return sf_sqrt_int(r2); }
int t_tilt_of(uint16_t s12, int16_t s14) { return tilt_of(s12, s14); }

unsigned t_sizeof(void) { return sizeof(shifter_t); }

void t_xy(const shifter_t *s, int *x, int *y, int *r2)
{
    *x = s->x;
    *y = s->y;
    *r2 = s->r2;
}

void t_match(const shifter_t *s, int i, int *x, int *y, int *r)
{
    *x = s->match_x[i];
    *y = s->match_y[i];
    *r = s->match_r[i];
}

int t_settings_valid(const shifter_t *s) { return s->settings_valid; }
