#!/usr/bin/env python3
"""
Mesh Stack - battery monitor (Waveshare UPS HAT (D), 21700 pack).

Reads the HAT's own onboard INA219 directly over I2C at its fixed address
0x43 (NOT the INA219 default 0x40 - this board doesn't expose the sense
pins for external wiring, so there's nothing to reconfigure). Calibration
constants below are Waveshare's own, ported from their UPS_HAT_D demo code
(https://www.waveshare.com/wiki/UPS_HAT_(D)) - they're specific to this
board's shunt resistor and must match exactly for current/power to mean
anything; voltage is unaffected by calibration but current/power read as
garbage without it.

Sign convention: current is reported positive while discharging, negative
while charging - this is the raw INA219 register value as-is on this board
(confirmed empirically: no sign flip needed, unlike Waveshare's own demo
which negates it for their own display). Matches what meshbot.py's
push_ext_batt() already expects for its charge-detection threshold.

Polls pack voltage/current, logs to telemetry.sqlite, writes power.json for
the screen/web/meshbot (ext.batt push), warns on the mesh once when low, and
powers the Pi down cleanly on a sustained dip. Before shutting down it also
arms the HAT's separate boot-control MCU (0x2d) to wake the Pi automatically
once a charger is plugged back in, via the same register Waveshare's own
demo uses.
"""
import json
import os
import sqlite3
import subprocess
import time

I2C_ADDR = int(os.environ.get("SMS_INA219_ADDR", "0x43"), 16)
I2C_BUS = int(os.environ.get("SMS_INA219_BUS", "1"))
BOOT_MCU_ADDR = int(os.environ.get("SMS_UPS_BOOT_MCU_ADDR", "0x2d"), 16)
ARM_WAKE_ON_CHARGE = os.environ.get("SMS_ARM_WAKE_ON_CHARGE", "1") == "1"
DB_PATH = os.environ.get("SMS_DB", "/opt/mesh-stack/telemetry.sqlite")
POWER_JSON = os.environ.get("SMS_POWER_JSON", "/opt/mesh-stack/power.json")

POLL_SEC = int(os.environ.get("SMS_POWER_POLL", "15"))
LOG_EVERY = max(1, int(os.environ.get("SMS_POWER_LOG_EVERY", "4")))   # * POLL_SEC
WARN_V = float(os.environ.get("SMS_BATT_WARN_V", "3.6"))
CRIT_V = float(os.environ.get("SMS_BATT_CRIT_V", "3.4"))
FLOOR_V = float(os.environ.get("SMS_BATT_FLOOR_V", "3.3"))            # immediate
CRIT_COUNT = int(os.environ.get("SMS_BATT_CRIT_COUNT", "3"))          # consecutive
RETRY_SEC = 15

# --- Waveshare UPS HAT (D) INA219 register map + calibration -----------
# Ported from their demo (INA219.py, set_calibration_16V_5A) - these values
# are board-specific (shunt resistor dependent), not INA219 generic defaults.
_REG_CONFIG = 0x00
_REG_SHUNTVOLTAGE = 0x01
_REG_BUSVOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

_CAL_VALUE = 26868
_CURRENT_LSB_MA = 0.1524
_POWER_LSB_W = 0.003048
# RANGE_16V<<13 | GAIN_DIV_2_80MV<<11 | BADC_12BIT_32S<<7 | SADC_12BIT_32S<<3 | MODE_SANDBVOLT_CONTINUOUS
_CONFIG_VALUE = (0x00 << 13) | (0x01 << 11) | (0x0D << 7) | (0x0D << 3) | 0x07


class Ina219:
    def __init__(self, bus, addr):
        self.bus = bus
        self.addr = addr

    def _read16(self, reg):
        data = self.bus.read_i2c_block_data(self.addr, reg, 2)
        return (data[0] << 8) | data[1]

    def _write16(self, reg, value):
        self.bus.write_i2c_block_data(self.addr, reg, [(value >> 8) & 0xFF, value & 0xFF])

    def _signed(self, raw):
        return raw - 65536 if raw > 32767 else raw

    def configure(self):
        self._write16(_REG_CONFIG, _CONFIG_VALUE)
        self._write16(_REG_CALIBRATION, _CAL_VALUE)

    def bus_voltage(self):
        self._write16(_REG_CALIBRATION, _CAL_VALUE)   # re-arm cal (Waveshare re-writes it before every read)
        self._read16(_REG_BUSVOLTAGE)                  # first read after a config/cal write is stale
        return (self._read16(_REG_BUSVOLTAGE) >> 3) * 0.004

    def shunt_voltage_mv(self):
        self._write16(_REG_CALIBRATION, _CAL_VALUE)
        return self._signed(self._read16(_REG_SHUNTVOLTAGE)) * 0.01

    def current_ma(self):
        # Empirically positive = discharging, negative = charging on this
        # board (confirmed live: raw register reads positive while running
        # off the pack with no charger on the HAT) - no sign flip needed,
        # despite Waveshare's own demo negating it for their own display.
        return self._signed(self._read16(_REG_CURRENT)) * _CURRENT_LSB_MA

    def power_w(self):
        self._write16(_REG_CALIBRATION, _CAL_VALUE)
        return self._signed(self._read16(_REG_POWER)) * _POWER_LSB_W


