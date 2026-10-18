#pragma once

// Copy to logiclatch_config.h (ignored by Git) and fill in your network/device settings.
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* PLC_HOST = "192.168.1.50";
const uint16_t PLC_PORT = 502;
const uint8_t PLC_UNIT_ID = 1;
const char* DEVICE_ID = "logiclatch-01";

// Python's LAN address, not localhost. Must match ESP32_DEVICE_ID / INGEST_TOKEN.
const char* PYTHON_URL = "http://192.168.1.100:8000/api/telemetry";
const char* PYTHON_TOKEN = "";
const char* PYTHON_ROOT_CA = ""; // Required if PYTHON_URL uses HTTPS: PEM root CA.

// Optional second destination; empty URL disables cloud uploading.
// Must accept the same JSON contract and acknowledge stored samples with 2xx.
const char* CLOUD_URL = "";
const char* CLOUD_TOKEN = "";
const char* CLOUD_ROOT_CA = ""; // PEM root CA; server verification is mandatory.
const char* NTP_SERVER = "pool.ntp.org";
