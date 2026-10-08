// submeter firmware — the meter app (HANDOFF §8 / §11.7) + polish.
//
// WiFi -> HTTP GET aggregator /usage every ~45s -> parse (ArduinoJson) into a retained provider
// array -> render. Two views: OVERVIEW (one card per provider) and DETAIL (tap a card for a full
// screen of that provider; tap again to go back). Calibrated resistive touch drives paging.
// Status LED reflects the WORST provider: this board's RGB red channel (GPIO4) is dead, so we use
// the two working channels — green=ok, cyan=warn, blinking blue=error. Fail-soft: keep last data on
// a fetch error and flag STALE. Direct TFT_eSPI drawing (no PSRAM; static readout).

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>
#include "secrets.h"

static TFT_eSPI tft = TFT_eSPI();

// touch calibration for this panel @ rotation 0 (from §11.6 calibrateTouch)
static const uint16_t TOUCH_CAL[5] = {263, 3547, 293, 3586, 4};

static const int W = 320, H = 480;
static const int HEADER_H = 38;
static const int CARD_H = 86;
static const uint32_t POLL_MS = 45000;

static const uint16_t COL_BG = TFT_BLACK, COL_TEXT = TFT_WHITE;
static const uint16_t COL_DIM = 0x8410, COL_TRACK = 0x2104; // COL_DIM now ONLY for divider lines + muted "stale" status — never text
static const uint16_t COL_LIMIT = TFT_VIOLET; // "limit" = quota exhausted (WEEKLY FULL) — violet, NOT alarm-red (red is for real failures)

// --- working RGB-LED channels (red/GPIO4 is dead on this SKU; green=16, blue=17, active-low) ---
#define LED_G 16
#define LED_B 17
#define LED_ACTIVE_LOW 1
static const bool LED_ENABLED = false; // user: back-of-board RGB LED disabled (kept code for re-enable)

struct Prov {
  char label[20];
  char tier[24];
  char status[8];
  char account[4];     // "G" / "LA"; empty for single-account providers
  int sPct, wPct;      // -1 = window absent
  long sReset, wReset; // minutes, -1 = unknown
  float sRate, wRate;  // %/min burn, NAN = unknown
  long sEta, wEta;     // minutes to cap at current rate, -1 = none (flat/falling)
  int hist[24];        // recent 5h % samples (sparkline)
  int histN;
  char extra[96];
};
static Prov provs[7];
static int provCount = 0;
static int worstRank = 0;        // 0 ok, 1 stale, 2 warn, 3 error
static bool haveData = false;
static bool staleFlag = false;

struct Strip { int a; int b; }; // b = -1 for a single-account strip
static Strip strips[7];
static int stripCount = 0;

// group ADJACENT rows that share a label (the aggregator emits a provider's accounts back-to-back)
static void buildStrips() {
  stripCount = 0;
  for (int i = 0; i < provCount && stripCount < 7; ) {
    if (i + 1 < provCount && strcmp(provs[i].label, provs[i + 1].label) == 0) {
      strips[stripCount++] = { i, i + 1 }; i += 2;
    } else {
      strips[stripCount++] = { i, -1 }; i += 1;
    }
  }
}

enum View { OVERVIEW, DETAIL };
static View view = OVERVIEW;
static int detailIdx = 0;
static uint32_t lastPoll = 0;
static bool wasTouched = false;

// ---- helpers --------------------------------------------------------------
static uint16_t statusColor(const char *s) {
  if (!strcmp(s, "ok")) return TFT_GREEN;
  if (!strcmp(s, "warn")) return TFT_ORANGE;
  if (!strcmp(s, "limit")) return COL_LIMIT; // quota exhausted — violet, distinct from warn/error
  if (!strcmp(s, "error")) return TFT_RED;
  return COL_DIM;
}
static int statusRank(const char *s) {
  if (!strcmp(s, "error")) return 3;     // a FAILED poll is the most actionable -> worst
  if (!strcmp(s, "limit")) return 2;     // exhausted quota: notable but expected (with warn)
  if (!strcmp(s, "warn")) return 2;
  if (!strcmp(s, "stale")) return 1;
  return 0;
}
// Status text for the detail line. "limit" (quota exhausted) reads as WEEKLY FULL or 5H FULL by which
// window maxed; everything else is just upper-cased. (sPct/wPct are percent-used; -1 = window absent.)
static String statusText(const Prov &p) {
  if (!strcmp(p.status, "limit")) return (p.wPct >= 100 || p.sPct < 100) ? "WEEKLY FULL" : "5H FULL";
  String s = p.status; s.toUpperCase();
  return s;
}
static String fmtReset(long m) {
  if (m < 0) return "";
  if (m < 60) return String(m) + "m";
  if (m < 1440) return String(m / 60) + "h" + String(m % 60) + "m";
  return String(m / 1440) + "d" + String((m % 1440) / 60) + "h";
}