def open_ina():
    import smbus2
    bus = smbus2.SMBus(I2C_BUS)
    dev = Ina219(bus, I2C_ADDR)
    dev.configure()
    dev.bus_voltage()   # force a bus transaction so a missing chip fails here
    return dev


def arm_wake_on_charge():
    """Tell the HAT's boot-control MCU to pull the Pi's wake pin once a
    charger is plugged back in, so it powers back on unattended. Same
    mechanism/register Waveshare's own demo uses before its poweroff."""
    if not ARM_WAKE_ON_CHARGE:
        return
    try:
        subprocess.run(
            ["i2cset", "-y", str(I2C_BUS), hex(BOOT_MCU_ADDR), "0x01", "0x55"],
            check=True, capture_output=True, timeout=5,
        )
        print("[power] armed wake-on-charge via boot MCU", flush=True)
    except Exception as e:
        print(f"[power] could not arm wake-on-charge (non-fatal): {e}", flush=True)


def db():
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.execute(
        """CREATE TABLE IF NOT EXISTS power (
               ts INTEGER, volts REAL, milliamps REAL
           )"""
    )
    c.commit()
    return c


def poweroff(reason: str):
    print(f"[power] {reason} - shutting down", flush=True)
    arm_wake_on_charge()
    subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)


def queue_mesh(conn, text: str):
    target = os.environ.get("SMS_WEB_SEND_TARGET", "")
    if target == "":
        return
    conn.execute(
        "INSERT INTO outbox (ts, target, text) VALUES (?,?,?)",
        (int(time.time()), target, text[:180]),
    )
    conn.commit()


def main():
    ina = None
    while ina is None:
        try:
            ina = open_ina()
        except Exception as e:
            print(f"[power] waiting for INA219 at {hex(I2C_ADDR)}: {e}", flush=True)
            time.sleep(RETRY_SEC)
    print(f"[power] INA219 found at {hex(I2C_ADDR)} (Waveshare UPS HAT (D))", flush=True)

    conn = db()
    warned = False
    crit_streak = 0
    i = 0
    while True:
        try:
            v = ina.bus_voltage()
            try:
                ma = ina.current_ma()
            except Exception:
                ma = 0.0
        except Exception as e:
            print(f"[power] read failed: {e}", flush=True)
            time.sleep(RETRY_SEC)
            continue

        try:
            with open(POWER_JSON, "w") as f:
                json.dump({"ts": int(time.time()), "volts": round(v, 3),
                           "milliamps": round(ma, 1)}, f)
        except OSError:
            pass

        if i % LOG_EVERY == 0:
            conn.execute("INSERT INTO power (ts, volts, milliamps) VALUES (?,?,?)",
                         (int(time.time()), round(v, 3), round(ma, 1)))
            conn.commit()
        i += 1

        if ma < 20:          # charging (negative on this HAT) or idle: the pack is recovering
            crit_streak = 0
        elif v <= FLOOR_V:
            poweroff(f"pack {v:.2f}V at/below floor {FLOOR_V}V")
            return
        elif v <= CRIT_V:
            crit_streak += 1
            print(f"[power] {v:.2f}V <= crit {CRIT_V}V ({crit_streak}/{CRIT_COUNT})", flush=True)
            if crit_streak >= CRIT_COUNT:
                poweroff(f"pack {v:.2f}V below {CRIT_V}V for {crit_streak} reads")
                return
        else:
            crit_streak = 0

        if v <= WARN_V and not warned:
            warned = True
            msg = f"Mesh Stack battery low: {v:.2f}V"
            print(f"[power] {msg}", flush=True)
            queue_mesh(conn, msg)
        elif v > WARN_V + 0.1:
            warned = False

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
