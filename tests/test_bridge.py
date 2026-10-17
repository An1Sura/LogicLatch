from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
import bridge as module
from bridge import BridgeConfig, PLCBridge, run_diagnostics


def live():
    return PLCBridge(BridgeConfig(use_mock_plc=False))


def test_live_cannot_switch_to_mock_or_simulation():
    b = live()
    for action in (lambda: b.set_mock_scenario('idle'), lambda: b.set_simulation('conveyor_jam')):
        with pytest.raises(PermissionError):
            action()
    assert b.state().source == 'modbus'


@pytest.mark.parametrize('registers', [[0]*14, [0]*16, [2]*15, [-1]*15, [70000]*15])
def test_malformed_frames_rejected(registers):
    b = live()
    b._client = Mock(connected=True)
    b._client.read_holding_registers.return_value = Mock(isError=lambda: False, registers=registers)
    b.poll_modbus_once()
    assert b.state().data_quality == 'unavailable'
    assert not run_diagnostics(b.state()).healthy


def test_transport_exception_invalidates_previous_sample():
    b = live()
    b._client = Mock(connected=True)
    b._client.read_holding_registers.return_value = Mock(isError=lambda: False, registers=[int(v) for v in module.MOCK_SCENARIOS['idle']])
    b.poll_modbus_once()
    assert b.state().connected
    old = b.state().sample_timestamp
    assert old is not None
    b._client.read_holding_registers.side_effect = TimeoutError('timeout')
    b.poll_modbus_once()
    assert not b.state().connected
    assert b.state().sample_timestamp == old
    assert b.root_cause()['status'] == 'data_unavailable'


def test_stale_age_is_not_refreshed_by_api_reads():
    b = PLCBridge(BridgeConfig())
    b._sample_time -= 10
    state = b.state()
    assert state.data_quality == 'stale'
    assert b.state().sample_timestamp == state.sample_timestamp
    assert not run_diagnostics(state).healthy
    assert b.root_cause()['status'] == 'data_unavailable'


def test_mock_transition_records_fault_event():
    b = PLCBridge(BridgeConfig())
    b.set_mock_scenario('conveyor_jam')
    assert any(e['type'] == 'fault_code_changed' for e in b.events())


def test_api_guard_and_simulation(monkeypatch):
    b = live()
    monkeypatch.setattr(module, 'bridge', b)
    client = TestClient(module.app)
    assert client.post('/api/mock/scenario', json={'scenario': 'idle'}).status_code == 403
    assert client.post('/api/simulation', json={}).status_code == 403
    b = PLCBridge(BridgeConfig())
    monkeypatch.setattr(module, 'bridge', b)
    assert client.post('/api/simulation', json={'scenario': 'invalid'}).status_code == 400
    assert client.post('/api/simulation', json={'scenario': 'conveyor_jam'}).json()['source'] == 'simulation'
    assert client.get('/api/root-cause').json()['trained_model'] is False


def test_lifespan_and_websocket(monkeypatch):
    b = PLCBridge(BridgeConfig())
    monkeypatch.setattr(module, 'bridge', b)
    with TestClient(module.app) as client:
        assert client.get('/health').json()['status'] == 'ok'
        with client.websocket_connect('/ws/live') as ws:
            data = ws.receive_json()
            assert 'root_cause' in data and data['state']['data_quality'] == 'good'