// true when a window will hit 100% before it resets (burning faster than it refills)
static bool willExceed(long etaMin, long resetMin) {
  return etaMin >= 0 && (resetMin < 0 || etaMin < resetMin);
}

// line chart of recent samples, AUTO-SCALED to the data's own min..max so small trends are visible
// (5h % usually sits in a narrow band; scaling to 0-100 made it a flat line). Caller shows the range.
static void drawSparkline(int x, int y, int w, int h, const int *v, int n, uint16_t col) {
  tft.drawRect(x, y, w, h, COL_TRACK);
  if (n < 2) return;
  int lo = v[0], hi = v[0];
  for (int i = 1; i < n; i++) {
    if (v[i] < lo) lo = v[i];
    if (v[i] > hi) hi = v[i];
  }
  int range = hi - lo;
  if (range < 1) range = 1; // flat series -> draw near mid, avoid div-by-zero
  const int pad = 2; // px inset top/bottom
  for (int i = 1; i < n; i++) {
    int x0 = x + (w * (i - 1)) / (n - 1), x1 = x + (w * i) / (n - 1);
    int y0 = y + h - 1 - pad - ((h - 1 - 2 * pad) * (v[i - 1] - lo)) / range;
    int y1 = y + h - 1 - pad - ((h - 1 - 2 * pad) * (v[i] - lo)) / range;
    tft.drawLine(x0, y0, x1, y1, col);
  }
}

// ---- LED ------------------------------------------------------------------
static void ledInit() { pinMode(LED_G, OUTPUT); pinMode(LED_B, OUTPUT); }
static void ledWrite(bool g, bool b) {
  digitalWrite(LED_G, LED_ACTIVE_LOW ? !g : g);
  digitalWrite(LED_B, LED_ACTIVE_LOW ? !b : b);
}
static void ledTick() {
  if (!LED_ENABLED) { ledWrite(false, false); return; } // disabled -> hold both channels off
  if (!haveData) { ledWrite(false, false); return; }
  if (worstRank >= 3) { bool on = (millis() / 400) % 2; ledWrite(false, on); } // blink blue = error
  else if (worstRank == 2) ledWrite(true, true);   // cyan = warn
  else if (worstRank == 1) ledWrite(false, true);  // blue = stale
  else ledWrite(true, false);                      // green = ok
}

// ---- drawing --------------------------------------------------------------
static const char *TZ_POSIX = "EST5EDT,M3.2.0,M11.1.0"; // US Eastern; edit for your zone

// HH:MM from the device clock (synced from the aggregator's `updated` each poll; no NTP needed)
static String clockStr() {
  time_t now = time(nullptr);
  if (now < 1700000000) return "--:--"; // not synced yet
  struct tm tmv;
  localtime_r(&now, &tmv);
  char buf[6];
  snprintf(buf, sizeof(buf), "%02d:%02d", tmv.tm_hour, tmv.tm_min);
  return String(buf);
}

static void header() {
  // in-place redraw (no full-rect blank) to avoid flicker
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(2);
  tft.drawString("AI USAGE", 8, 10);

  // center: connection / freshness indicator (clear just its region so it can appear/clear cleanly)
  tft.fillRect(118, 8, 116, 18, COL_BG);
  if (WiFi.status() != WL_CONNECTED) {
    tft.setTextDatum(MC_DATUM); tft.setTextSize(1); tft.setTextColor(TFT_RED, COL_BG);
    tft.drawString("OFFLINE", W / 2 + 10, 16);
  } else if (staleFlag) {
    tft.setTextDatum(MC_DATUM); tft.setTextSize(1); tft.setTextColor(TFT_ORANGE, COL_BG);
    tft.drawString("STALE", W / 2 + 10, 16);
  }

  // right: live clock
  tft.setTextDatum(TR_DATUM);
  tft.setTextSize(2);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString(clockStr(), W - 8, 8);
  tft.drawFastHLine(0, HEADER_H - 1, W, COL_DIM);
}

