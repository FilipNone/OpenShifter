# OpenShift firmware. Needs clang + lld (LLVM >= 15); no libc, no libgcc.
VERSION ?= 0.5.0
BUILD   := build
TARGET  := $(BUILD)/openshift
SRCS    := $(wildcard src/*.c)
OBJS    := $(SRCS:src/%.c=$(BUILD)/%.o)

CC      := clang
OBJCOPY := llvm-objcopy
SIZE    := llvm-size
ARCH    := --target=thumbv7m-none-eabi -mcpu=cortex-m3 -mthumb -mfloat-abi=soft
CFLAGS  := $(ARCH) -std=c11 -Os -g -ffreestanding -fno-builtin -nostdlib \
           -ffunction-sections -fdata-sections -fno-common \
           -Wall -Wextra -Werror -Wno-unused-function -Wno-initializer-overrides \
           -DOPENSHIFT_VERSION='"$(VERSION)"'
LDFLAGS := $(ARCH) -fuse-ld=lld -nostdlib -static -Wl,-T,linker.ld \
           -Wl,--gc-sections -Wl,-Map,$(TARGET).map

PYTHON  ?= python3
FLASHER := $(PYTHON) ../tools/openshift_flash.py

.PHONY: all clean flash flash-stock size test
all: $(TARGET).bin size

$(BUILD)/%.o: src/%.c src/*.h Makefile | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(TARGET).elf: $(OBJS) linker.ld
	$(CC) $(LDFLAGS) $(OBJS) -o $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

size: $(TARGET).elf
	@$(SIZE) $<

$(BUILD):
	mkdir -p $@

# Puts the shifter in update mode and writes the image (asks for confirmation).
flash: $(TARGET).bin
	$(FLASHER) flash $<

# Restores a vendor image, e.g. make flash-stock STOCK=VelocityOneTuner_Shift_Firmware.bin
flash-stock:
	$(FLASHER) flash --allow-foreign $(STOCK)

# Host, emulator and flasher tests (pip install -r ../requirements-dev.txt).
test: $(TARGET).bin
	cd .. && $(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf $(BUILD)
