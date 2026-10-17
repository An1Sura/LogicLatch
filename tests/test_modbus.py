"""Actual TCP request/response integration, including zero-based addressing."""
import asyncio
from pymodbus.client import ModbusTcpClient
from pymodbus.server import ModbusTcpServer
from simulator_server import make_context
from simulation import CellSimulator
from bridge import PLCBridge, BridgeConfig


def test_tcp_registers_read_only_and_bridge_recovery():
    async def exercise():
        sim = CellSimulator('pump_station_failure', fault_after=0)
        device, context = make_context(sim)
        server = ModbusTcpServer(context, address=('127.0.0.1', 0))
        await server.serve_forever(background=True)
        port = server.transport.sockets[0].getsockname()[1]
        b = PLCBridge(BridgeConfig(use_mock_plc=False, plc_port=port))
        client = ModbusTcpClient('127.0.0.1', port=port)
        try:
            await asyncio.to_thread(b.poll_modbus_once)
            state = b.state()
            assert state.connected and state.raw_registers[14] == 201
            assert state.raw_registers[13] == 30 and state.raw_registers[0] == 0
            assert b.root_cause()['hypotheses'][0]['cause'] == 'level_feedback_conflict'
            assert await asyncio.to_thread(client.connect)
            response = await asyncio.to_thread(client.write_register, 14, 0, slave=1)
            assert response.isError()
            response = await asyncio.to_thread(client.read_holding_registers, 14, count=2, slave=1)
            assert response.isError()
            # Shut down the actual socket and reconnect to a restarted server.
            await server.shutdown()
            await asyncio.to_thread(b.poll_modbus_once)
            assert not b.state().connected
            assert b.root_cause()['status'] == 'data_unavailable'
            server = ModbusTcpServer(context, address=('127.0.0.1', port))
            await server.serve_forever(background=True)
            device.setValues(3, 0, CellSimulator('healthy_conveyor').scan())
            await asyncio.to_thread(b.poll_modbus_once)
            assert b.state().connected and b.state().source == 'modbus'
            assert b.state().raw_registers[14] == 0
            assert any('connected' in e['message'] for e in b.events())
        finally:
            client.close()
            b.close()
            await server.shutdown()
    asyncio.run(exercise())
