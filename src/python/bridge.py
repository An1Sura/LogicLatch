from __future__ import annotations

import asyncio
import logging
import os
import hmac
import math
import json
import sqlite3
from pathlib import Path
from contextlib import asynccontextmanager
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from threading import RLock
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("logiclatch")

REGISTER_COUNT = 15

REGISTER_MAP: dict[int, str] = {
    0: "Conveyor_Running",
    1: "Sensor_Blocked",
    2: "Motor_Current",
    3: "Safety_OK",
    4: "Start_Command",
    5: "Stop_Command",
    6: "Reset_Command",
    7: "Tank_Level_Low",
    8: "Tank_Level_High",
    9: "Sequence_Timeout",
    10: "System_Fault_Latch",
    11: "Pump_Running",
    12: "HVAC_Fault",
    13: "Mode_Code",
    14: "Fault_Code",
}


class ModeCode(IntEnum):
    IDLE = 0
    CONVEYOR = 10
    TRAFFIC_CONTROL = 20
    PUMP_STATION = 30
    ELEVATOR = 40
    TUNNEL_VENTILATION = 50
    GARAGE_DOOR = 60


MODE_NAMES = {
    ModeCode.IDLE: "Idle",
    ModeCode.CONVEYOR: "Conveyor",
    ModeCode.TRAFFIC_CONTROL: "Traffic Control",
    ModeCode.PUMP_STATION: "Pump Station",
    ModeCode.ELEVATOR: "Elevator",
    ModeCode.TUNNEL_VENTILATION: "Tunnel Ventilation",
    ModeCode.GARAGE_DOOR: "Garage Door",
}

FAULT_NAMES = {
    0: "No active fault",
    101: "Traffic phase conflict",
    201: "Pump station level feedback failure",
    301: "Elevator door safety timeout",
    401: "Tunnel ventilation fault",
    501: "Garage door safety lockout",
    601: "Conveyor jam or overload",
}

MOCK_SCENARIOS: dict[str, list[int]] = {
    "idle": [0, 0, 18, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, ModeCode.IDLE, 0],
    "traffic_phase_conflict": [0, 0, 12, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, ModeCode.TRAFFIC_CONTROL, 101],
    "pump_station_failure": [0, 0, 24, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, ModeCode.PUMP_STATION, 201],
    "elevator_door_fault": [0, 1, 16, 0, 1, 0, 0, 0, 1, 1, 1, 0, 0, ModeCode.ELEVATOR, 301],
    "tunnel_ventilation_fault": [0, 0, 20, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, ModeCode.TUNNEL_VENTILATION, 401],
    "garage_door_fault": [0, 1, 15, 0, 1, 0, 0, 0, 1, 1, 1, 0, 0, ModeCode.GARAGE_DOOR, 501],
    "conveyor_jam": [1, 1, 95, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, ModeCode.CONVEYOR, 601],
}


@dataclass(frozen=True)
class BridgeConfig:
    poll_interval_seconds: float = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))
    use_mock_plc: bool = os.getenv("USE_MOCK_PLC", "false").lower() in {"1", "true", "yes", "on"}
    initial_mock_scenario: str = os.getenv("INITIAL_MOCK_SCENARIO", "idle")
    device_id: str = os.getenv("ESP32_DEVICE_ID", "logiclatch-01")
    ingest_token: str = os.getenv("INGEST_TOKEN", "")
    stale_after_seconds: float = float(os.getenv("STALE_AFTER_SECONDS", "15"))
    database_path: str = os.getenv("DATABASE_PATH", "data/telemetry.sqlite3")
    history_limit: int = 10000

    def __post_init__(self) -> None:
        if (not math.isfinite(self.poll_interval_seconds) or not math.isfinite(self.stale_after_seconds)
                or self.poll_interval_seconds <= 0 or self.stale_after_seconds <= 0 or self.history_limit < 1):
            raise ValueError("Intervals and history_limit must be positive")


RegisterValue = Annotated[int, Field(strict=True, ge=0, le=65535)]


