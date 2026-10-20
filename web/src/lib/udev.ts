// SPDX-License-Identifier: MIT
// The same rule as UDEV_RULE in tools/openshift_flash.py; lib.test.ts checks
// that the two match.
export const UDEV_RULE = `# /etc/udev/rules.d/70-openshift.rules
# VelocityOne Multi-Shift: application (7096) and vendor bootloader (7097).
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="10f5", ATTRS{idProduct}=="7096", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="10f5", ATTRS{idProduct}=="7097", TAG+="uaccess"
`
