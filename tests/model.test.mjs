import test from "node:test";
import assert from "node:assert/strict";
import {
  usable,
  current,
  validState,
  sourceLabel,
} from "../frontend/model.mjs";
const state = {
  connected: true,
  source: "modbus",
  registers: { Motor_Current: 95 },
  raw_registers: Array(15).fill(0),
  machine_state: "Conveyor - Faulted",
};
test("scales raw motor current into amps", () =>
  assert.equal(current(state), 9.5));
test("disconnected retained registers are never live", () => {
  const s = { ...state, connected: false };
  assert.equal(usable(s), false);
  assert.equal(current(s), null);
});
test("mock source is explicitly labeled", () =>
  assert.equal(
    sourceLabel({ ...state, source: "mock" }),
    "Demo · backend mock",
  ));
test("missing and unknown sources are unavailable", () => {
  assert.equal(current(null), null);
  assert.equal(usable({ ...state, source: "other" }), false);
});
test("rejects malformed register payloads", () => {
  assert.ok(validState(state));
  assert.ok(!validState({ ...state, raw_registers: [1] }));
  assert.ok(!validState({ ...state, raw_registers: Array(15).fill("1") }));
});
