#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <time.h>
#if __has_include("logiclatch_config.h")
#include "logiclatch_config.h"
#else
#include "config.example.h"
#endif

// PLC reads remain on the ESP32. Python and cloud receive independent HTTP uploads.
constexpr uint16_t REGISTER_COUNT = 15;
constexpr uint32_t POLL_MS = 1000, UPLOAD_MS = 5000, MAX_BACKOFF_MS = 60000;
constexpr size_t MAX_QUEUED = 120;
WiFiClient modbus;
Preferences memory;
uint32_t bootId = 0, sequence = 0, dropped = 0;
uint32_t lastPoll = 0, lastUpload = 0, lastWiFi = 0, lastReplay = 0, lastCheckpoint = 0;
uint32_t sampledMs = 0;
uint16_t registers[REGISTER_COUNT] = {}, transaction = 0, lastFault = 0;
bool plcConnected = false, storageReady = false, urgent = false;
size_t queueDepth = 0;

struct Destination {
  const char* url;
  const char* token;
  const char* ca;
  bool cloud;
  uint32_t lastAttempt = 0;
  uint32_t backoff = 0;
  Destination(const char* u, const char* t, const char* c, bool isCloud)
      : url(u), token(t), ca(c), cloud(isCloud) {}
};
Destination pythonDest{PYTHON_URL, PYTHON_TOKEN, PYTHON_ROOT_CA, false};
Destination cloudDest{CLOUD_URL, CLOUD_TOKEN, CLOUD_ROOT_CA, true};

bool readExactly(uint8_t* bytes, size_t count) {
  uint32_t start = millis();
  size_t offset = 0;
  while (offset < count && millis() - start < 1500) {
    int available = modbus.available();
    if (available > 0) {
      int n = modbus.read(bytes + offset, min(count - offset, size_t(available)));
      if (n > 0) offset += n;
    } else if (!modbus.connected()) {
      break;
    }
    delay(1);
  }
  return offset == count;
}

bool readPLC() {
  if (WiFi.status() != WL_CONNECTED) return false;
  if (!modbus.connected() && !modbus.connect(PLC_HOST, PLC_PORT, 1500)) return false;
  ++transaction;
  // MBAP header plus function 03: 15 holding registers starting at address zero.
  uint8_t request[] = {uint8_t(transaction >> 8), uint8_t(transaction), 0, 0, 0, 6,
                       PLC_UNIT_ID, 3, 0, 0, 0, REGISTER_COUNT};
  uint8_t header[7], body[2 + REGISTER_COUNT * 2];
  if (modbus.write(request, sizeof(request)) != sizeof(request) || !readExactly(header, 7)) return false;
  if (header[0] != request[0] || header[1] != request[1] || header[2] || header[3] ||
      header[4] || header[5] != sizeof(body) + 1 || header[6] != PLC_UNIT_ID) return false;
  if (!readExactly(body, sizeof(body)) || body[0] != 3 || body[1] != REGISTER_COUNT * 2) return false;
  for (size_t i = 0; i < REGISTER_COUNT; ++i) registers[i] = (uint16_t(body[2 + i * 2]) << 8) | body[3 + i * 2];
  return true;
}

bool postSample(Destination& dest, JsonDocument& sample) {
  if (!strlen(dest.url) || WiFi.status() != WL_CONNECTED) return false;
  if (dest.backoff && millis() - dest.lastAttempt < dest.backoff) return false;
  dest.lastAttempt = millis();
  bool https = String(dest.url).startsWith("https://");
  bool valid = https ? strlen(dest.ca) && time(nullptr) > 1704067200 :
                      !dest.cloud && String(dest.url).startsWith("http://");
  int status = -1;
  if (valid && strlen(dest.token)) {
    WiFiClient plain;
    WiFiClientSecure secure;
    HTTPClient http;
    if (https) { secure.setCACert(dest.ca); secure.setHandshakeTimeout(3); }
    bool started = https ? http.begin(secure, dest.url) : http.begin(plain, dest.url);
    if (started) {
      http.setConnectTimeout(1500);
      http.setTimeout(1500);
      http.addHeader("Content-Type", "application/json");
      http.addHeader("Authorization", String("Bearer ") + dest.token);
      String body;
      serializeJson(sample, body);
      status = http.POST(body);
      http.end();
    }
  }
  if (status >= 200 && status < 300) {
    dest.backoff = 0;
    return true;
  }
  dest.backoff = dest.backoff ? min(dest.backoff * 2, MAX_BACKOFF_MS) : 2000;
  Serial.printf("%s upload pending (HTTP %d); retry in %lu ms\n", dest.cloud ? "Cloud" : "Python", status, (unsigned long)dest.backoff);
  return false;
}