class TelemetryRequest(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    boot_id: int = Field(strict=True, ge=1, le=4294967295)
    sequence: int = Field(strict=True, ge=1, le=4294967295)
    uptime_ms: int = Field(strict=True, ge=0)
    plc_connected: bool = Field(strict=True)
    registers: list[RegisterValue] | None = Field(default=None, min_length=15, max_length=15)
    wifi_rssi: int = Field(strict=True, ge=-127, le=0)
    free_heap: int = Field(strict=True, ge=0)
    queue_depth: int = Field(strict=True, ge=0)
    dropped_samples: int = Field(strict=True, ge=0)
    last_fault_code: int = Field(default=0, strict=True, ge=0, le=65535)
    storage_ready: bool = Field(default=True, strict=True)
    replayed: bool = Field(default=False, strict=True)
    sample_age_ms: int = Field(default=0, strict=True, ge=0)
    sampled_at: str | None = None


class ScenarioRequest(BaseModel):
    scenario: str = Field(..., description="One of the supported demo scenario names.")


class DiagnosticFinding(BaseModel):
    severity: str
    code: int
    title: str
    evidence: list[str]
    recommendation: str


class DiagnosticsResponse(BaseModel):
    timestamp: str
    healthy: bool
    machine_state: str
    active_fault_code: int
    findings: list[DiagnosticFinding]


class PLCState(BaseModel):
    timestamp: str
    connected: bool
    source: str
    registers: dict[str, int]
    raw_registers: list[int]
    machine_state: str
    active_fault_code: int
    active_fault_name: str
    gateway: dict[str, Any] | None = None


class PLCBridge:
    """Single-machine telemetry receiver. The ESP32 is the only live PLC reader."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._lock = RLock()
        Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(config.database_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS telemetry (
                device_id TEXT, boot_id INTEGER, sequence INTEGER,
                received_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (device_id, boot_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS latest (
                device_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._mock = config.initial_mock_scenario
        self._record_event("bridge_started", "Waiting for ESP32 telemetry." if not config.use_mock_plc else "Mock mode enabled.")

    def close(self) -> None:
        self._db.close()

    def _latest(self) -> tuple[str, dict[str, Any]] | None:
        row = self._db.execute("SELECT received_at, payload FROM latest WHERE device_id = ?", (self.config.device_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def ingest(self, packet: TelemetryRequest) -> dict[str, Any]:
        if packet.device_id != self.config.device_id:
            raise ValueError("Unknown device_id")
        if packet.plc_connected != (packet.registers is not None):
            raise ValueError("Connected samples require 15 registers; disconnected samples require null registers")
        now = utc_now()
        payload = packet.model_dump()
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT 1 FROM telemetry WHERE device_id=? AND boot_id=? AND sequence=?",
                (packet.device_id, packet.boot_id, packet.sequence),
            ).fetchone()
            if existing:
                return {"accepted": True, "duplicate": True, "live_updated": False}
            previous = self._latest()
            newer = previous is None or (packet.boot_id, packet.sequence) > (previous[1]["boot_id"], previous[1]["sequence"])
            live = newer and not packet.replayed and packet.sample_age_ms < self.config.stale_after_seconds * 1000
            self._db.execute("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?)",
                             (packet.device_id, packet.boot_id, packet.sequence, now, json.dumps(payload)))
            if live:
                # A failed PLC poll invalidates connectivity, but preserves the last known values.
                payload["last_registers"] = packet.registers or (previous[1].get("last_registers") if previous else None)
                self._db.execute("INSERT OR REPLACE INTO latest VALUES (?, ?, ?)",
                                 (packet.device_id, now, json.dumps(payload)))
                old_fault = (previous[1].get("last_registers") or [0] * 15)[14] if previous else 0
                if packet.registers is not None and packet.registers[14] != old_fault:
                    self._record_event("fault_code_changed", f"Fault changed to {packet.registers[14]}.")
                if previous is None or packet.plc_connected != previous[1]["plc_connected"]:
                    self._record_event("connection_state_changed", f"ESP32 reports PLC connected={packet.plc_connected}.")
            self._db.execute("DELETE FROM telemetry WHERE rowid NOT IN (SELECT rowid FROM telemetry ORDER BY rowid DESC LIMIT ?)",
                             (self.config.history_limit,))
        return {"accepted": True, "duplicate": False, "live_updated": live}

    def state(self) -> PLCState:
        with self._lock:
            latest = self._latest()
            gateway = None
            if self.config.use_mock_plc:
                registers = list(MOCK_SCENARIOS.get(self._mock, MOCK_SCENARIOS["idle"]))
                connected, source = True, "mock"
            elif latest:
                received_at, payload = latest
                age = max(0, (datetime.now(UTC) - datetime.fromisoformat(received_at)).total_seconds()) + payload["sample_age_ms"] / 1000
                stale = age >= self.config.stale_after_seconds
                registers = payload.get("last_registers") or [0] * REGISTER_COUNT
                connected, source = payload["plc_connected"] and not stale, "esp32"
                gateway = {k: v for k, v in payload.items() if k not in {"registers", "last_registers"}}
                gateway.update(received_at=received_at, age_seconds=age, stale=stale)
            else:
                registers, connected, source = [0] * REGISTER_COUNT, False, "esp32"
        fault_code = registers[14]
        return PLCState(
            timestamp=utc_now(), connected=connected, source=source,
            registers={REGISTER_MAP[i]: registers[i] for i in range(REGISTER_COUNT)},
            raw_registers=registers, machine_state=detect_machine_state(registers),
            active_fault_code=fault_code, active_fault_name=FAULT_NAMES.get(fault_code, "Unknown fault"),
            gateway=gateway,
        )

    def history(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT received_at, payload FROM telemetry ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            return [{"received_at": row[0], **json.loads(row[1])} for row in rows]

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def set_mock_scenario(self, scenario: str) -> PLCState:
        if not self.config.use_mock_plc:
            raise ValueError("Mock scenarios are disabled in ESP32 mode")
        if scenario not in MOCK_SCENARIOS:
            raise ValueError(f"Unsupported scenario '{scenario}'.")
        with self._lock:
            self._mock = scenario
            self._record_event("mock_scenario_loaded", f"Loaded mock scenario '{scenario}'.")
        return self.state()

    def _record_event(self, event_type: str, message: str) -> None:
        self._events.appendleft({"timestamp": utc_now(), "type": event_type, "message": message})


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def bool_reg(registers: list[int], index: int) -> bool:
    return registers[index] != 0


def detect_machine_state(registers: list[int]) -> str:
    mode_value = registers[13]
    mode_name = MODE_NAMES.get(ModeCode(mode_value), "Unknown") if mode_value in ModeCode._value2member_map_ else "Unknown"

    if bool_reg(registers, 10):
        return f"{mode_name} - Faulted"
    if bool_reg(registers, 4) and not bool_reg(registers, 5):
        return f"{mode_name} - Running"
    if bool_reg(registers, 5):
        return f"{mode_name} - Stopping"
    return f"{mode_name} - Ready"


def run_diagnostics(state: PLCState) -> DiagnosticsResponse:
    r = state.raw_registers
    findings: list[DiagnosticFinding] = []
    fault_code = state.active_fault_code

    if not state.connected:
        findings.append(
            DiagnosticFinding(
                severity="critical",
                code=-1,
                title="PLC communication unavailable",
                evidence=[f"Source '{state.source}' is not connected."],
                recommendation="Check ESP32 power, Wi-Fi, telemetry uploads, and its Modbus connection to the PLC.",
            )
        )

    if bool_reg(r, 10):
        findings.append(
            DiagnosticFinding(
                severity="high",
                code=fault_code,
                title=FAULT_NAMES.get(fault_code, "Latched system fault"),
                evidence=["System_Fault_Latch is active.", f"Fault_Code is {fault_code}."],
                recommendation="Review the active fault, correct the field condition, then issue Reset_Command after the process is safe.",
            )
        )

    if bool_reg(r, 0) and bool_reg(r, 1) and r[2] >= 80:
        findings.append(
            DiagnosticFinding(
                severity="critical",
                code=601,
                title="Conveyor jam or overload",
                evidence=[
                    "Conveyor_Running is true.",
                    "Sensor_Blocked is true.",
                    f"Motor_Current is {r[2] / 10:.1f} A.",
                ],
                recommendation="Stop the conveyor, clear the obstruction, inspect the motor load path, and verify the photoeye before restart.",
            )
        )

    if bool_reg(r, 11) and bool_reg(r, 7) and bool_reg(r, 8):
        findings.append(
            DiagnosticFinding(
                severity="high",
                code=201,
                title="Pump station level feedback conflict",
                evidence=["Pump_Running is true.", "Tank_Level_Low and Tank_Level_High are both true."],
                recommendation="Inspect level switches, wet well wiring, and input scaling. Do not run the pump automatically until feedback is credible.",
            )
        )

    if bool_reg(r, 12):
        findings.append(
            DiagnosticFinding(
                severity="high",
                code=401,
                title="Ventilation fault active",
                evidence=["HVAC_Fault is true."],
                recommendation="Check fan starter feedback, VFD fault status, airflow proving switch, and emergency ventilation permissives.",
            )
        )

    if not bool_reg(r, 3):
        findings.append(
            DiagnosticFinding(
                severity="critical",
                code=501 if fault_code == 501 else fault_code,
                title="Safety chain is not healthy",
                evidence=["Safety_OK is false."],
                recommendation="Validate E-stop, guard door, light curtain, safety relay, and reset circuit before enabling motion.",
            )
        )

    if bool_reg(r, 9):
        findings.append(
            DiagnosticFinding(
                severity="medium",
                code=301 if fault_code == 301 else fault_code,
                title="Sequence timeout",
                evidence=["Sequence_Timeout is true."],
                recommendation="Check actuator travel, permissive inputs, sensor timing, and step transition logic for the active mode.",
            )
        )

    if bool_reg(r, 4) and bool_reg(r, 5):
        findings.append(
            DiagnosticFinding(
                severity="medium",
                code=101,
                title="Conflicting operator commands",
                evidence=["Start_Command and Stop_Command are both true."],
                recommendation="Check HMI command latching, momentary pushbutton wiring, and command arbitration logic.",
            )
        )

    if not findings:
        findings.append(
            DiagnosticFinding(
                severity="info",
                code=0,
                title="No active diagnostic findings",
                evidence=["No fault latch or abnormal register combinations are active."],
                recommendation="Continue monitoring. Confirm field devices during scheduled maintenance.",
            )
        )

    return DiagnosticsResponse(
        timestamp=utc_now(),
        healthy=all(item.severity == "info" for item in findings),
        machine_state=state.machine_state,
        active_fault_code=fault_code,
        findings=findings,
    )


config = BridgeConfig()
bridge = PLCBridge(config)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    bridge.close()


app = FastAPI(
    lifespan=lifespan,
    title="LogicLatch",
    description="Industrial diagnostics bridge for CODESYS, Modbus TCP, FastAPI, and ESP32 demos.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/telemetry")
def api_telemetry(packet: TelemetryRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not config.ingest_token:
        raise HTTPException(status_code=503, detail="Configure INGEST_TOKEN before accepting telemetry")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {config.ingest_token}".encode()):
        raise HTTPException(status_code=401, detail="Invalid ingestion token")
    if config.use_mock_plc:
        raise HTTPException(status_code=409, detail="Disable USE_MOCK_PLC to accept ESP32 telemetry")
    try:
        return bridge.ingest(packet)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/history")
def api_history(limit: int = 100) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000")
    return {"samples": bridge.history(limit)}


@app.get("/health")
async def health() -> dict[str, Any]:
    state = bridge.state()
    return {
        "status": "ok" if state.connected else "degraded",
        "timestamp": utc_now(),
        "plc_connected": state.connected,
        "source": state.source,
    }


@app.get("/api/state", response_model=PLCState)
async def api_state() -> PLCState:
    return bridge.state()


@app.get("/api/events")
async def api_events() -> dict[str, Any]:
    return {"events": bridge.events()}


@app.get("/api/diagnostics", response_model=DiagnosticsResponse)
async def api_diagnostics() -> DiagnosticsResponse:
    return run_diagnostics(bridge.state())


@app.post("/api/mock/scenario", response_model=PLCState)
async def api_mock_scenario(request: ScenarioRequest) -> PLCState:
    try:
        return bridge.set_mock_scenario(request.scenario)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "supported_scenarios": sorted(MOCK_SCENARIOS)},
        ) from exc


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            state = bridge.state()
            diagnostics = run_diagnostics(state)
            await websocket.send_json(
                {
                    "state": state.model_dump(),
                    "diagnostics": diagnostics.model_dump(),
                }
            )
            await asyncio.sleep(config.poll_interval_seconds)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
