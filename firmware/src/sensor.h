/* OpenShift: 3-axis magnetometer on a bit-banged SPI bus. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFT_SENSOR_H
#define OPENSHIFT_SENSOR_H

#include <stdint.h>

/* Power, register setup and burst mode start. Blocking,
 * ~190 ms; call from thread mode only. */
void sensor_init(void);
/* Power the sensor down and float the bus. */
void sensor_power_off(void);
/* Read-measurement frame: status, T, X, Y, Z-hi. */
void sensor_read(uint8_t frame[8]);

#endif
