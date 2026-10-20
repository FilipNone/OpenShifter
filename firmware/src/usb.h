/* OpenShift: USB full-speed HID device. SPDX-License-Identifier: MIT */
#ifndef OPENSHIFT_USB_H
#define OPENSHIFT_USB_H

#include <stdint.h>

#define USB_CMD_LEN 64

extern volatile uint8_t  usb_configured;
extern volatile uint8_t  usb_suspended;
extern volatile uint8_t  usb_ever_configured;
extern volatile uint16_t usb_reset_count;

/* Drop off the bus, wait, come back. Blocking. */
void usb_reconnect(void);
void usb_init(void);
void usb_irq_handler(void);

/* Interrupt IN endpoint 1. */
int  usb_in_ready(void);
void usb_in_send(const uint8_t *p, unsigned len);

/* HID OUT reports (EP1 OUT or SET_REPORT), oldest first. Returns length or 0. */
unsigned usb_cmd_pop(uint8_t out[USB_CMD_LEN]);

/* Supplied by the application for GET_REPORT; returns length or 0. */
unsigned usb_get_report(uint8_t report_id, uint8_t *out);

/* Exposed for tests. */
extern const uint8_t usb_device_desc[18];
extern const uint8_t usb_config_desc[41];
extern const uint8_t usb_report_desc[83];

#endif
