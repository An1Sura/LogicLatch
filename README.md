# LogicLatch

## Project Overview

LogicLatch reads PLC status through an ESP32 gateway. The ESP32 polls CODESYS over Modbus TCP, sends telemetry to Python for diagnostics, and optionally uploads the same readings directly to a configurable HTTPS cloud endpoint.

## Architecture

```text
CODESYS PLC (holding registers 0–14)
                |
                | Modbus TCP, port 502
                v
          ESP32 gateway
          |           |
          |           +--- HTTPS POST ---> Configurable cloud receiver
          |
          +--- HTTP(S) POST /api/telemetry ---> Python FastAPI + SQLite
                                                        |
                                                  HTTP / WebSocket
                                                        v
                                              Operator tools / clients
```

Python no longer polls the PLC. ESP32 is the only live PLC client in this project. Mock mode remains available explicitly for demonstrations.

## What the ESP32 does

- Polls 15 holding registers about once a second and uploads about every five seconds, plus on detected fault/connectivity changes. Network operations are bounded but synchronous, so these are target intervals, not real-time guarantees.
- Sends independent uploads to Python and an optional cloud receiver, with separate retry backoff (2–60 seconds). Cloud replay failures do not block Python replay attempts.
- Saves failed deliveries to a shared LittleFS queue of at most 120 samples, with separate acknowledgement bits for each destination. At a five-second upload interval this holds roughly ten minutes; fault/connectivity changes may fill it sooner. When full, it preserves queued samples and counts new drops. A prolonged outage at either destination can fill this shared queue.
- Restores pending deliveries after restart. A record is deleted only after all enabled destinations acknowledge it. A crash between delivery and acknowledgement can cause duplicates: receivers must deduplicate `(device_id, boot_id, sequence)`.
- Remembers its boot counter and last observed fault code in NVS. The dropped-sample counter is checkpointed once a minute; a sudden power loss may lose up to a minute of that counter.
- Reports Wi-Fi signal, uptime, free memory, queue depth, dropped samples, and storage availability. With NTP, it also supplies a UTC sample timestamp; otherwise `sampled_at` is null and boot/sequence/uptime identify the sample.
- Uses verified TLS for cloud uploads. HTTP is supported only for the Python endpoint on a trusted local network; use HTTPS with a root CA when needed.

Python validates device identity and a bearer token, stores up to 10,000 telemetry records in SQLite, detects faults, and marks live telemetry stale after 15 seconds by default. Buffered/replayed samples enter history only, so they cannot make old readings appear live. Latest state and history survive Python restarts; the short `/api/events` event list is in memory.

## Register Map

| Register | Tag | Description |
| --- | --- | --- |
| 0 | `Conveyor_Running` | Conveyor motor command/status |
| 1 | `Sensor_Blocked` | Product/photoeye blockage |
| 2 | `Motor_Current` | Motor current in tenths of amps |
| 3 | `Safety_OK` | Safety chain healthy |
| 4 | `Start_Command` | Start request |
| 5 | `Stop_Command` | Stop request |
| 6 | `Reset_Command` | Fault reset request |
| 7 | `Tank_Level_Low` | Low level switch |
| 8 | `Tank_Level_High` | High level switch |
| 9 | `Sequence_Timeout` | Sequence watchdog timeout |
| 10 | `System_Fault_Latch` | Latched system fault |
| 11 | `Pump_Running` | Pump status |
| 12 | `HVAC_Fault` | Ventilation/HVAC fault |
| 13 | `Mode_Code` | Active machine mode |
| 14 | `Fault_Code` | Active fault code |

## Demo Scenarios

- `idle`: healthy system with no active fault.
- `traffic_phase_conflict`: simultaneous command conflict represented by fault `101`.
- `pump_station_failure`: pump is running with invalid tank level feedback, fault `201`.
- `elevator_door_fault`: sequence timeout and safety fault, fault `301`.
- `tunnel_ventilation_fault`: HVAC fault in tunnel ventilation mode, fault `401`.
- `garage_door_fault`: safety chain open with a blocked sensor, fault `501`.
- `conveyor_jam`: conveyor running with blocked sensor and high current, fault `601`.

