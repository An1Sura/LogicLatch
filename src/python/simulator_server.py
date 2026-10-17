"""Loopback Modbus TCP simulator using the production bridge's register map."""
import argparse
import asyncio
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.server import ModbusTcpServer
from simulation import CellSimulator, SCENARIOS


class ReadOnlyContext(ModbusSlaveContext):
    def validate(self, fc, address, count=1):
        return fc == 3 and super().validate(fc, address, count)


def make_context(simulator):
    # PyModbus 3.8 adds one to PDU addresses in ModbusSlaveContext.
    device = ReadOnlyContext(hr=ModbusSequentialDataBlock(1, simulator.scan()))
    return device, ModbusServerContext(slaves={1: device}, single=False)


async def serve(scenario, port, seed):
    sim = CellSimulator(scenario=scenario, seed=seed)
    device, context = make_context(sim)
    server = ModbusTcpServer(context, address=('127.0.0.1', port))

    async def scan_loop():
        while True:
            await asyncio.sleep(sim.scan_seconds)
            device.setValues(3, 0, sim.scan())

    scanning = asyncio.create_task(scan_loop())
    try:
        await server.serve_forever()
    finally:
        scanning.cancel()
        await asyncio.gather(scanning, return_exceptions=True)
        await server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='healthy_conveyor')
    parser.add_argument('--port', type=int, default=1502)
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    asyncio.run(serve(args.scenario, args.port, args.seed))