bool saveRecord(const String& path, JsonDocument& record) {
  // A failed rewrite leaves the original pending record available for retry.
  File file = LittleFS.open("/queue.tmp", "w");
  if (!file) return false;
  size_t expected = measureJson(record);
  size_t written = serializeJson(record, file);
  file.flush();
  file.close();
  if (written != expected) { LittleFS.remove("/queue.tmp"); return false; }
  return LittleFS.rename("/queue.tmp", path);
}

void enqueue(JsonDocument& sample, uint8_t pending) {
  if (!storageReady || queueDepth >= MAX_QUEUED) { ++dropped; return; }
  JsonDocument record;
  record["pending"] = pending;
  record["sample"] = sample.as<JsonObject>();
  String path = String("/q/") + bootId + "_" + sequence + ".json";
  if (saveRecord(path, record)) ++queueDepth;
  else ++dropped;
}

void replayOne() {
  if (!storageReady || !queueDepth || WiFi.status() != WL_CONNECTED) return;
  // Skip destinations in backoff so a cloud outage cannot block Python replay.
  uint8_t eligible = 0;
  if (!pythonDest.backoff || millis() - pythonDest.lastAttempt >= pythonDest.backoff) eligible |= 1;
  if (strlen(CLOUD_URL) && (!cloudDest.backoff || millis() - cloudDest.lastAttempt >= cloudDest.backoff)) eligible |= 2;
  if (!eligible) return;
  // Process the oldest eligible boot/sequence; directory enumeration is not ordered.
  String oldest;
  uint32_t oldestBoot = UINT32_MAX, oldestSeq = UINT32_MAX;
  File dir = LittleFS.open("/q");
  File file = dir.openNextFile();
  while (file) {
    String name = file.name();
    name = name.substring(name.lastIndexOf('/') + 1);
    JsonDocument metadata;
    DeserializationError parseError = deserializeJson(metadata, file);
    bool invalid = parseError || !metadata["pending"].is<uint8_t>() ||
                   metadata["pending"].as<uint8_t>() < 1 || metadata["pending"].as<uint8_t>() > 3;
    uint8_t pendingBits = invalid ? eligible : metadata["pending"].as<uint8_t>();
    unsigned long b = 0, s = 0;
    if ((pendingBits & eligible) && sscanf(name.c_str(), "%lu_%lu.json", &b, &s) == 2 &&
        (b < oldestBoot || (b == oldestBoot && s < oldestSeq))) {
      oldest = String("/q/") + name;
      oldestBoot = b; oldestSeq = s;
    }
    file.close();
    file = dir.openNextFile();
  }
  dir.close();
  if (oldest.isEmpty()) return;
  file = LittleFS.open(oldest, "r");
  JsonDocument record;
  DeserializationError error = deserializeJson(record, file);
  file.close();
  if (error || !record["sample"].is<JsonObject>() || !record["pending"].is<uint8_t>() ||
      record["pending"].as<uint8_t>() < 1 || record["pending"].as<uint8_t>() > 3) {
    Serial.println("Corrupt queue record discarded");
    if (LittleFS.remove(oldest)) { --queueDepth; ++dropped; }
    return;
  }
  uint8_t pending = record["pending"], original = pending;
  JsonDocument sample;
  sample.set(record["sample"]);
  sample["replayed"] = true; // Never treat buffered data as current machine state.
  if ((pending & eligible & 1) && postSample(pythonDest, sample)) pending &= ~1;
  // Disabled cloud retains old cloud records until it is configured again.
  if ((pending & eligible & 2) && postSample(cloudDest, sample)) pending &= ~2;
  if (!pending) {
    if (LittleFS.remove(oldest)) --queueDepth;
  } else if (pending != original) {
    record["pending"] = pending;
    saveRecord(oldest, record); // If interrupted, duplicate delivery is safe by sample ID.
  }
}

