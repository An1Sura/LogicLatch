from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from threading import RLock
from time import monotonic
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymodbus.client import ModbusTcpClient
from reasoning import EvidenceEngine
from simulation import CellSimulator, SCENARIOS

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("resolvyn")

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
    plc_host: str = os.getenv("PLC_HOST", "127.0.0.1")
    plc_port: int = int(os.getenv("PLC_PORT", "502"))
    plc_unit_id: int = int(os.getenv("PLC_UNIT_ID", "1"))
    poll_interval_seconds: float = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))
    use_mock_plc: bool = os.getenv("USE_MOCK_PLC", "true").lower() in {"1", "true", "yes", "on"}
    stale_after_seconds: float = float(os.getenv("STALE_AFTER_SECONDS", "3.0"))
    initial_mock_scenario: str = os.getenv("INITIAL_MOCK_SCENARIO", "idle")


    def __post_init__(self):
        if self.poll_interval_seconds <= 0 or self.stale_after_seconds <= 0:
            raise ValueError("Polling and stale intervals must be positive")


class SimulationRequest(BaseModel):
    scenario: str = 'healthy_conveyor'
    seed: int = 1


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
    sample_timestamp: str | None = None
    sample_age_seconds: float | None = None
    data_quality: str = "good"
    sequence: int = 0
    source: str
    registers: dict[str, int]
    raw_registers: list[int]
    machine_state: str
    active_fault_code: int
    active_fault_name: str


class PLCBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._client = ModbusTcpClient(host=config.plc_host, port=config.plc_port, timeout=2.0)
        self._lock = RLock()
        self._io_lock = RLock()
        self._sample_time = None
        self._sample_timestamp = None
        self._sequence = 0
        self._engine = EvidenceEngine(max_gap=config.stale_after_seconds)
        self._simulator = None
        initial = MOCK_SCENARIOS.get(config.initial_mock_scenario, MOCK_SCENARIOS["idle"])
        self._registers = [int(value) for value in initial]
        self._connected = config.use_mock_plc
        self._source = "mock" if config.use_mock_plc else "modbus"
        self._last_fault_code = self._registers[14]
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        if config.use_mock_plc:
            self._stamp_sample()
        self._record_event("bridge_started", f"Diagnostics bridge started using {self._source} source.")

    def state(self) -> PLCState:
        with self._lock:
            registers = list(self._registers)
            connected = self._connected
            source = self._source
            age = None if self._sample_time is None else monotonic() - self._sample_time
            quality = 'unavailable' if age is None else 'stale' if age > self.config.stale_after_seconds else 'good'
            if not connected:
                quality = 'unavailable'
            sample_timestamp = self._sample_timestamp
            sequence = self._sequence

        fault_code = registers[14]
        return PLCState(
            timestamp=utc_now(),
            connected=connected,
            sample_timestamp=sample_timestamp,
            sample_age_seconds=age,
            data_quality=quality,
            sequence=sequence,
            source=source,
            registers={REGISTER_MAP[index]: registers[index] for index in range(REGISTER_COUNT)},
            raw_registers=registers,
            machine_state=detect_machine_state(registers),
            active_fault_code=fault_code,
            active_fault_name=FAULT_NAMES.get(fault_code, "Unknown fault"),
        )

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def _stamp_sample(self):
        self._sample_time = monotonic()
        self._sample_timestamp = utc_now()
        self._sequence += 1
        self._engine.observe(self._sequence, self._sample_time, self._registers)

    def root_cause(self):
        with self._lock:
            state = self.state()
            return self._engine.analyze(state.connected and state.data_quality == 'good')

    def set_simulation(self, scenario: str, seed: int = 1):
        if not self.config.use_mock_plc:
            raise PermissionError('Simulation controls are disabled for live PLC connections')
        simulator = CellSimulator(scenario=scenario, seed=seed)
        with self._lock:
            self._simulator = simulator
            self._source = 'simulation'
            self._engine.reset()
            self._registers = simulator.scan()
            self._connected = True
            self._stamp_sample()
            self._evaluate_fault_transition()
            self._record_event('simulation_started', f'Scenario={scenario}, seed={seed}')
        return self.state()

    def set_mock_scenario(self, scenario: str) -> PLCState:
        if not self.config.use_mock_plc:
            raise PermissionError('Mock controls are disabled for live PLC connections')
        if scenario not in MOCK_SCENARIOS:
            raise ValueError(f"Unsupported scenario '{scenario}'.")

        with self._lock:
            self._registers = [int(value) for value in MOCK_SCENARIOS[scenario]]
            self._connected = True
            self._source = "mock"
            self._simulator = None
            self._engine.reset()
            self._stamp_sample()
            self._evaluate_fault_transition()
            self._record_event("mock_scenario_loaded", f"Loaded mock scenario '{scenario}'.")

        return self.state()

    async def poll_forever(self) -> None:
        while True:
            interval = self.config.poll_interval_seconds
            try:
                if self.config.use_mock_plc:
                    with self._lock:
                        if self._simulator:
                            self._registers = self._simulator.scan()
                            interval = self._simulator.scan_seconds
                        self._stamp_sample()
                        self._evaluate_fault_transition()
                else:
                    await asyncio.to_thread(self.poll_modbus_once)
            except Exception:
                self._set_connection_state(False)
                logger.exception("PLC polling cycle failed")
            await asyncio.sleep(interval)

    def poll_modbus_once(self) -> None:
        with self._io_lock:
            try:
                if not self._client.connected and not self._client.connect():
                    self._set_connection_state(False)
                    return
                response = self._client.read_holding_registers(
                    address=0, count=REGISTER_COUNT, slave=self.config.plc_unit_id)
                if response.isError():
                    raise ValueError(str(response))
                values = list(response.registers)
                if len(values) != REGISTER_COUNT or any(type(v) is not int or not 0 <= v <= 65535 for v in values):
                    raise ValueError('Invalid register frame')
                if any(values[i] not in (0, 1) for i in range(13) if i != 2):
                    raise ValueError('Invalid boolean register')
                with self._lock:
                    self._registers = values
                    self._set_connection_state(True)
                    self._source = 'modbus'
                    self._stamp_sample()
                    self._evaluate_fault_transition()
            except Exception as exc:
                self._set_connection_state(False)
                self._client.close()
                self._record_event('modbus_error', str(exc))

    def close(self):
        with self._io_lock:
            self._client.close()

    def _set_connection_state(self, connected: bool) -> None:
        with self._lock:
            if self._connected != connected:
                state = "connected" if connected else "disconnected"
                self._record_event("connection_state_changed", f"PLC Modbus client {state}.")
            self._connected = connected
            if not connected:
                self._engine.reset()

    def _evaluate_fault_transition(self) -> None:
        with self._lock:
            fault_code = self._registers[14]
            if fault_code != self._last_fault_code:
                fault_name = FAULT_NAMES.get(fault_code, "Unknown fault")
                self._record_event("fault_code_changed", f"Fault changed to {fault_code}: {fault_name}.")
                self._last_fault_code = fault_code

    def _record_event(self, event_type: str, message: str) -> None:
        with self._lock:
            self._events.appendleft({
                "timestamp": utc_now(),
                "type": event_type,
                "message": message,
            })


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

    if not state.connected or state.data_quality != "good":
        findings.append(
            DiagnosticFinding(
                severity="critical",
                code=-1,
                title="PLC communication unavailable",
                evidence=[f"Source {state.source}: connected={state.connected}, quality={state.data_quality}."],
                recommendation="Verify PLC power, IP address, Modbus TCP server configuration, firewall rules, and network cabling.",
            )
        )

    if findings:
        # Old register values remain visible for inspection, never as current diagnosis.
        return DiagnosticsResponse(timestamp=utc_now(), healthy=False,
            machine_state='Unknown - telemetry unavailable', active_fault_code=fault_code,
            findings=findings)

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
async def lifespan(app):
    task = asyncio.create_task(bridge.poll_forever())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(bridge.close)


app = FastAPI(
    lifespan=lifespan,
    title="Resolvyn",
    description="Industrial diagnostics bridge for CODESYS, Modbus TCP, FastAPI, and ESP32 demos.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    state = bridge.state()
    return {
        "status": "ok" if state.connected and state.data_quality == "good" else "degraded",
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
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "supported_scenarios": sorted(MOCK_SCENARIOS)},
        ) from exc


@app.get('/api/root-cause')
async def api_root_cause():
    return bridge.root_cause()


@app.post('/api/simulation', response_model=PLCState)
async def api_simulation(request: SimulationRequest):
    try:
        return bridge.set_simulation(request.scenario, request.seed)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={'message': str(exc), 'scenarios': SCENARIOS}) from exc


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
                    "root_cause": bridge.root_cause(),
                }
            )
            await asyncio.sleep(config.poll_interval_seconds)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "bridge:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
