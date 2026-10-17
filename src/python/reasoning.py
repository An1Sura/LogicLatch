"""Evidence-ranked diagnostic assistance; no trained model or causal certainty.

No Fault_Code labels are used to infer causes. Repeated HTTP reads do not count
as new evidence. Observation time, freshness and operating mode gate rules.
"""
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    sequence: int
    time: float
    registers: tuple[int, ...]


class EvidenceEngine:
    def __init__(self, max_gap: float = 1.5):
        self.history = deque(maxlen=120)
        self.max_gap = max_gap
        self.last_supported = None

    def observe(self, sequence: int, time: float, registers: list[int]):
        if self.history and sequence <= self.history[-1].sequence:
            return
        if self.history and (time <= self.history[-1].time or time - self.history[-1].time > self.max_gap):
            self.history.clear()
        self.history.append(Observation(sequence, time, tuple(registers)))
        current = self.analyze()
        if current['hypotheses']:
            self.last_supported = {'sequence': sequence, 'observed_at_monotonic': time,
                'registers': list(registers), 'hypotheses': current['hypotheses']}


    def reset(self):
        self.history.clear()
        self.last_supported = None

    def analyze(self, usable: bool = True) -> dict:
        result = {'method': 'temporal_rules_v1', 'trained_model': False,
                  'status': 'insufficient_evidence', 'hypotheses': [],
                  'last_supported_observation': self.last_supported}
        if not usable or not self.history:
            result['status'] = 'data_unavailable'
            return result
        last = self.history[-1]
        r = last.registers

        def held(predicate, duration):
            earliest = last.time
            for sample in reversed(self.history):
                if not predicate(sample.registers):
                    break
                earliest = sample.time
            return predicate(r) and last.time - earliest >= duration - 1e-9

        def add(cause, support, evidence, alternatives, checks):
            result['hypotheses'].append(dict(cause=cause, support=support,
                evidence=evidence, alternatives=alternatives, next_checks=checks,
                observation_sequence=last.sequence))

        if not r[3]:
            add('safety_chain_open', 'direct_signal', ['Safety_OK=0'],
                ['Guard open', 'E-stop operated', 'Safety input or wiring problem'],
                ['Have authorized personnel inspect the safety circuit; do not bypass it.'])
        if r[13] == 30 and r[7] and r[8]:
            add('level_feedback_conflict', 'direct_signal', ['Both level alarm switches are active'],
                ['Switch failure', 'Wiring error', 'Incorrect tag semantics'],
                ['Confirm switch semantics and compare independent level measurement.'])
        running_stable = held(lambda v: v[13] == 10 and v[0], 0.5)
        if running_stable and held(lambda v: v[13] == 10 and v[0] and v[1] and v[2] >= 80, 0.2):
            add('conveyor_obstruction', 'corroborated', ['Running + blocked photoeye + current >=8 A for >=0.2 s'],
                ['Mechanical load fault with occupied photoeye', 'Correlated sensor faults'],
                ['Review pre-trip trend and inspect the load path under site isolation procedure.'])
        elif running_stable and held(lambda v: v[13] == 10 and v[0] and v[2] >= 80, 0.2):
            add('motor_load_abnormal', 'corroborated', ['Running + current >=8 A for >=0.2 s'],
                ['Mechanical drag', 'Electrical motor fault', 'Current scaling error'],
                ['Compare drive diagnostics, phase currents and mechanical load.'])
        if held(lambda v: v[13] == 10 and v[0] and v[1] and v[2] < 80, 1.0):
            add('photoeye_persistently_blocked', 'suggestive', ['Blocked >=1 s with current <8 A'],
                ['Stationary product', 'Dirty or misaligned photoeye', 'Input stuck high'],
                ['Compare physical product position with sensor LED and PLC input.'])
        if r[12]:
            add('ventilation_fault_signal', 'direct_signal', ['HVAC_Fault=1'],
                ['Drive trip', 'Airflow permissive loss'], ['Inspect fan feedback and drive fault history.'])
        if r[4] and r[5]:
            add('command_conflict', 'direct_signal', ['Start_Command=1 and Stop_Command=1'],
                ['HMI latch', 'Input mapping error'], ['Review command source and arbitration.'])
        if result['hypotheses']:
            result['status'] = 'hypotheses_available'
        elif r[10] or r[14] or r[9]:
            result['status'] = 'unknown_fault'
        elif len(self.history) > 1:
            result['status'] = 'no_supported_hypothesis'
        return result
