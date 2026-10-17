import { usable, current, validState, sourceLabel } from "./model.mjs";
const $ = (id) => document.getElementById(id);
let state = null,
  diagnostics = null,
  events = [],
  selected = "plc",
  samples = [],
  busy = false,
  scenarioBusy = false,
  lastSource = null;
const text = (id, value) => ($(id).textContent = value);
const el = (tag, content, className) => {
  const n = document.createElement(tag);
  n.textContent = content;
  if (className) n.className = className;
  return n;
};
async function request(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    signal: AbortSignal.timeout(5000),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Bridge returned HTTP ${r.status}`);
  return r.json();
}
function showView(view) {
  document.querySelectorAll(".view").forEach((n) => (n.hidden = n.id !== view));
  document.querySelectorAll("[data-view]").forEach((n) => {
    n.classList.toggle("active", n.dataset.view === view);
    if (n.getAttribute("role") === "tab")
      n.setAttribute("aria-selected", String(n.dataset.view === view));
  });
}
document
  .querySelectorAll("[data-view]")
  .forEach((n) => (n.onclick = () => showView(n.dataset.view)));
document.querySelectorAll("[data-device]").forEach(
  (n) =>
    (n.onclick = () => {
      selected = n.dataset.device;
      renderDetail();
    }),
);
function row(label, value) {
  const d = el("div", "");
  d.append(el("dt", label), el("dd", String(value)));
  return d;
}
function renderDetail() {
  const ok = usable(state),
    r = state?.registers || {};
  const data = {
    plc: [
      "CODESYS PLC",
      "The control-layer source for all 15 holding registers. Connection reflects the bridge’s reported PLC status.",
      [
        [
          "Transport",
          state?.source === "mock" ? "Simulated registers" : "Modbus TCP",
        ],
        ["Register range", "0–14"],
        ["Machine state", ok ? state.machine_state : "Unavailable"],
        ["Fault code", ok ? state.active_fault_code : "—"],
      ],
    ],
    bridge: [
      "FastAPI bridge",
      "Polls the PLC and translates register values into state, diagnostics, and a rolling event history.",
      [
        ["API", state ? "Responding" : "Unavailable"],
        ["Source", sourceLabel(state)],
        ["Dashboard refresh", "2 seconds"],
        ["History", "Up to 200 backend events"],
      ],
    ],
    conveyor: [
      "Conveyor drive",
      "A logical signal group derived from PLC registers. No separate drive connection is exposed by the API.",
      [
        ["Motor current", ok ? `${current(state)} A` : "—"],
        ["Running", ok ? String(Boolean(r.Conveyor_Running)) : "—"],
        ["Sensor blocked", ok ? String(Boolean(r.Sensor_Blocked)) : "—"],
        ["Register addresses", "0 / 1 / 2"],
      ],
    ],
    safety: [
      "Safety circuit",
      "Safety feedback reported by the PLC. This dashboard is an observation tool; it does not issue motion or reset commands.",
      [
        ["Safety_OK", ok ? String(Boolean(r.Safety_OK)) : "—"],
        ["Sequence timeout", ok ? String(Boolean(r.Sequence_Timeout)) : "—"],
        ["Fault latch", ok ? String(Boolean(r.System_Fault_Latch)) : "—"],
        ["Register addresses", "3 / 9 / 10"],
      ],
    ],
    esp: [
      "ESP32 module",
      "The ESP32 independently polls the same PLC over WiFi and Modbus TCP. Its firmware prints readings to Serial; the bridge has no ESP32 telemetry endpoint.",
      [
        ["Connection", "Unobserved"],
        ["Telemetry", "Not exposed"],
        ["Firmware output", "Serial · 115200 baud"],
        ["Protocol", "WiFi / Modbus TCP"],
      ],
    ],
  }[selected];
  text("detail-title", data[0]);
  text("detail-description", data[1]);
  text(
    "detail-status",
    selected === "esp"
      ? "Unobserved"
      : selected === "bridge"
        ? state
          ? "API responding"
          : "Unavailable"
        : ok
          ? state.source === "mock"
            ? "Demo signal"
            : "PLC signal"
          : "Unavailable",
  );
  $("detail-values").replaceChildren(...data[2].map((x) => row(...x)));
  document.querySelectorAll("[data-device]").forEach((n) => {
    n.classList.toggle("selected", n.dataset.device === selected);
    n.setAttribute("aria-pressed", String(n.dataset.device === selected));
  });
  const chart = $("chart");
  chart.replaceChildren();
  if (samples.length) {
    const max = Math.max(10, ...samples);
    const line = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "polyline",
    );
    line.setAttribute(
      "points",
      samples
        .map(
          (s, i) =>
            `${samples.length === 1 ? 140 : (i * 280) / (samples.length - 1)},${85 - (s / max) * 75}`,
        )
        .join(" "),
    );
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#c8f873");
    line.setAttribute("stroke-width", "2");
    chart.append(line);
    if (samples.length === 1) {
      const dot = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "circle",
      );
      dot.setAttribute("cx", "140");
      dot.setAttribute("cy", String(85 - (samples[0] / max) * 75));
      dot.setAttribute("r", "3");
      dot.setAttribute("fill", "#c8f873");
      chart.append(dot);
    }
  }
  text(
    "chart-caption",
    samples.length
      ? `${samples.length} ${state?.source === "mock" ? "demo" : "PLC"} samples · up to 60`
      : "Awaiting connected samples",
  );
}
function render() {
  const ok = usable(state),
    r = state?.registers || {};
  text("connection", state ? "● API connected" : "○ API unavailable");
  text("source", sourceLabel(state));
  text(
    "plc",
    ok ? (state.source === "mock" ? "Simulated" : "Connected") : "Unavailable",
  );
  text("machine", ok ? state.machine_state : "Unavailable");
  text("fault", ok ? String(state.active_fault_code).padStart(3, "0") : "—");
  text(
    "fault-name",
    ok ? state.active_fault_name : "No reliable current reading",
  );
  text(
    "current",
    current(state) === null ? "— A" : `${current(state).toFixed(1)} A`,
  );
  text("bridge-status", state ? "API responding" : "Unavailable");
  text(
    "node-plc",
    ok
      ? state.source === "mock"
        ? "Demo · simulated"
        : "Connected · Modbus"
      : "Unavailable",
  );
  text(
    "node-conveyor",
    ok
      ? r.Conveyor_Running
        ? r.Sensor_Blocked && r.Motor_Current >= 80
          ? "Fault · jam / overload"
          : "Running"
        : "Stopped"
      : "Unavailable",
  );
  text(
    "node-safety",
    ok
      ? r.Safety_OK
        ? "Healthy signal"
        : "Fault · safety open"
      : "Unavailable",
  );
  for (const [id, good, bad] of [
    ["bridge", !!state, false],
    ["plc", ok, ok && !!r.System_Fault_Latch],
    [
      "conveyor",
      ok,
      ok && r.Conveyor_Running && r.Sensor_Blocked && r.Motor_Current >= 80,
    ],
    ["safety", ok, ok && !r.Safety_OK],
  ]) {
    const n = document.querySelector(`[data-device="${id}"]`);
    n.classList.toggle("good", Boolean(good && !bad));
    n.classList.toggle("bad", Boolean(bad));
  }
  $("load").disabled = !(ok && state.source === "mock") || scenarioBusy;
  text(
    "demo-help",
    state?.source === "mock"
      ? "Changes the backend mock scenario for all connected viewers."
      : "Disabled: the bridge must already be configured with USE_MOCK_PLC=true.",
  );
  $("registers").replaceChildren(
    ...(state
      ? Object.entries(state.registers).map(([tag, value], i) => {
          const tr = el("tr", "");
          tr.append(
            el("td", String(i).padStart(2, "0")),
            el("td", tag),
            el("td", ok ? String(value) : "Unavailable"),
          );
          return tr;
        })
      : [
          (() => {
            const tr = el("tr", "");
            const td = el("td", "Register telemetry unavailable");
            td.colSpan = 3;
            tr.append(td);
            return tr;
          })(),
        ]),
  );
  text(
    "finding-count",
    diagnostics
      ? diagnostics.findings.filter((f) => f.severity !== "info").length
      : "—",
  );
  $("findings").replaceChildren(
    ...(diagnostics
      ? diagnostics.findings.map((f) => {
          const n = el(
            "article",
            "",
            `finding ${f.severity === "info" ? "info" : ""}`,
          );
          n.append(
            el("small", `${f.severity.toUpperCase()} / ${f.code}`),
            el("h3", f.title),
          );
          const ul = el("ul", "");
          f.evidence.forEach((e) => ul.append(el("li", e)));
          n.append(ul, el("p", f.recommendation));
          return n;
        })
      : [el("p", "Diagnostics unavailable. Reconnect to the bridge.")]),
  );
  $("events").replaceChildren(
    ...(events.length
      ? events.slice(0, 6).map((e) => {
          const n = el("div", "", "event");
          const d = new Date(e.timestamp);
          n.append(
            el(
              "time",
              Number.isNaN(d.getTime())
                ? "—"
                : d.toLocaleTimeString([], { hour12: false }),
            ),
            el("span", e.message),
          );
          return n;
        })
      : [
          el(
            "p",
            state ? "No events available." : "Event history unavailable.",
          ),
        ]),
  );
  renderDetail();
}
async function refresh() {
  if (busy) return;
  busy = true;
  $("refresh").disabled = true;
  try {
    const results = await Promise.allSettled([
      request("/api/state"),
      request("/api/diagnostics"),
      request("/api/events"),
    ]);
    if (results[0].status !== "fulfilled" || !validState(results[0].value))
      throw new Error("State endpoint unavailable or invalid");
    state = results[0].value;
    diagnostics =
      results[1].status === "fulfilled" &&
      Array.isArray(results[1].value.findings)
        ? results[1].value
        : null;
    events =
      results[2].status === "fulfilled" &&
      Array.isArray(results[2].value.events)
        ? results[2].value.events
        : [];
    if (lastSource !== state.source || !usable(state)) samples = [];
    lastSource = state.source;
    if (current(state) !== null)
      samples = [...samples, current(state)].slice(-60);
    text(
      "updated",
      `API response ${new Date().toLocaleTimeString()} · 2s refresh`,
    );
    text(
      "notice",
      !usable(state)
        ? "PLC unavailable — current values are hidden. Backend diagnostics may include retained register values."
        : state.source === "mock"
          ? "DEMO MODE — simulated PLC registers from the backend. No live hardware telemetry is shown."
          : "PLC SOURCE — values reported by the Modbus bridge. API response time is not a PLC acquisition timestamp.",
    );
    if (!diagnostics || results[2].status !== "fulfilled")
      $("notice").append(" Some diagnostic or event data is unavailable.");
  } catch (e) {
    state = null;
    diagnostics = null;
    events = [];
    samples = [];
    text(
      "notice",
      `Bridge unavailable — ${e.message}. Retrying automatically; no demo fallback is used.`,
    );
    text("updated", "No current API response");
  } finally {
    busy = false;
    $("refresh").disabled = false;
    render();
  }
}
$("refresh").onclick = refresh;
$("load").onclick = async () => {
  if (state?.source !== "mock" || !usable(state) || scenarioBusy) return;
  scenarioBusy = true;
  render();
  try {
    await request("/api/mock/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: $("scenario").value }),
    });
    await refresh();
  } catch (e) {
    text("notice", `Scenario could not be loaded: ${e.message}`);
  } finally {
    scenarioBusy = false;
    render();
  }
};
render();
refresh();
setInterval(refresh, 2000);
