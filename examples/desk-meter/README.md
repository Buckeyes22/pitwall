# Desk meter

Firmware for a 4-inch ESP32 touch display that shows subscription usage. It polls
`pitwall usage serve` over WiFi and draws one strip per plan.

This is an example. It is not built or tested in CI. Comments in the sources that cite
"HANDOFF" sections refer to the design notes of the project this firmware came from.

## Board (verified)

| Attribute | Value |
|---|---|
| Board family | ESP32-3248S035 ("Cheap Yellow Display"), 4.0" variant |
| MCU | ESP32-WROOM-32E: classic LX6, no PSRAM, 4 MB flash |
| Display | ST7796S 320x480 on HSPI, 40 MHz |
| Touch | XPT2046 resistive, sharing the display HSPI bus (SCLK 14 / MOSI 13 / MISO 12); only CS 33 and IRQ 36 are separate. Calibration `{263, 3547, 293, 3586, 4}` at `setRotation(0)`, set in `src/meter.cpp` and `src/touch_test.cpp` |
| Backlight | GPIO 27 (21 is the 2.8" ILI9341 variant) |
| USB bridge | CH340, which appears as `/dev/ttyUSB0` |
| RGB LED | red GPIO 4 does not work on this model; green 16 and blue 17 are active-low. `LED_ENABLED = false` in `src/meter.cpp` |

Do not add a second SPI bus for touch and do not add a `TOUCH_*` pin block. `platformio.ini`
passes only `-DTOUCH_CS=33` because the display library's touch driver shares the display bus.

## Prerequisites

- A USB-C data cable. A board that powers on but never shows a serial port usually has a
  charge-only cable.
- PlatformIO Core: `uv tool install platformio --with pip`.
- Membership of the `dialout` group for USB serial access.

## Find the port

```bash
pio device list
ls /dev/ttyUSB* /dev/ttyACM*
```

Expect `/dev/ttyUSB0`. The CH340 driver is in the kernel.

## Environments

| Env | Source | Proves |
|---|---|---|
| `display` | `src/main.cpp` | the panel: cycles red, green, blue and prints `ST7796 OK 320x480` |
| `touch` | `src/touch_test.cpp` | calibrated touch: prints coordinates and draws a dot under the stylus |
| `meter` | `src/meter.cpp` | the app: WiFi, poll `/usage` every 45 s, draw the strips |
| `ledsweep` | `src/ledsweep.cpp` | maps RGB LED pins; only needed for a different board model |

```bash
cd examples/desk-meter
pio run -e display -t upload --upload-port /dev/ttyUSB0
pio run -e touch   -t upload --upload-port /dev/ttyUSB0
pio run -e meter   -t upload --upload-port /dev/ttyUSB0
```

Run `display` and `touch` first on a new unit.

## Configure `secrets.h` before flashing `meter`

`src/meter.cpp` includes `secrets.h`, so the `meter` environment does not compile without it. The
file is ignored by git and must never be committed.

```bash
cd examples/desk-meter
cp src/secrets.example.h src/secrets.h
```

| Macro | Value |
|---|---|
| `WIFI_SSID` | a 2.4 GHz network; this ESP32 cannot join 5 GHz |
| `WIFI_PASS` | its password |
| `AGGREGATOR_URL` | `http://<address of the machine running usage serve>:8848/usage` |
| `AGGREGATOR_BEARER` | the value of `PITWALL_AGENTS_USAGE_TOKEN` on that machine |

WiFi and the address are compiled in. Changing either means editing `src/secrets.h` and flashing
again. Give the serving machine a fixed address; if its address changes the meter shows STALE.

The serving machine must listen on an address the meter can reach, which needs the token:

```bash
PITWALL_AGENTS_USAGE_TOKEN=<token> pitwall usage serve --host 0.0.0.0
```

## First flash of the meter

```bash
pio run -e meter -t upload --upload-port /dev/ttyUSB0
pio device monitor -b 115200
```

If the upload cannot connect, hold **BOOT**, tap **RESET**, release RESET, release BOOT, and run
the upload again. If uploads fail part-way, lower the speed:
`pio run -e meter -t upload -t upload_speed=460800 --upload-port /dev/ttyUSB0`.

## If the screen stays black

| Symptom | Cause | Fix |
|---|---|---|
| Whole screen dark | wrong `TFT_BL` | try 27, then 21 |
| Backlight on, no image | wrong `TFT_DC` or `TFT_CS`, or `USE_HSPI_PORT` missing | check the pins in `platformio.ini` |
| Colours inverted | wrong inversion flag | add `-DTFT_INVERSION_ON` or `-DTFT_INVERSION_OFF` to `build_flags` |
| Noisy image | SPI too fast | lower `-DSPI_FREQUENCY` to `27000000` |
| Nothing uploads | cable or port | cable first, then `--upload-port` |

## What a healthy boot looks like

1. The screen shows `WiFi...` with the network name.
2. Serial prints `WiFi OK <ip>`. On failure the screen shows `WiFi FAILED` in red.
3. The overview draws: an `AI USAGE` header, one strip per plan with 5-hour and 7-day bars, and a
   clock. Two accounts of one plan share a split strip, each half tagged with its account.

Two behaviours are by design:

- The clock is set from the payload's `updated` field on every poll. There is no NTP.
- The device restarts once a day to clear heap fragmentation.

`usage GET <code>` on serial is a poll that did not return 200. `json err: <reason>` is a payload
that did not parse. Both leave the last good data on screen, marked STALE.

## Limits the payload respects

The firmware holds 7 rows, 3-character account tags, and five statuses. `GET /usage` sends no more
than that. See `docs/agents/usage.md`.
