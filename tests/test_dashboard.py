import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/python'))
from fastapi.testclient import TestClient
from bridge import app, bridge, MOCK_SCENARIOS

class DashboardContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
    def tearDown(self):
        bridge.set_mock_scenario('idle')
    def test_dashboard_and_assets(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Network topology', response.text)
        for asset in ['app.js', 'style.css', 'model.mjs']:
            self.assertEqual(self.client.get('/dashboard/' + asset).status_code, 200)
    def test_all_scenario_contracts(self):
        for scenario in MOCK_SCENARIOS:
            response = self.client.post('/api/mock/scenario', json={'scenario':scenario})
            self.assertEqual(response.status_code, 200)
            state = self.client.get('/api/state').json()
            self.assertEqual(state['source'], 'mock')
            self.assertEqual(len(state['raw_registers']), 15)
            self.assertEqual(len(state['registers']), 15)
            diagnostics = self.client.get('/api/diagnostics').json()
            self.assertEqual(diagnostics['active_fault_code'], state['active_fault_code'])
            self.assertTrue(diagnostics['findings'])
        self.assertTrue(self.client.get('/api/events').json()['events'])
    def test_invalid_scenario(self):
        self.assertEqual(self.client.post('/api/mock/scenario', json={'scenario':'invalid'}).status_code,400)
    def test_websocket_contract_preserved(self):
        with self.client.websocket_connect('/ws/live') as ws:
            payload=ws.receive_json()
            self.assertIn('state',payload)
            self.assertIn('diagnostics',payload)

if __name__ == '__main__':
    unittest.main()