## Setup Instructions

### Python Bridge

Use Python 3.12 with the pinned dependencies. Run from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated value in `.env` as `INGEST_TOKEN`, and use the same value for `PYTHON_TOKEN` in the ESP32 configuration. Set `ESP32_DEVICE_ID` to the firmware's `DEVICE_ID`. Keep `USE_MOCK_PLC=false` for hardware use. An empty ingestion token rejects uploads instead of silently exposing an unauthenticated receiver.

```bash
python src/python/bridge.py
```

The default listener is `0.0.0.0:8000`. ESP32 must use this computer's LAN IP, not `127.0.0.1`. The database defaults to `data/telemetry.sqlite3`; set `DATABASE_PATH` for persistent deployment storage. This receiver serves one configured device/machine per instance.

| Interface | Purpose |
| --- | --- |
| `POST /api/telemetry` | Authenticated ESP32 ingestion |
| `GET /health` | PLC data availability and source |
| `GET /api/state` | Latest state plus gateway health and stale flag |
| `GET /api/history?limit=100` | Persistent recent telemetry, newest receipt first (limit 1–1000) |
| `GET /api/events` | In-memory connection and fault events |
| `GET /api/diagnostics` | Rule-based diagnostic findings |
| `WS /ws/live` | State and diagnostics stream |
| `POST /api/mock/scenario` | Demo scenario selection, only with `USE_MOCK_PLC=true` |

For a demo without hardware, set `USE_MOCK_PLC=true` and restart Python. That mode rejects ESP32 uploads to prevent mixing simulated and real data. The existing scenario request remains `{"scenario":"conveyor_jam"}`. Read endpoints remain unauthenticated as in the original demo; put an authenticated HTTPS proxy in front of them before exposing a deployment publicly.

### CODESYS Setup

1. Create a CODESYS project using a runtime that supports Modbus TCP server operation.
2. Add the Structured Text files from `CODESYS/` as global variable lists, a function block, and the main PLC program.
3. Enable a Modbus TCP server/slave device.
4. Map the exported holding register array `GVL_Diagnostics.HoldingRegs[0..14]` to Modbus holding registers `0..14`.
5. Run the PLC project and set `GVL_Scenario.ScenarioName` plus `GVL_Scenario.LoadScenario` to load demo conditions.

### ESP32 Setup

1. Install PlatformIO and select the appropriate board. `platformio.ini` pins the build for an `esp32dev` (4 MB ESP32 Dev Module), Arduino core 2.0.17, and ArduinoJson 7.3.1.
2. Copy `firmware/esp32/config.example.h` to `firmware/esp32/logiclatch_config.h`. This private configuration is ignored by Git.
3. Set Wi-Fi credentials, PLC address, device ID, Python URL, and Python bearer token.
4. Build and upload firmware with `pio run` and `pio run --target upload`.
5. **Once on a new device**, provision the LittleFS partition with `pio run --target uploadfs`. This replaces the filesystem and erases any queued telemetry; do not repeat it for routine firmware updates. The firmware never automatically formats a failed mount.
6. Open `pio device monitor` at 115200 baud. Confirm storage is ready and uploads succeed. If storage cannot mount, live uploads still run, but failed uploads are counted as drops.

The gateway sends read-only Modbus function 03 requests. It checks the transaction ID, protocol ID, unit ID, length, function, and byte count before using a reply. A malformed reply or timeout marks the PLC disconnected and closes the socket.

### Optional cloud upload

Set `CLOUD_URL` to a full **HTTPS** ingestion URL, `CLOUD_TOKEN` to its bearer token, and `CLOUD_ROOT_CA` to its PEM root CA certificate in `logiclatch_config.h`. Leave `CLOUD_URL` empty to disable it. NTP must be reachable for certificate date validation. Certificate verification is never disabled. Python can also use HTTPS via `PYTHON_ROOT_CA`.