static void bar(int x, int y, int w, int h, int pct, long reset, uint16_t col, const char *tag) {
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString(tag, x, y + (h - 8) / 2);
  const int bx = x + 22, bw = w - 22;
  tft.fillRoundRect(bx, y, bw, h, 3, COL_TRACK);
  if (pct >= 0) {
    int fw = (bw * (pct > 100 ? 100 : pct)) / 100;
    if (fw > 0) tft.fillRoundRect(bx, y, fw, h, 3, col);
    String lbl = String(pct) + "%";
    String rs = fmtReset(reset);
    if (rs.length()) lbl += " " + rs;
    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(COL_TEXT, COL_TRACK);
    tft.drawString(lbl, bx + bw - 4, y + (h - 8) / 2);
  } else {
    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(COL_TEXT, COL_TRACK);
    tft.drawString("n/a", bx + bw - 4, y + (h - 8) / 2);
  }
  tft.setTextDatum(TL_DATUM); // restore for callers (avoids right-aligned label leak)
}

static void drawCard(int i, int y) {
  const Prov &p = provs[i];
  uint16_t sc = statusColor(p.status);
  // no full-card blank (would flicker); redraw elements in place. Bars repaint their full width,
  // text is drawn with a bg so it overwrites cleanly, and the warning ▲ region is cleared below.
  tft.fillRect(0, y + 4, 5, CARD_H - 8, sc);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(2);
  tft.drawString(p.label, 14, y + 6);
  tft.setTextSize(1);
  tft.setTextColor(sc, COL_BG);
  tft.drawString(p.tier[0] ? p.tier : p.status, 14, y + 26);
  bar(14, y + 40, W - 24, 16, p.sPct, p.sReset, sc, "5h");
  bar(14, y + 62, W - 24, 16, p.wPct, p.wReset, sc, "7d");
  // pace warning: red ▲ top-right if a window will hit its cap before it resets.
  // clear its box first so it disappears (no ghost) when the condition clears.
  tft.fillRect(W - 22, y + 4, 20, 16, COL_BG);
  if (willExceed(p.sEta, p.sReset) || willExceed(p.wEta, p.wReset)) {
    tft.fillTriangle(W - 20, y + 18, W - 6, y + 18, W - 13, y + 5, TFT_RED);
  }
  tft.drawFastHLine(0, y + CARD_H - 1, W, COL_TRACK);
}

// one column of a split strip: status stripe + "Label TAG" + tier + half-width 5h/7d bars
static void drawHalf(int hx, int hw, int y, const Prov &p) {
  uint16_t sc = statusColor(p.status);
  tft.fillRect(hx, y + 4, 4, CARD_H - 8, sc);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(2);
  String head = String(p.label);
  if (p.account[0]) { head += " "; head += p.account; }
  tft.drawString(head.substring(0, (hw - 12) / 12), hx + 8, y + 6);
  tft.setTextSize(1);
  tft.setTextColor(sc, COL_BG);
  tft.drawString(p.tier[0] ? p.tier : p.status, hx + 8, y + 26);
  bar(hx + 8, y + 40, hw - 14, 14, p.sPct, p.sReset, sc, "5h");
  bar(hx + 8, y + 60, hw - 14, 14, p.wPct, p.wReset, sc, "7d");
}

static void drawCardSplit(int a, int b, int y) {
  const int half = W / 2;
  drawHalf(0, half, y, provs[a]);
  drawHalf(half, half, y, provs[b]);
  tft.drawFastVLine(half, y + 6, CARD_H - 12, COL_DIM); // center divider
  tft.drawFastHLine(0, y + CARD_H - 1, W, COL_TRACK);
}

static void renderOverview(bool full) {
  if (full) tft.fillScreen(COL_BG); // full clear only on view change; a poll refresh redraws in place
  header();
  for (int s = 0; s < stripCount && s < 5; s++) {
    int y = HEADER_H + s * CARD_H;
    if (strips[s].b < 0) drawCard(strips[s].a, y);
    else drawCardSplit(strips[s].a, strips[s].b, y);
  }
}

