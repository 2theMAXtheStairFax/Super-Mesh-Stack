#!/usr/bin/env python3
"""ST7735S driver for the 0.96" 80x160 IPS module on SPI0 (spidev + gpiod v2 + Pillow)."""
import time, spidev, gpiod
try:
    import lgpio            # software PWM for the backlight (brightness)
except ImportError:
    lgpio = None
import numpy as np
from gpiod.line import Direction, Value
from PIL import Image

# MADCTL bits
MY, MX, MV, BGR = 0x80, 0x40, 0x20, 0x08

class ST7735:
    def __init__(self, bus=0, device=0, hz=16_000_000,
                 chip='/dev/gpiochip0', dc=25, rst=24, bl=18,  # Pi 3B harness: pin 22 DC, 18 RES, 12 BLK, 24 CS0
                 landscape=True, flip=False, xoff=24, yoff=0,
                 invert=False, bgr=True):
        self.dc, self.rst, self.bl = dc, rst, bl
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = hz
        self.spi.mode = 0
        self.brightness = 100
        self._lg = None
        lines = (dc, rst, bl)
        if lgpio is not None:   # backlight on lgpio so it can be dimmed with PWM
            try:
                self._lg = lgpio.gpiochip_open(int(chip.rstrip('/').split('gpiochip')[-1]))
                lgpio.gpio_claim_output(self._lg, bl, 0)
                lines = (dc, rst)
            except Exception:
                self._lg = None
        self.req = gpiod.Chip(chip).request_lines(consumer="st7735",
            config={lines: gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.INACTIVE)})

        self.landscape, self.bgr = landscape, bgr
        self._xoff0, self._yoff0 = xoff, yoff
        self._invert = invert
        self._init(self._orient(flip), invert)
        self.backlight(True)

    def _orient(self, flip):
        """Set size/offsets for the orientation and return its MADCTL value.

        Panel is natively 80 wide x 160 tall; landscape swaps axes and offsets.
        flip = rotate 180 degrees. Offsets stay the same because 24/0 centre the
        80x160 panel in the controller's 128x160 RAM, so mirroring doesn't move it.
        """
        self.flip = flip
        if self.landscape:
            self.width, self.height = 160, 80
            self.xoff, self.yoff = self._yoff0, self._xoff0
            madctl = MV | (MX if flip else MY)
        else:
            self.width, self.height = 80, 160
            self.xoff, self.yoff = self._xoff0, self._yoff0
            madctl = 0 if flip else (MX | MY)
        return madctl | (BGR if self.bgr else 0)

    def reinit(self):
        """Full panel reset + init (recovers a panel that lost power or state)."""
        self._init(self._orient(self.flip), self._invert)

    def set_flip(self, flip):
        """Rotate the picture 180 degrees on the fly (takes effect on the next show())."""
        self._cmd(0x36, self._orient(flip))                    # MADCTL

    def _set(self, line, val):
        self.req.set_value(line, Value.ACTIVE if val else Value.INACTIVE)

    def _cmd(self, c, *data):
        self._set(self.dc, 0); self.spi.writebytes([c])
        if data:
            self._set(self.dc, 1); self.spi.writebytes(list(data))

    def _init(self, madctl, invert):
        self._set(self.rst, 1); time.sleep(0.01)
        self._set(self.rst, 0); time.sleep(0.01)
        self._set(self.rst, 1); time.sleep(0.12)
        self._cmd(0x01); time.sleep(0.15)                      # SWRESET
        self._cmd(0x11); time.sleep(0.12)                      # SLPOUT
        self._cmd(0xB1, 0x01, 0x2C, 0x2D)                      # FRMCTR1
        self._cmd(0xB2, 0x01, 0x2C, 0x2D)                      # FRMCTR2
        self._cmd(0xB3, 0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)    # FRMCTR3
        self._cmd(0xB4, 0x07)                                  # INVCTR
        self._cmd(0xC0, 0xA2, 0x02, 0x84)                      # PWCTR1
        self._cmd(0xC1, 0xC5)                                  # PWCTR2
        self._cmd(0xC2, 0x0A, 0x00)                            # PWCTR3
        self._cmd(0xC3, 0x8A, 0x2A)                            # PWCTR4
        self._cmd(0xC4, 0x8A, 0xEE)                            # PWCTR5
        self._cmd(0xC5, 0x0E)                                  # VMCTR1
        self._cmd(0x21 if invert else 0x20)                    # INVON / INVOFF
        self._cmd(0x36, madctl)                                # MADCTL
        self._cmd(0x3A, 0x05)                                  # COLMOD 16-bit
        self._cmd(0xE0, 0x02, 0x1C, 0x07, 0x12, 0x37, 0x32, 0x29, 0x2D,
                        0x29, 0x25, 0x2B, 0x39, 0x00, 0x01, 0x03, 0x10)
        self._cmd(0xE1, 0x03, 0x1D, 0x07, 0x06, 0x2E, 0x2C, 0x29, 0x2D,
                        0x2E, 0x2E, 0x37, 0x3F, 0x00, 0x00, 0x02, 0x10)
        self._cmd(0x13); time.sleep(0.01)                      # NORON
        self._cmd(0x29); time.sleep(0.1)                       # DISPON

    def backlight(self, on):
        self._bl_on = on
        if self._lg is not None:
            lgpio.tx_pwm(self._lg, self.bl, 800, self.brightness if on else 0)   # 0/100 = steady
        else:
            self._set(self.bl, on)

    def set_brightness(self, pct):
        self.brightness = max(5, min(100, int(pct)))
        self.backlight(getattr(self, '_bl_on', True))

    def show(self, img):
        """Push a PIL image (width x height) to the panel."""
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))
        a = np.asarray(img.convert('RGB'), dtype=np.uint16)
        v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
        buf = v.astype('>u2').tobytes()                          # RGB565 big-endian
        x0, y0 = self.xoff, self.yoff
        x1, y1 = x0 + self.width - 1, y0 + self.height - 1
        self._cmd(0x2A, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)  # CASET
        self._cmd(0x2B, y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)  # RASET
        self._cmd(0x2C)                                          # RAMWR
        self._set(self.dc, 1)
        self.spi.writebytes2(buf)

    def close(self):
        self.backlight(False)
        self.spi.close()
        self.req.release()
        if self._lg is not None:
            lgpio.gpiochip_close(self._lg)
