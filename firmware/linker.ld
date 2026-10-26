/* OpenShifter: application image, started by the vendor bootloader.
 * 0x08000000-0x0800EFFF bootloader (untouched), 0x0800F000 settings page,
 * 0x08010000 application (the stock image is linked at the same address). */
ENTRY(reset_handler)

MEMORY
{
    FLASH  (rx)  : ORIGIN = 0x08010000, LENGTH = 64K
    RAM    (rwx) : ORIGIN = 0x20000000, LENGTH = 16K   /* top 4 KB: stack */
}

_stack_top = 0x20005000;   /* 20 KB part */

SECTIONS
{
    .text : {
        KEEP(*(.vectors))
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH

    .ARM.exidx : { *(.ARM.exidx*) } > FLASH

    .data : {
        _data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        _data_end = .;
    } > RAM AT > FLASH
    _data_load = LOADADDR(.data);

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > RAM

    _image_end = _data_load + SIZEOF(.data);
    ASSERT(_image_end <= 0x08020000, "image too large")
    ASSERT(_bss_end <= 0x20004000, "RAM overflow")

    /DISCARD/ : { *(.ARM.attributes) *(.comment) }
}
