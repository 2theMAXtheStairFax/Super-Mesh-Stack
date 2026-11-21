#!/usr/bin/env python3
"""SparkFun Qwiic Navigation Switch (PRT-27576): 5-way joystick + RGB LED on a PCA9554 @ 0x20.

Two input modes:
  poll - read the input register every POLL_S seconds (4 wires: 3V3, GND, SDA, SCL)
  int  - sleep until the PCA9554's open-drain INT pin falls, then read (5th wire, pin 12)

In int mode a watchdog compares the input register against INT on every idle timeout:
if the buttons changed but INT never went low, the wire is missing and `int_dead` is set
so the caller can fall back to polling.
"""
import fcntl, os, time
import gpiod
from gpiod.line import Bias, Direction, Edge, Value

I2C_SLAVE = 0x0703  # ioctl: set the target address for read()/write() on /dev/i2c-N

class I2CDevice:
    """Plain /dev/i2c-N register access (no smbus2 needed)."""
    def __init__(self, bus, addr):
        self.fd = os.open(f'/dev/i2c-{bus}', os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, addr)

    def write(self, reg, val):
        os.write(self.fd, bytes([reg, val]))

    def read(self, reg):
        os.write(self.fd, bytes([reg]))
        return os.read(self.fd, 1)[0]

    def close(self):
        os.close(self.fd)

# PCA9554 IO bits (SparkFun hardware overview)
UP, DOWN, RIGHT, LEFT, CENTER = 0x01, 0x02, 0x04, 0x08, 0x10
BUTTONS = UP | DOWN | RIGHT | LEFT | CENTER
BLUE, GREEN, RED = 0x20, 0x40, 0x80        # LED channels, active low
YELLOW, WHITE = RED | GREEN, RED | GREEN | BLUE

REG_IN, REG_OUT, REG_POL, REG_CFG = 0, 1, 2, 3

POLL_S = 0.03       # poll interval
DEBOUNCE_S = 0.12   # ignore a new press this soon after the last one
REPEAT_DELAY = 0.45  # hold this long before auto-repeat starts
REPEAT_S = 0.12      # repeat interval...
REPEAT_FAST_S = 0.05  # ...dropping to this once held past FAST_AFTER
FAST_AFTER = 1.5


class NavSwitch:
    def __init__(self, bus=1, addr=0x20, int_chip='/dev/gpiochip0', int_line=17):   # Pi 3B harness: I2C1, INT on pin 11
        self.addr, self.int_chip, self.int_line = addr, int_chip, int_line
        self.dev = I2CDevice(bus, addr)
        try:
            self.dev.write(REG_POL, 0x00)
            self.dev.write(REG_OUT, 0xFF)       # LED off
            self.dev.write(REG_CFG, BUTTONS)    # 1 = input
            self.state = self._read()
        except OSError:                         # nothing answering at addr
            self.dev.close()
            raise
        self.last_press = 0.0
        self.int_req = None
        self.int_dead = False
        self._suspect = 0
        self.repeat_mask = 0        # buttons that auto-repeat while held (set by the UI)
        self.repeating = False      # last press returned was a repeat, not a new press
        self._hold_t, self._next_rep = None, 0.0

    def _read(self):
        """Bitmask of buttons held right now (inputs are pulled up, low = pressed)."""
        return ~self.dev.read(REG_IN) & BUTTONS

    def _update(self):
        now = self._read()
        pressed, self.state = now & ~self.state, now
        if pressed:
            t = time.monotonic()
            if t - self.last_press < DEBOUNCE_S:
                return 0
            self.last_press = t
        return pressed

    def led(self, color):
        """color: any mix of RED/GREEN/BLUE, 0 = off."""
        self.dev.write(REG_OUT, 0xFF & ~color)

    # ---- interrupt mode ----
    def int_enable(self):
        self.int_req = gpiod.request_lines(self.int_chip, consumer='navswitch', config={
            self.int_line: gpiod.LineSettings(direction=Direction.INPUT,
                                              edge_detection=Edge.FALLING, bias=Bias.PULL_UP)})
        self.state = self._read()           # reading clears any pending INT
        self.int_dead, self._suspect = False, 0

    def int_disable(self):
        if self.int_req:
            self.int_req.release()
            self.int_req = None

    def int_confirm(self, timeout=10):
        """Switch to int mode only if a press arrives through INT within `timeout` seconds."""
        self.int_enable()
        if self.int_req.wait_edge_events(timeout):
            self.int_req.read_edge_events()
            self._update()
            return True
        self.int_disable()
        return False

    def _int_watchdog(self):
        line_high = self.int_req.get_value(self.int_line) == Value.ACTIVE
        before = self.state
        pressed = self._update()
        if line_high and self.state != before:      # inputs changed, INT stayed high
            self._suspect += 1
            self.int_dead = self._suspect >= 2
        return pressed

    # ---- both modes ----
    def _repeat(self, pressed):
        """Turn a held repeat_mask button into a stream of presses, speeding up the longer it's held."""
        held = self.state & self.repeat_mask
        t = time.monotonic()
        if pressed or not held:
            self._hold_t = t if held else None
            self._next_rep = t + REPEAT_DELAY
            self.repeating = False
            return pressed
        if self._hold_t is None:
            self._hold_t, self._next_rep = t, t + REPEAT_DELAY
        if t < self._next_rep:
            return 0
        self._next_rep = t + (REPEAT_FAST_S if t - self._hold_t > FAST_AFTER else REPEAT_S)
        self.repeating = True
        return held

    def wait(self, timeout):
        """Block for up to `timeout` s (poll mode: at most POLL_S); return newly pressed buttons."""
        if self.int_req and not self.state & self.repeat_mask:   # holding: no edges come, so poll
            if self.int_req.wait_edge_events(timeout):
                self.int_req.read_edge_events()
                self._suspect = 0
                return self._repeat(self._update())
            return self._int_watchdog()
        time.sleep(min(timeout, POLL_S))
        return self._repeat(self._update())

    def close(self):
        try:
            self.led(0)
        except OSError:
            pass
        self.int_disable()
        self.dev.close()
