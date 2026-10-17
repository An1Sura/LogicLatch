import pytest
from simulation import CellSimulator, SCENARIOS
from reasoning import EvidenceEngine


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_reproducible_register_contract(scenario):
    a, b = CellSimulator(scenario), CellSimulator(scenario)
    for _ in range(200):
        r = a.scan()
        assert r == b.scan()
        assert len(r) == 15 and all(type(v) is int and 0 <= v <= 65535 for v in r)
        assert all(r[i] in (0, 1) for i in range(13) if i != 2)


def test_latch_reset_and_stop_priority():
    sim = CellSimulator('conveyor_jam', fault_after=1)
    for _ in range(50):
        r = sim.scan()
    assert r[14] == 601 and r[0] == 0
    sim.reset, sim.stop, sim.start = True, True, False
    assert sim.scan()[14] == 601  # Cause remains.
    sim.injection_enabled = False
    assert sim.scan()[14] == 601  # Held reset is not a new edge.
    sim.reset = False
    sim.scan()
    sim.reset = True
    r = sim.scan()
    assert r[14] == 0 and r[0] == 0
    sim.start = True
    assert sim.scan()[0] == 0  # Stop wins.
    sim.stop = False
    assert sim.scan()[0] == 1


def test_safety_trip_removes_output_in_same_scan():
    sim = CellSimulator('safety_trip', fault_after=0)
    r = sim.scan()
    assert (r[0], r[3], r[10], r[14]) == (0, 0, 1, 501)


def test_pump_changes_level_and_stops_at_low_switch():
    sim = CellSimulator('healthy_pump')
    original = sim.level
    for _ in range(120):
        r = sim.scan()
    assert sim.level < original
    assert not (r[7] and r[8])
    sim.level = 0.1
    assert sim.scan()[11] == 0


def test_detection_ignores_fault_code_and_debounces():
    engine = EvidenceEngine()
    r = [1, 1, 90, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 10, 999]
    for i in range(12):
        engine.observe(i, i * .05, r)
    assert engine.analyze()['hypotheses'][0]['cause'] == 'conveyor_obstruction'
    engine.reset()
    engine.observe(1, 0, r)
    for _ in range(100):
        engine.observe(1, 10, r)
    assert engine.analyze()['hypotheses'] == []
    assert engine.analyze(False)['status'] == 'data_unavailable'


def test_gap_and_mode_change_break_persistence():
    engine = EvidenceEngine(max_gap=.2)
    r = [1, 1, 90, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 10, 0]
    for i in range(4):
        engine.observe(i, i*.05, r)
    engine.observe(4, 2, r)
    assert not engine.analyze()['hypotheses']
    r[13] = 30
    for i in range(5, 30):
        engine.observe(i, 2+(i-4)*.05, r)
    assert not engine.analyze()['hypotheses']


def test_unknown_fault_is_not_healthy_or_invented():
    engine = EvidenceEngine()
    engine.observe(1, 1, [0,0,0,1,0,0,0,0,0,0,1,0,0,10,999])
    assert engine.analyze()['status'] == 'unknown_fault'


def test_pretrip_evidence_survives_output_stop():
    sim = CellSimulator('conveyor_jam')
    engine = EvidenceEngine()
    for i in range(100):
        r = sim.scan()
        engine.observe(i, sim.elapsed, r)
    result = engine.analyze()
    assert r[0] == 0 and r[14] == 601
    assert result['status'] == 'unknown_fault'
    assert result['last_supported_observation']['hypotheses'][0]['cause'] == 'conveyor_obstruction'
    assert result['last_supported_observation']['sequence'] < 99