void uploadCurrent() {
  if (sequence == UINT32_MAX) { ESP.restart(); return; }
  ++sequence;
  JsonDocument sample;
  sample["device_id"] = DEVICE_ID;
  sample["boot_id"] = bootId;
  sample["sequence"] = sequence;
  sample["uptime_ms"] = millis();
  sample["plc_connected"] = plcConnected;
  if (plcConnected) {
    JsonArray values = sample["registers"].to<JsonArray>();
    for (uint16_t value : registers) values.add(value);
  } else sample["registers"] = nullptr;
  sample["wifi_rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -127;
  sample["free_heap"] = ESP.getFreeHeap();
  sample["queue_depth"] = queueDepth;
  sample["dropped_samples"] = dropped;
  sample["last_fault_code"] = lastFault;
  sample["storage_ready"] = storageReady;
  sample["replayed"] = false;
  sample["sample_age_ms"] = millis() - sampledMs;
  time_t now = time(nullptr);
  if (now > 1704067200) {
    char timestamp[25]; struct tm utc;
    time_t sampled = now - (millis() - sampledMs) / 1000;
    gmtime_r(&sampled, &utc);
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
    sample["sampled_at"] = timestamp;
  } else sample["sampled_at"] = nullptr;
  uint8_t pending = 0;
  if (!postSample(pythonDest, sample)) pending |= 1;
  // Account for time spent posting to Python before sending the same sample to cloud.
  sample["sample_age_ms"] = millis() - sampledMs;
  if (strlen(CLOUD_URL) && !postSample(cloudDest, sample)) pending |= 2;
  if (pending) enqueue(sample, pending);
}

void setup() {
  Serial.begin(115200);
  if (!memory.begin("logiclatch", false)) {
    Serial.println("Cannot open persistent device identity; uploads halted");
    while (true) delay(1000);
  }
  uint32_t previousBoot = memory.getUInt("boot", 0);
  if (previousBoot == UINT32_MAX || memory.putUInt("boot", previousBoot + 1) != sizeof(uint32_t)) {
    Serial.println("Cannot advance persistent boot counter; uploads halted");
    while (true) delay(1000);
  }
  bootId = previousBoot + 1;
  lastFault = memory.getUShort("lastFault", 0);
  dropped = memory.getUInt("dropped", 0);
  // Never auto-format: an unexpected mount failure must not erase queued readings.
  storageReady = LittleFS.begin(false);
  if (storageReady) {
    storageReady = LittleFS.exists("/q") || LittleFS.mkdir("/q");
    File dir = LittleFS.open("/q");
    File file = dir.openNextFile();
    while (file) { ++queueDepth; file.close(); file = dir.openNextFile(); }
  }
  Serial.printf("Gateway boot %lu; previous fault %u; queued %u; storage %s\n", (unsigned long)bootId, lastFault, unsigned(queueDepth), storageReady ? "ready" : "unavailable");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, NTP_SERVER);
}

void loop() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED && now - lastWiFi >= 10000) {
    lastWiFi = now;
    WiFi.reconnect();
  }
  if (now - lastPoll >= POLL_MS) {
    lastPoll = now;
    bool wasConnected = plcConnected;
    plcConnected = readPLC();
    sampledMs = millis();
    if (!plcConnected) modbus.stop();
    urgent = urgent || wasConnected != plcConnected;
    if (plcConnected && registers[14] != lastFault) {
      lastFault = registers[14];
      memory.putUShort("lastFault", lastFault); // Only write NVS when the fault changes.
      urgent = true;
    }
  }
  if (sampledMs && (urgent || millis() - lastUpload >= UPLOAD_MS)) {
    lastUpload = millis(); urgent = false;
    uploadCurrent();
  }
  if (millis() - lastReplay >= 250) {
    lastReplay = millis();
    replayOne();
  }
  if (millis() - lastCheckpoint >= 60000) {
    lastCheckpoint = millis();
    if (memory.getUInt("dropped", 0) != dropped) memory.putUInt("dropped", dropped);
  }
  delay(5);
}
