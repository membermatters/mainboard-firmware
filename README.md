# mainboard-firmware

The MemberMatters mainboard firmware for door access control.

## Warning

This software is in Alpha. It may be used with caution, but may occasionally have bugs & breaking changes.

## Compatibility

This is tested on the BMS (Brisbane Makerspace) Bravo controller board. These boards are in the prototype stage and the design will be eventually published once they reach a stable design verison. However, any ESP32 based board with enough IO should be compatible, you may just have to do the wiring yourself and update the pin definitions!

We currently support the following configuration:

- Any ESP32 based controller
- Micropython v1.19.1

## Getting Started

The first step is to install Micropython onto your ESP32 board. A full tutorial on how to do this outside the scope of this document, but some quick steps for reference are included below.

0. [Install esptool.py](https://pypi.org/project/esptool/) and download the [latest version of Micropython](https://micropython.org/download/esp32/) for your ESP32 board.

1. Plug in your ESP32 board using a USB serial adapter.

2. Before you flash Micropython, or update it's version, you should erase the flash memory.

   Notes: this will erase the offline cache of authorised swipe cards and configuration changes made to `config.py`. Your port may be different, check your operating system (ie `ls /dev/`) to locate your specific serial port.

   ```bash
   esptool.py --chip esp32 --port /dev/tty.usbserial-1110 erase_flash
   ```

3. Flash the Micropython binary you downloaded earlier by using:
   ```bash
   esptool.py --chip esp32 --port /dev/tty.usbserial-1110 --baud 115200 write_flash -z 0x1000 esp32-20220618-v1.19.1.bin
   ```
   Note: you can use a faster baudrate, but some boards have upload reliability problems at faster speeds.
4. Copy `config.py.example` to `config.py` and update it with the correct config for your setup then flash the software to your board using the VS Code plugin "pymakr". Alternatively, use another tool like "ampy".
