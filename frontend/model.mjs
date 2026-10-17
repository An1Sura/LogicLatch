export function usable(state) {
  return Boolean(state?.connected && ["mock", "modbus"].includes(state.source));
}
export function current(state) {
  return usable(state) && Number.isFinite(state.registers?.Motor_Current)
    ? state.registers.Motor_Current / 10
    : null;
}
export function validState(s) {
  return (
    s &&
    typeof s.connected === "boolean" &&
    typeof s.source === "string" &&
    Array.isArray(s.raw_registers) &&
    s.raw_registers.length === 15 &&
    s.raw_registers.every(Number.isFinite) &&
    s.registers &&
    typeof s.machine_state === "string"
  );
}
export function sourceLabel(s) {
  return !s
    ? "Unavailable"
    : s.source === "mock"
      ? "Demo · backend mock"
      : s.source === "modbus"
        ? "PLC · Modbus TCP"
        : "Unknown source";
}