The cloud service must implement the telemetry contract below, durably store or deduplicate each accepted sample, and return a 2xx response only after acceptance. A second deployment of this Python receiver behind an HTTPS proxy can serve as that endpoint. This repository does not provision cloud hosting or a vendor-specific database integration.

A cloud upload failure retains its queue acknowledgement bit; it does not cancel successful Python delivery. If cloud is disabled after pending samples exist, those cloud records stay queued until cloud is configured again. Changing a destination URL causes pending samples for that destination to be retried at the new URL.

### Telemetry contract

Both destinations receive JSON with `Authorization: Bearer <destination-token>`:

```json
{
  "device_id": "logiclatch-01",
  "boot_id": 1,
  "sequence": 1,
  "uptime_ms": 5000,
  "plc_connected": true,
  "registers": [1, 1, 95, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 10, 601],
  "wifi_rssi": -55,
  "free_heap": 150000,
  "queue_depth": 0,
  "dropped_samples": 0,
  "last_fault_code": 601,
  "storage_ready": true,
  "replayed": false,
  "sample_age_ms": 10,
  "sampled_at": "2026-10-18T12:00:00Z"
}
```

`registers` must contain exactly 15 unsigned 16-bit integers when connected, or be null when disconnected. Python keeps the last known values on a disconnected update but marks them unavailable. `sample_age_ms` is age at transmission; `sampled_at` is optional device-provided metadata, not used to establish freshness. Replayed samples always have `replayed=true`, including after reboot when their precise age is unknown.

Python returns `accepted`, `duplicate`, and `live_updated`. Deduplication covers retained history; the persisted latest boot/sequence also prevents older live packets from replacing current state. A new boot uses a higher boot ID and starts sequence at 1. If NVS is erased or the ESP32 is replaced, use a new device ID and update Python's configuration (or intentionally reset its database). Reusing a lower boot ID under the same identity will not advance the previous latest state.

## Verification

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
pio run
```

Backend tests cover ingestion/authentication, validation, restart recovery, bounded history, deduplication, stale data, disconnected PLCs, replay ordering, mock isolation, and WebSocket output. Firmware compilation does not replace hardware testing. Before using a physical installation:

- Confirm PLC register values reach `/api/state` and both destinations.
- Stop Python, observe queue growth, restart it, and verify history catches up while the next fresh sample restores live state.
- Disconnect cloud independently and confirm Python continues receiving live readings and replay attempts.
- Reboot with queued readings; verify boot ID increases and pending records drain without duplicate rows.
- Disconnect PLC and then Wi-Fi; check disconnected and stale states respectively.
- Check missing/wrong tokens and CA certificates, full queue/drop reporting, and loss of power during queue updates.

Only failed deliveries are written to flash; fault memory changes only on fault transitions. Frequent long outages still cause flash writes. This is a bounded demonstration gateway, not lossless archival storage or a safety controller.

## CAD Section

The `CAD/` directory contains STL enclosure components for an ESP32 diagnostics module:

- `ESP32ModuleBase.stl`: lower tray for the embedded diagnostics module.
- `ESP32ModuleTop.stl`: matching cover plate.

The CAD assets are included so the project can be presented as a complete hardware/software diagnostic bridge rather than only an API demo.

## Project Goal

Modern plants rarely fail inside one clean boundary. A troubleshooting workflow often crosses PLC logic, fieldbus/register data, backend services, embedded devices, and physical hardware. This project demonstrates that bridge: raw PLC signals are converted into readable system state, fault events, and actionable diagnostics while preserving the register-level truth that controls engineers need.

## Summary

CODESYS → ESP32 gateway → Python diagnostics, with an independent optional HTTPS cloud upload path, persistent retry queue, device health telemetry, and SQLite history.
