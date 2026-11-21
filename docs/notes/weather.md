# Weather station integration

The bot answers `<prefix> wx` (channel) or `wx` (DM) from a local cache, with no cloud API.
The cache is filled from a weather station node on the mesh (`SMS_WX_NODE`, or the Config tab).

## Sources

1. **Telemetry request** to the station contact. The reply is Cayenne LPP: temperature,
   humidity, barometer, illuminance and wind direction by type; voltage, current and power
   for the station battery; analog input channel 2 = wind mph, 3 = rain rate in/h,
   4 = rain total in.
2. **Text reply** from a station bot, parsed for the numbers. Example line:
   `✈: 76F | 71%💧 | 💨 6MPH SW ☔ 0.00in | ⚡12.7V @12:27/6m`. The `@HH:MM` stamp is read in
   `SMS_STATION_TZ` (defaults to `SMS_TZ`).

## Cache schema

`SMS_WEATHER_DB` (default `/opt/mesh-stack/weather.sqlite`), table `readings`:

```sql
CREATE TABLE readings (
  recv_time INTEGER PRIMARY KEY,
  temp_c REAL, temp_f REAL, humidity REAL, dewpoint_c REAL, dewpoint_f REAL,
  pressure_hpa REAL, pressure_inhg REAL, light_lux REAL,
  wind_speed_mph REAL, rain_rate_iph REAL, rain_total_in REAL,
  battery_voltage REAL, battery_current REAL, battery_power REAL,
  timestamp REAL,
  wind_direction TEXT
);
```

Values are stored raw. Readings older than `WEATHER_MAX_AGE_SEC` (30 min) are reported as stale.

## Settings

| Key | Meaning |
|---|---|
| `wx_node` | contact name or key prefix of the station |
| `wx_poll_min` | minutes between telemetry polls (default 10) |
| `cmd_prefix` | word the station bot and this node answer to |