// word-wrap `s` within maxW px at the CURRENT font/size, drawing each line at x and advancing y by
// lineH; stops before yMax so it never spills past the page. A token wider than the line is hard-
// broken. Used for error/note detail lines so a long string (e.g. "error: /api/oauth/usage HTTP 429")
// shows in full across lines instead of clipping mid-word.
static void drawWrapped(const String &s, int x, int &y, int maxW, int lineH, int yMax) {
  String line = "";
  int i = 0, n = s.length();
  while (i < n && y < yMax) {
    int sp = s.indexOf(' ', i);
    String word = (sp < 0) ? s.substring(i) : s.substring(i, sp);
    i = (sp < 0) ? n : sp + 1;
    while (tft.textWidth(word) > maxW && word.length() > 1) {   // hard-break an over-long token
      if (line.length()) { tft.drawString(line, x, y); y += lineH; line = ""; if (y >= yMax) return; }
      int cut = word.length();
      while (cut > 1 && tft.textWidth(word.substring(0, cut)) > maxW) cut--;
      tft.drawString(word.substring(0, cut), x, y); y += lineH; if (y >= yMax) return;
      word = word.substring(cut);
    }
    String trial = line.length() ? line + " " + word : word;
    if (tft.textWidth(trial) <= maxW) {
      line = trial;
    } else {
      if (line.length()) { tft.drawString(line, x, y); y += lineH; if (y >= yMax) return; }
      line = word;
    }
  }
  if (line.length() && y < yMax) { tft.drawString(line, x, y); y += lineH; }
}

static void renderDetail(int i) {
  const Prov &p = provs[i];
  uint16_t sc = statusColor(p.status);
  tft.fillScreen(COL_BG);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(1);
  tft.drawString("< tap anywhere to go back", 8, 14);
  tft.drawFastHLine(0, HEADER_H - 1, W, COL_DIM);

  int y = HEADER_H + 12;
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(3);
  { String t = String(p.label); if (p.account[0]) { t += " "; t += p.account; } tft.drawString(t, 12, y); }
  y += 40;

  tft.setTextSize(2);
  tft.setTextColor(sc, COL_BG);
  { String st = statusText(p);
    String line = p.tier[0] ? (String(p.tier) + " - " + st) : st; // tight separator: "WEEKLY FULL" fits the 26-char line
    tft.drawString(line, 12, y); }
  y += 36;

  // 5-hour window
  tft.setTextSize(1);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString("5-HOUR WINDOW", 12, y); y += 12;
  bar(12, y, W - 24, 22, p.sPct, p.sReset, sc, "5h"); y += 32;

  // weekly / cycle window
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString("WEEKLY / CYCLE", 12, y); y += 12;
  bar(12, y, W - 24, 22, p.wPct, p.wReset, sc, "7d"); y += 34;

  // PACE — burn rate + ETA-to-cap (red when it will exhaust before reset)
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString("PACE", 12, y); y += 12;
  tft.setTextSize(2);
  {
    bool warn = willExceed(p.sEta, p.sReset);
    String l = "5h " + (isnan(p.sRate) ? String("--") : String(p.sRate, 1) + "%/m");
    if (p.sEta >= 0) l += "  cap " + fmtReset(p.sEta) + (warn ? "!" : "");
    tft.setTextColor(warn ? TFT_RED : COL_TEXT, COL_BG);
    tft.drawString(l.substring(0, 26), 12, y); y += 22;
  }
  {
    bool warn = willExceed(p.wEta, p.wReset);
    String l = "7d " + (isnan(p.wRate) ? String("--") : String(p.wRate, 2) + "%/m");
    if (p.wEta >= 0) l += "  cap " + fmtReset(p.wEta) + (warn ? "!" : "");
    tft.setTextColor(warn ? TFT_RED : COL_TEXT, COL_BG);
    tft.drawString(l.substring(0, 26), 12, y); y += 24;
  }
  // hide the trend for exhausted ("limit") or failed ("error") rows — the 5h sparkline is flat/
  // meaningless there and only invited the "same line across accounts" confusion.
  bool noTrend = !strcmp(p.status, "limit") || !strcmp(p.status, "error");
  if (p.histN >= 2 && !noTrend) {
    int lo = p.hist[0], hi = p.hist[0];
    for (int k = 1; k < p.histN; k++) { if (p.hist[k] < lo) lo = p.hist[k]; if (p.hist[k] > hi) hi = p.hist[k]; }
    tft.setTextDatum(TL_DATUM);
    tft.setTextSize(1);
    tft.setTextColor(COL_TEXT, COL_BG);
    tft.drawString("5h TREND  " + String(lo) + "-" + String(hi) + "%", 12, y); y += 12;
    drawSparkline(12, y, W - 24, 32, p.hist, p.histN, sc); y += 40;
  }

  // extra metrics — the reason to open detail. *_pct keys render as labeled bars (like the windows);
  // non-numeric extras (model, note) render as text. (parse "k=v  k=v")
  tft.setTextDatum(TL_DATUM);
  tft.setTextSize(1);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString("DETAILS", 12, y); y += 14;
  if (p.extra[0]) {
    String ex = p.extra;
    int start = 0;
    while (start < (int)ex.length() && y < H - 26) {
      int sep = ex.indexOf("  ", start);
      String tok = (sep < 0) ? ex.substring(start) : ex.substring(start, sep);
      int eq = tok.indexOf('=');
      String k = eq < 0 ? tok : tok.substring(0, eq);
      String v = eq < 0 ? "" : tok.substring(eq + 1);
      if (k.indexOf("pct") >= 0) {            // a percentage -> bar
        String label = k; label.replace("_pct", ""); label.replace("_", " ");
        tft.setTextSize(1); tft.setTextColor(COL_TEXT, COL_BG);
        tft.drawString(label, 12, y); y += 12;
        bar(12, y, W - 24, 18, v.toInt(), -1, sc, ""); y += 26;
      } else {                                // string -> word-wrapped text line(s)
        String label = k; label.replace("_", " ");
        String text = v.length() ? label + ": " + v : label;
        tft.setTextSize(2); tft.setTextColor(COL_TEXT, COL_BG);
        drawWrapped(text, 12, y, W - 24, 22, H - 26); // wraps instead of clipping at 26 chars
      }
      tft.setTextDatum(TL_DATUM);
      if (sep < 0) break;
      start = sep + 2;
    }
  } else {
    tft.setTextSize(2);
    tft.setTextColor(COL_TEXT, COL_BG);
    tft.drawString("(no extra metrics)", 12, y);
  }

  // page-indicator dots: which provider you're viewing
  for (int k = 0; k < provCount; k++) {
    int dx = W / 2 - (provCount * 14) / 2 + k * 14 + 7;
    tft.fillCircle(dx, H - 12, k == detailIdx ? 4 : 3, k == detailIdx ? COL_TEXT : COL_TRACK);
  }
}

