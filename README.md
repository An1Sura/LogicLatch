# Resolvyn

Resolvyn evolves the LogicLatch diagnostics demo into a reproducible PLC simulation
and evidence-based troubleshooting lab. This is a development prototype: dynamic
conveyor/pump models and temporal rules are implemented; trained AI, production
hardware validation and the operator dashboard are proposed work.

## Run without hardware

Python 3.11 or 3.12 is recommended for the pinned dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python src/python/evaluate.py
python src/python/bridge.py
```

Open `http://127.0.0.1:8000/docs` for the interactive API. With default mock mode:

```bash
curl -X POST http://127.0.0.1:8000/api/simulation \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"conveyor_jam","seed":1}'
curl http://127.0.0.1:8000/api/root-cause
```

The simulation trips after an injected fault at 3 seconds. Query while it runs for
live hypotheses; after the trip, inspect `last_supported_observation` for historical
pre-trip evidence. This is distinct from the legacy immediate `/api/diagnostics`
alerts. Available dynamic scenarios: `healthy_conveyor`, `conveyor_jam`,
`photoeye_stuck`, `motor_overload`, `safety_trip`, `healthy_pump`,
`pump_station_failure`. Starting a new scenario resets the simulated cell; the API
never sends a reset to a PLC.

### Exercise the real Modbus path

Run these in separate terminals after activating the environment:

```bash
python src/python/simulator_server.py --scenario conveyor_jam --port 1502
```

```bash
USE_MOCK_PLC=false PLC_HOST=127.0.0.1 PLC_PORT=1502 \
  POLL_INTERVAL_SECONDS=0.05 python src/python/bridge.py
```

Start the bridge first if you want its evidence history to include startup and the
fault onset. The server binds loopback, unit 1, read-only holding registers 0–14.
Mock/simulation controls return 403 in live mode. The 50 ms polling interval is for
this local lab; the ordinary default is 1 second and may miss short transients.
The API defaults to localhost and is not authenticated for remote deployment.

[Audit, hardware assessment and phased verification](docs/RESOLVYN_PLAN.md) explain
what is implemented, the simulation limits, and remaining production work.
[Per-episode synthetic measurements](docs/evaluation.json) are reproducible and are
not claims of real-world AI accuracy or downtime savings.

## Original components and compatibility

## Project Overview

Resolvyn is an industrial diagnostics bridge that connects PLC logic, Modbus TCP data, a Python API layer, and an embedded ESP32 client into one coherent troubleshooting system. It is built to demonstrate how real automation faults can be surfaced, classified, and explained across controls and software boundaries.

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
- Legacy instantaneous alerts plus temporal, evidence-ranked diagnostic hypotheses.
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
resolvyn/
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

The original demo supplied an industrial PLC diagnostics bridge integrating CODESYS Structured Text, Modbus TCP, Python FastAPI, WebSocket telemetry, ESP32 embedded polling, and CAD enclosure assets. Built a fixed register interface, fault simulation scenarios, and a rule-based diagnostics engine for realistic automation troubleshooting workflows.
