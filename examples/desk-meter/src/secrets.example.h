// Copy this to src/secrets.h (gitignored) and fill in. secrets.h is NEVER committed.
#pragma once

#define WIFI_SSID        "your-2.4GHz-ssid"   // ESP32 classic = 2.4 GHz only
#define WIFI_PASS        "your-wifi-password"

// Address of the machine running `pitwall usage serve`. Give it a fixed address.
#define AGGREGATOR_URL   "http://192.0.2.10:8848/usage"

// The value of PITWALL_AGENTS_USAGE_TOKEN on that machine.
#define AGGREGATOR_BEARER ""