static void render(bool full) {
  if (view == DETAIL && detailIdx < provCount) {
    if (full) renderDetail(detailIdx); // detail re-renders on tap only, not every poll (avoids flicker)
  } else {
    renderOverview(full);
  }
}

static void banner(const char *msg, uint16_t col) {
  tft.fillRect(0, 0, W, HEADER_H, COL_BG);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(col, COL_BG);
  tft.setTextSize(2);
  tft.drawString(msg, 8, 10);
}

// ---- fetch ----------------------------------------------------------------
static bool fetchUsage() {
  if (WiFi.status() != WL_CONNECTED) return false;
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, AGGREGATOR_URL)) return false;
  http.setConnectTimeout(3000);
  http.setTimeout(4000); // on-LAN; a 10s timeout just freezes touch when the network hiccups
  if (strlen(AGGREGATOR_BEARER) > 0) http.addHeader("Authorization", String("Bearer ") + AGGREGATOR_BEARER);
  int code = http.GET();
  if (code != 200) { http.end(); Serial.printf("usage GET %d\n", code); return false; }

  // stream-parse straight from the socket (no whole-body heap String): the no-PSRAM ESP32 fragments
  // and crashes over days with http.getString(); this is the dominant long-uptime stability fix.
  JsonDocument doc;
  DeserializationError jerr = deserializeJson(doc, http.getStream());
  http.end();
  if (jerr) { Serial.printf("json err: %s\n", jerr.c_str()); return false; }
  JsonArray arr = doc["providers"].as<JsonArray>();

  // sync the device clock from the server's `updated` (unix UTC, stamped at request time)
  uint32_t updated = doc["updated"] | 0;
  if (updated > 1700000000UL) {
    struct timeval tv;
    tv.tv_sec = (time_t)updated;
    tv.tv_usec = 0;
    settimeofday(&tv, nullptr);
  }

  int i = 0, worst = 0;
  for (JsonObject p : arr) {
    if (i >= 7) break;
    Prov &d = provs[i];
    strlcpy(d.label, p["label"] | (p["name"] | "?"), sizeof(d.label));
    strlcpy(d.tier, p["tier"] | "", sizeof(d.tier));
    strlcpy(d.status, p["status"] | "stale", sizeof(d.status));
    strlcpy(d.account, p["account"] | "", sizeof(d.account));
    d.sPct = p["s_pct"].isNull() ? -1 : (int)(p["s_pct"] | 0);
    d.wPct = p["w_pct"].isNull() ? -1 : (int)(p["w_pct"] | 0);
    d.sReset = p["s_reset_min"].isNull() ? -1 : (long)(p["s_reset_min"] | -1);
    d.wReset = p["w_reset_min"].isNull() ? -1 : (long)(p["w_reset_min"] | -1);
    d.sRate = p["s_rate"].isNull() ? NAN : (float)(p["s_rate"] | 0.0f);
    d.wRate = p["w_rate"].isNull() ? NAN : (float)(p["w_rate"] | 0.0f);
    d.sEta = p["s_eta_min"].isNull() ? -1 : (long)(p["s_eta_min"] | -1);
    d.wEta = p["w_eta_min"].isNull() ? -1 : (long)(p["w_eta_min"] | -1);
    d.histN = 0;
    for (JsonVariant hv : p["s_hist"].as<JsonArray>()) {
      if (d.histN < 24) d.hist[d.histN++] = hv.as<int>();
    }
    String es;
    for (JsonPair kv : p["extra"].as<JsonObject>()) {
      if (es.length()) es += "  ";
      es += kv.key().c_str();
      es += "=";
      es += kv.value().as<String>();
    }
    strlcpy(d.extra, es.c_str(), sizeof(d.extra));
    if (statusRank(d.status) > worst) worst = statusRank(d.status);
    i++;
  }
  provCount = i;
  buildStrips();
  worstRank = worst;
  haveData = true;
  staleFlag = false;
  return true;
}

