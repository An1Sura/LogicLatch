# LogicLatch

## Project Overview

LogicLatch is an industrial diagnostics bridge that connects PLC logic, Modbus TCP data, a Python API layer, and an embedded ESP32 client into one coherent troubleshooting system. It is built to demonstrate how real automation faults can be surfaced, classified, and explained across controls and software boundaries.

The project models a small industrial cell where CODESYS publishes machine status through holding registers, FastAPI converts raw register values into useful diagnostics, and an ESP32 client independently polls the same PLC data for embedded field visibility.

## Architecture

```text
CODESYS PLC Structured Text
    Holding registers 0-14
            |
            | Modbus TCP
            v
Python FastAPI Bridge
    Polling, state detection, event history, diagnostics API, WebSocket stream
            |
            | HTTP / WebSocket
            v
Operator tools, dashboards, test clients

ESP32 Embedded Client
    WiFi + Modbus TCP polling of the same PLC register map
```

## Features

- CODESYS Structured Text logic for machine state, fault latches, and scenario loading.
- Fixed Modbus TCP holding register map from address `0` through `14`.
- Python FastAPI bridge with live polling, diagnostics, event history, mock scenarios, and WebSocket streaming.
- Rule-based diagnostics engine for common industrial fault modes.
- ESP32 WiFi client that polls PLC holding registers and prints decoded values to Serial.
- CAD enclosure assets for a small ESP32 diagnostics module.
- Repository layout suitable for an engineering portfolio or practical controls/software integration demo.

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

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/python/bridge.py
```

Default API URL:

```text
http://127.0.0.1:8000
```

Useful endpoints:

- `GET /health`
- `GET /api/state`
- `GET /api/events`
- `GET /api/diagnostics`
- `POST /api/mock/scenario`
- `WS /ws/live`

Example mock scenario request:

```bash
curl -X POST http://127.0.0.1:8000/api/mock/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario":"conveyor_jam"}'
```

### CODESYS Setup

1. Create a CODESYS project using a runtime that supports Modbus TCP server operation.
2. Add the Structured Text files from `CODESYS/` as global variable lists, a function block, and the main PLC program.
3. Enable a Modbus TCP server/slave device.
4. Map the exported holding register array `GVL_Diagnostics.HoldingRegs[0..14]` to Modbus holding registers `0..14`.
5. Run the PLC project and set `GVL_Scenario.ScenarioName` plus `GVL_Scenario.LoadScenario` to load demo conditions.

### ESP32 Setup

1. Install the ESP32 board package in the Arduino IDE.
2. Install the `ArduinoModbus` library.
3. Open `firmware/esp32/esp32_diagnostics.ino`.
4. Set `WIFI_SSID`, `WIFI_PASSWORD`, and `PLC_HOST`.
5. Upload to the ESP32.
6. Open Serial Monitor at `115200` baud.

## Folder Structure

```text
logiclatch/
├── README.md
├── requirements.txt
├── .env.example
├── src/python/bridge.py
├── firmware/esp32/esp32_diagnostics.ino
├── CODESYS/
│   ├── codesys_globals.st
│   ├── codesys_constants.st
│   ├── codesys_scenario_globals.st
│   ├── codesys_fb_scenario_demo_loader.st
│   └── codesys_main.st
└── CAD/
    ├── ESP32ModuleBase.stl
    └── ESP32ModuleTop.stl
```

## CAD Section

The `CAD/` directory contains STL enclosure components for an ESP32 diagnostics module:

- `ESP32ModuleBase.stl`: lower tray for the embedded diagnostics module.
- `ESP32ModuleTop.stl`: matching cover plate.

The CAD assets are included so the project can be presented as a complete hardware/software diagnostic bridge rather than only an API demo.

## Project Goal

Modern plants rarely fail inside one clean boundary. A troubleshooting workflow often crosses PLC logic, fieldbus/register data, backend services, embedded devices, and physical hardware. This project demonstrates that bridge: raw PLC signals are converted into readable system state, fault events, and actionable diagnostics while preserving the register-level truth that controls engineers need.

## Summary

Designed and implemented an industrial PLC diagnostics bridge integrating CODESYS Structured Text, Modbus TCP, Python FastAPI, WebSocket telemetry, ESP32 embedded polling, and CAD enclosure assets. Built a fixed register interface, fault simulation scenarios, and a rule-based diagnostics engine for realistic automation troubleshooting workflows.

## Operator dashboard

Start the bridge as above and open `http://127.0.0.1:8000/dashboard/` (the root URL redirects there). The dashboard is served by FastAPI with no Node build step, CDN, or frontend runtime dependencies. Python 3.11+ is required by the bridge; Python 3.12 is recommended for the pinned dependencies.

The near-black LogicLatch dashboard includes a selectable logical topology, node inspector, all 15 register values, session motor-current samples, backend diagnostics, an event timeline, and a mock scenario lab. The layout adapts to mobile; the topology remains horizontally scrollable to preserve readable device cards.

### Data contracts and limits

- Every two seconds, same-origin `GET /api/state`, `GET /api/diagnostics`, and `GET /api/events` provide the displayed data. Requests time out after five seconds; polling resumes automatically. The existing `/ws/live` endpoint is unchanged.
- `source: mock` is visibly marked **DEMO MODE**. There is no fabricated fallback when the API is unreachable. Only an already connected mock source enables `POST /api/mock/scenario`; loading a scenario affects all viewers of that backend. The frontend does not expose a switch from physical PLC operation into mock mode.
- `connected: false` hides retained register values, current, machine state, and fault metrics. Backend diagnostics may still contain retained-register evidence; the banner explains this. A failed diagnostics/events request is shown as unavailable independently of state.
- Motor current is `Motor_Current / 10` amperes. The chart contains at most 60 actual responses collected during this browser session, clears after disconnection/source changes, and is not persisted.
- The bridge timestamp is a response timestamp, **not a verified PLC acquisition timestamp**. The existing bridge does not expose sample age. This dashboard cannot detect a silently stale connected backend value.
- The topology represents the documented architecture, not discovered network inventory. Conveyor/safety nodes are PLC signal groups. ESP32 connection and readings are **unobserved**, because the firmware outputs to Serial and exposes no telemetry endpoint to FastAPI. Host addresses, latency, temperature, and uptime are not invented.
- No reset, motion, or PLC write controls are added. Firmware, register mapping, diagnostics rules, and existing API behavior remain unchanged.

### Validation

```bash
node --test tests/model.test.mjs
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Browser integration testing requires an installed Playwright package and Chrome, plus a **mock** bridge running on port 8765:

```bash
API_HOST=127.0.0.1 API_PORT=8765 USE_MOCK_PLC=true python src/python/bridge.py
# In another terminal; use an installed Playwright package path if not on the module path:
PLAYWRIGHT_MODULE=/absolute/path/to/playwright SCREENSHOT_DIR=/absolute/output/directory node tests/browser.cjs
```

The browser suite changes mock scenarios and checks topology selection, diagnostics, register rows, mobile overflow, disconnected PLC handling, API outage/recovery, and console errors. Optional `DASHBOARD_URL` and `BROWSER_CHANNEL` override the test URL and browser. Screenshots use mock data; real hardware requires separate commissioning validation.
