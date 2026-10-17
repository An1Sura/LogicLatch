"""Deterministic teaching model, not a CODESYS runtime or safety controller.

Each scan samples inputs, evaluates interlocks/timers, commits outputs, then
advances the plant. The legacy 15-register interface is preserved.
"""
from dataclasses import dataclass, field
import random

SCENARIOS = ('healthy_conveyor', 'conveyor_jam', 'photoeye_stuck',
             'motor_overload', 'safety_trip', 'healthy_pump', 'pump_station_failure')


@dataclass
class CellSimulator:
    scenario: str = 'healthy_conveyor'
    seed: int = 1
    scan_seconds: float = 0.05
    fault_after: float = 3.0
    elapsed: float = 0.0
    level: float = 0.6
    speed: float = 0.0
    start: bool = True
    stop: bool = False
    reset: bool = False
    injection_enabled: bool = True
    fault_code: int = 0
    _jam_time: float = 0.0
    _previous_reset: bool = False
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        if self.scenario not in SCENARIOS:
            raise ValueError(f'Unsupported simulation scenario: {self.scenario}')
        if not 0 < self.scan_seconds <= 0.1 or self.fault_after < 0:
            raise ValueError('scan_seconds must be (0, 0.1]; fault_after must be nonnegative')
        self._rng = random.Random(self.seed)

    def scan(self) -> list[int]:
        dt = self.scan_seconds
        injected = self.injection_enabled and self.elapsed >= self.fault_after
        pump_mode = self.scenario in ('healthy_pump', 'pump_station_failure')
        jam = injected and self.scenario == 'conveyor_jam'
        overload = injected and self.scenario == 'motor_overload'
        stuck_eye = injected and self.scenario == 'photoeye_stuck'
        safety = not (injected and self.scenario == 'safety_trip')
        conflict = injected and self.scenario == 'pump_station_failure'
        # Legacy switches mean low-level alarm and high-level alarm; both is invalid.
        low, high = self.level <= 0.2, self.level >= 0.8
        if conflict:
            low = high = True
        # Normal products occlude the photoeye for 0.2 s every 2 s.
        blocked = not pump_mode and (jam or stuck_eye or (self.speed > 0.5 and self.elapsed % 2 < 0.2))
        current = 0
        if not pump_mode and self.speed > 0.05:
            # Short inrush above 8 A deliberately exercises temporal diagnostics.
            amps = 9.0 if self.elapsed < 0.3 or jam or overload else 3.0
            current = max(0, round((amps + self._rng.uniform(-0.15, 0.15)) * 10))
        self._jam_time = self._jam_time + dt if jam or overload else 0.0
        active = 201 if conflict else 501 if not safety else 601 if self._jam_time >= 0.5 else 0
        rising_reset = self.reset and not self._previous_reset
        self._previous_reset = self.reset
        # Reset requires fault removed, explicit stop, and no start; never auto-restart.
        if rising_reset and not active and not jam and not overload and self.stop and not self.start:
            self.fault_code = 0
        if active:
            self.fault_code = active
        permitted = self.start and not self.stop and safety and not self.fault_code
        conveyor = permitted and not pump_mode
        pump = permitted and pump_mode and not low
        target = 0.0 if jam else float(conveyor)
        self.speed += (target - self.speed) * min(1.0, dt / 0.15)
        self.level = min(1.0, max(0.0, self.level + dt * (0.03 - 0.10 * pump)))
        self.elapsed = round(self.elapsed + dt, 9)
        return [int(conveyor), int(blocked), current, int(safety), int(self.start),
                int(self.stop), int(self.reset), int(low), int(high),
                int(self.fault_code == 601), int(bool(self.fault_code)), int(pump),
                0, 30 if pump_mode else 10, self.fault_code]
