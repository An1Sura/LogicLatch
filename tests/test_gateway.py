import importlib.util
import os
from pathlib import Path
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Keep the module's default database out of the checkout.
_bootstrap = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = str(Path(_bootstrap.name) / 'bootstrap.sqlite3')
import sys
spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'src/python/bridge.py')
bridge = importlib.util.module_from_spec(spec)
sys.modules['bridge'] = bridge
spec.loader.exec_module(bridge)
bridge.bridge.close()


@pytest.fixture
def receiver(tmp_path):
    result = bridge.PLCBridge(bridge.BridgeConfig(database_path=str(tmp_path / 'data.sqlite3'), ingest_token='test-token', use_mock_plc=False))
    yield result
    result.close()


def packet(**changes):
    values = dict(device_id='logiclatch-01', boot_id=1, sequence=1, uptime_ms=1000,
                  plc_connected=True, registers=[1, 1, 95, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 10, 601],
                  wifi_rssi=-50, free_heap=100000, queue_depth=0, dropped_samples=0)
    values.update(changes)
    return bridge.TelemetryRequest(**values)


def test_receives_and_diagnoses(receiver):
    assert not receiver.state().connected
    assert receiver.ingest(packet())['live_updated']
    state = receiver.state()
    assert state.source == 'esp32' and state.connected
    assert state.active_fault_code == 601
    assert state.gateway['wifi_rssi'] == -50
    assert any(f.title == 'Conveyor jam or overload' for f in bridge.run_diagnostics(state).findings)


def test_retries_do_not_duplicate_or_refresh(receiver):
    receiver.ingest(packet())
    before = receiver.state().gateway['received_at']
    assert receiver.ingest(packet(replayed=True))['duplicate']
    assert len(receiver.history(100)) == 1
    assert receiver.state().gateway['received_at'] == before


def test_replay_and_out_of_order_never_replace_live(receiver):
    receiver.ingest(packet(sequence=5))
    receiver.ingest(packet(sequence=4, registers=[0] * 15))
    receiver.ingest(packet(sequence=6, registers=[0] * 15, replayed=True))
    assert receiver.state().active_fault_code == 601
    assert receiver.state().gateway['sequence'] == 5
    assert len(receiver.history(100)) == 3


def test_replay_alone_is_not_live(receiver):
    receiver.ingest(packet(replayed=True))
    assert not receiver.state().connected
    assert receiver.state().gateway is None


def test_stale_samples_and_disconnection(receiver):
    receiver.ingest(packet(sample_age_ms=16000))
    assert not receiver.state().connected
    receiver.ingest(packet(sequence=2))
    stale = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    receiver._db.execute('UPDATE latest SET received_at=?', (stale,))
    receiver._db.commit()
    assert receiver.state().gateway['stale']
    assert not receiver.state().connected
    receiver.ingest(packet(sequence=3, plc_connected=False, registers=None))
    state = receiver.state()
    assert not state.connected and not state.gateway['stale']
    assert state.active_fault_code == 601  # Last known values, explicitly disconnected.


def test_restart_restores_history_and_sequence_guard(receiver):
    receiver.ingest(packet(boot_id=2))
    other = bridge.PLCBridge(receiver.config)
    try:
        assert other.state().active_fault_code == 601
        assert len(other.history(10)) == 1
        assert not other.ingest(packet(boot_id=1, sequence=100))['live_updated']
        assert other.ingest(packet(boot_id=3))['live_updated']
    finally:
        other.close()


@pytest.mark.parametrize('registers', [[0] * 14, [0] * 16, [-1] + [0] * 14, [65536] + [0] * 14, [True] + [0] * 14])
def test_invalid_registers(registers):
    with pytest.raises(ValidationError):
        packet(registers=registers)


def test_device_and_status_validation(receiver):
    with pytest.raises(ValueError):
        receiver.ingest(packet(device_id='other'))
    with pytest.raises(ValueError):
        receiver.ingest(packet(plc_connected=False))
    with pytest.raises(ValueError):
        receiver.ingest(packet(registers=None))


def test_history_is_bounded(tmp_path):
    instance = bridge.PLCBridge(bridge.BridgeConfig(database_path=str(tmp_path / 'bounded.db'), history_limit=2, use_mock_plc=False))
    try:
        for seq in range(1, 5):
            instance.ingest(packet(sequence=seq))
        assert [s['sequence'] for s in instance.history(10)] == [4, 3]
    finally:
        instance.close()


def test_http_auth_validation_and_stream(receiver, monkeypatch):
    monkeypatch.setattr(bridge, 'bridge', receiver)
    monkeypatch.setattr(bridge, 'config', receiver.config)
    client = TestClient(bridge.app)
    payload = packet().model_dump()
    assert client.post('/api/telemetry', json=payload).status_code == 401
    headers = {'Authorization': 'Bearer test-token'}
    assert client.post('/api/telemetry', json=payload, headers=headers).status_code == 200
    assert client.get('/api/state').json()['source'] == 'esp32'
    assert client.get('/health').json()['plc_connected']
    assert len(client.get('/api/history').json()['samples']) == 1
    assert client.get('/api/history?limit=1001').status_code == 422
    assert client.post('/api/mock/scenario', json={'scenario': 'idle'}).status_code == 400
    payload['registers'] = [0]
    assert client.post('/api/telemetry', json=payload, headers=headers).status_code == 422
    with client.websocket_connect('/ws/live') as ws:
        assert ws.receive_json()['state']['active_fault_code'] == 601


def test_unconfigured_and_mock_ingestion_rejected(receiver, monkeypatch):
    monkeypatch.setattr(bridge, 'bridge', receiver)
    client = TestClient(bridge.app)
    monkeypatch.setattr(bridge, 'config', replace(receiver.config, ingest_token=''))
    assert client.post('/api/telemetry', json=packet().model_dump()).status_code == 503
    monkeypatch.setattr(bridge, 'config', replace(receiver.config, use_mock_plc=True))
    assert client.post('/api/telemetry', json=packet().model_dump(), headers={'Authorization': 'Bearer test-token'}).status_code == 409
