"""Reproducible synthetic episode benchmark; not field accuracy validation."""
import json
from simulation import CellSimulator
from reasoning import EvidenceEngine

EXPECTED = {'healthy_conveyor': None, 'healthy_pump': None,
    'conveyor_jam': 'conveyor_obstruction', 'photoeye_stuck': 'photoeye_persistently_blocked',
    'motor_overload': 'motor_load_abnormal', 'safety_trip': 'safety_chain_open',
    'pump_station_failure': 'level_feedback_conflict'}


def evaluate(seeds=range(10)):
    episodes = []
    for seed in seeds:
        for scenario, expected in EXPECTED.items():
            sim = CellSimulator(scenario=scenario, seed=seed)
            engine = EvidenceEngine()
            predictions = []
            first_detection = None
            baseline_positive = False
            for i in range(160):
                r = sim.scan()
                engine.observe(i, sim.elapsed, r)
                causes = [h['cause'] for h in engine.analyze()['hypotheses']]
                predictions.extend(causes)
                # Old bridge's instantaneous physical-condition rules (no label oracle).
                baseline_positive |= bool(r[0] and r[1] and r[2] >= 80 or
                                          r[11] and r[7] and r[8] or not r[3])
                if expected in causes and first_detection is None:
                    first_detection = round(sim.elapsed - sim.fault_after, 3)
            episodes.append(dict(seed=seed, scenario=scenario, expected=expected,
                detected=expected in predictions if expected else False,
                false_positive=any(c != expected for c in predictions),
                latency_seconds=first_detection, baseline_alert=baseline_positive))
    faults = [e for e in episodes if e['expected']]
    healthy = [e for e in episodes if not e['expected']]
    tp = sum(e['detected'] for e in faults)
    fp = sum(e['false_positive'] for e in episodes)
    return dict(dataset='synthetic_v1; 8 s episodes, 50 ms scans, injection at 3 s',
        episodes=len(episodes), true_positive_episodes=tp, false_positive_episodes=fp,
        precision=tp/(tp+fp) if tp+fp else 0, recall=tp/len(faults),
        healthy_false_alarm_rate=sum(e['false_positive'] for e in healthy)/len(healthy),
        baseline_healthy_false_alarm_rate=sum(e['baseline_alert'] for e in healthy)/len(healthy),
        max_detection_latency_seconds=max(e['latency_seconds'] or 0 for e in faults),
        details=episodes)


if __name__ == '__main__':
    print(json.dumps(evaluate(), indent=2))