// ---- touch ----------------------------------------------------------------
static void handleTouch() {
  uint16_t x, y;
  bool t = tft.getTouch(&x, &y);
  if (t && !wasTouched) {            // press edge
    if (view == OVERVIEW) {
      if (y >= HEADER_H) {
        int s = (y - HEADER_H) / CARD_H;
        if (s >= 0 && s < stripCount) {
          int idx = strips[s].a;
          if (strips[s].b >= 0 && x >= W / 2) idx = strips[s].b; // right column = 2nd account
          view = DETAIL; detailIdx = idx; render(true);
        }
      }
    } else {
      view = OVERVIEW;
      render(true);
    }
  }
  wasTouched = t;
}

// ---- lifecycle ------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nsubmeter meter app (§8/§11.7) + touch paging");
  setenv("TZ", TZ_POSIX, 1);
  tzset(); // clock is synced from the aggregator's `updated` each poll; TZ makes it local
  tft.init();
  tft.setRotation(0);
  tft.setTouch((uint16_t *)TOUCH_CAL);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  ledInit();

  tft.fillScreen(COL_BG);
  banner("WiFi...", COL_TEXT);
  tft.setTextSize(1);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextDatum(TL_DATUM);
  tft.drawString(WIFI_SSID, 8, HEADER_H + 8);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(300); Serial.print("."); }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK ");
    Serial.println(WiFi.localIP());
    banner("loading...", COL_TEXT);
    lastPoll = millis() - POLL_MS;
  } else {
    banner("WiFi FAILED", TFT_RED);
  }
}

void loop() {
  // self-heal WiFi: a dropped link otherwise freezes the meter on STALE until a power cycle
  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect >= 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }

  // belt-and-suspenders vs multi-day heap fragmentation on this no-PSRAM part: reboot once a day
  // (only when not mid-touch). millis() rolls at ~49.7d; this fires long before that.
  if (millis() > 86400000UL && !wasTouched) ESP.restart();

  // tick the header clock ~once a minute (cheap header-only redraw, not a full re-render)
  static int lastMin = -1;
  time_t tnow = time(nullptr);
  if (tnow > 1700000000) {
    struct tm tmv;
    localtime_r(&tnow, &tmv);
    if (tmv.tm_min != lastMin) {
      lastMin = tmv.tm_min;
      if (view == OVERVIEW && haveData) header();
    }
  }

  static bool firstPaint = true; // first successful paint clears the boot banner; rest redraw in place
  if (millis() - lastPoll >= POLL_MS) {
    lastPoll = millis();
    bool ok = fetchUsage();
    if (ok) { render(firstPaint); firstPaint = false; }
    else if (haveData) { staleFlag = true; if (view == OVERVIEW) header(); }
    else banner("no data", TFT_RED);
  }
  handleTouch();
  ledTick();
  delay(30);
}
