# Resolvyn: implementation audit and phased architecture

## Scope and evidence

Audited baseline: LogicLatch commit `4b20f95`. Reviewed all tracked source,
configuration and README files; CAD STL assets were inventoried, not dimensionally
or mechanically validated. Reviewed the supplied Resolvyn Automation Lean Canvas
v1.0, designed by Anirudh Sura (date printed `05/01/26`, date convention unspecified).
The Canvas targets controls engineers, maintenance technicians and integrators;
its proposed product combines edge acquisition, fault detection, explanations and
an operator interface. This change establishes the acquisition/simulation and
explanation foundation. Canvas pricing, hardware costs and 20–40% downtime reduction
are hypotheses, not validated commercial or performance claims.

## Audit findings

| Baseline finding | Impact | Treatment in this branch |
|---|---|---|
| `MOCK_SCENARIOS` contains constant arrays | No scan timing, plant dynamics, progression or meaningful detection evaluation | Added seeded 50 ms process model and independent Modbus server; retained legacy fixtures |
| Mock setter changes `_source` even in live mode | A request can silently replace plant data with demo data | Live mode rejects mock and simulation mutations with 403 |
| `state()` generates a new timestamp for old registers | Old data can appear current | Separate response/sample time, age, sequence and quality; unavailable/stale diagnosis abstains |
| Connection/read exceptions leave previous connection flag true | False reassurance after communication loss | Failed and invalid polls invalidate availability, close transport, clear inference history |
| Synchronous Modbus calls run in async loop | Network timeouts stall HTTP/WebSocket service | Poll in worker thread; serialize client access and close it at lifespan shutdown |
| Fault history misses mock transitions; reconnect events missing | Incomplete troubleshooting history | Record fault and connection transitions; retain pre-trip supporting sample |
| Rules use current snapshots, latch and fault codes | Startup false alarms; no distinction between symptoms and causes | Separate temporal inference from legacy immediate alerts; no fault-code oracle in new cause inference |
| CODESYS reset clears HVAC/fault unconditionally | Physical cause can remain while latch clears | Guard reset with stop/no-start and cause checks; no longer clear HVAC input |
| No tests or CI | No evidence for claimed behavior | Added unit/API/TCP tests, synthetic benchmark, CI configuration |
| API bound to all interfaces, wildcard CORS | Broad exposure of unauthenticated demo controls | Default loopback binding and restricted local CORS; auth still not implemented |
| ESP32 only polls and prints registers | No additional physical measurements or inference | Renamed banner; documented optional role; firmware build/hardware unverified |
| ST files are snippets, not an exported CODESYS project | Cannot reproduce controller build from source alone | Explicit limitation; controller parity remains a release gate |

## Implemented architecture

```text
CellSimulator (seeded plant + scan model)
  ├─ in-process: POST /api/simulation → bridge sample/history
  └─ loopback Modbus TCP :1502 → unchanged 15-register contract → bridge poll
                                                                   ↓
                                               sample quality + immediate alerts
                                                                   ↓
                                               temporal evidence/hypotheses
                                                                   ↓
                                            HTTP API + WebSocket + /docs
```

Simulation supports conveyor startup/inrush, intermittent product photoeye pulses,
first-order speed response, current noise, tank mass balance, low-level pump stop,
jam/overload timers, safety trips, contradictory level switches, latched shutdown
and stop-gated reset. Fixed 50 ms simulation time is deterministic; wall-clock
execution is best effort. The pump model has no hysteresis and may cycle near the
low switch; this is a simplified plant, not validated process tuning. Register 0
continues to mean output command rather than independent motor feedback. Switches
7/8 mean low/high *alarms*, not two vertically stacked wet contacts. Confirm those
semantics for every real installation before enabling the conflict rule.

The separate Modbus server accepts FC03 reads only and preserves zero-based
addresses 0–14, unit 1. It binds localhost. The bridge never writes PLC registers.
The in-process scenario API is enabled only in mock configuration. There is no
remote reset/start API, cloud dependency, LLM invocation or trained model.

The simulator is not an interpreter of Structured Text. Traffic, elevator,
ventilation and garage scenarios remain legacy snapshots; dynamic implementations
are deliberately not claimed. The ST reset edit has not been compiled or run in
CODESYS and must be reviewed and verified there before use. Existing ST scenario
loader still sets outputs directly; it is not a machine control program or safety
system. The Python model is not a validated digital twin or safety controller.

## Diagnostic contract and measured verification

`/api/diagnostics` retains legacy instantaneous alerts for compatibility.
`/api/root-cause` adds temporal hypotheses with supporting evidence, qualitative
support, alternatives and next checks. These are possible explanations, not proven
root causes or calibrated probabilities. Codes/labels do not determine hypotheses.
For conveyor current rules, running must persist 0.5 s and abnormal current 0.2 s;
normal startup is excluded. A blocked eye at ordinary current must persist 1 s.
A safety signal or impossible level combination is reported immediately.

Sample sequence prevents duplicate reads becoming evidence. Non-monotonic times
or excessive gaps break persistence. Current inference abstains on stale or lost
telemetry; a fault without physical support returns `unknown_fault`. A separately
labelled `last_supported_observation` holds the pre-trip registers and hypotheses;
it is historical evidence, never proof a cause remains active. It is in memory,
cleared by explicit source reset/disconnection, and lost on restart. Monotonic time
is process-local, not a cross-machine timestamp.

Run `python -m pytest -q` and `python src/python/evaluate.py`. The checked-in
`evaluation.json` contains each episode, seed, expected cause, detection and latency.
At implementation time Python 3.12.14 passed 27 tests, including real loopback TCP,
zero-based register alignment, write rejection, malformed frames, transport errors,
stale timestamps, source-switch protection, WebSocket/lifecycle, reset sequencing,
mode/gap handling, deterministic replay and pre-trip retention. One upstream
Starlette/AnyIO deprecation warning remains. CI is configured for 3.11/3.12; remote
CI execution is not assumed from a local pass.

Synthetic benchmark: 7 scenarios × 10 seeds × 8 seconds = 70 episodes. There are
50 fault episodes and 20 healthy episodes. All 50 expected causes were detected;
there were zero unexpected-cause episodes. Episode precision and recall are 1.0,
healthy false-alarm rate 0, maximum detection latency 1.05 s. Latency uses simulation
time at the scan boundary, not wall-clock API latency. The baseline comparator is
only the original physical-condition subset (conveyor jam, pump conflict, safety),
without fault-code/latch labels. Its healthy episode false-alarm rate is 0.5 because
conveyor startup combines inrush with a photoeye pulse. This is a narrow comparison,
not an evaluation of every original diagnostic rule.

Seeds vary current noise only. These designed scenarios were used to develop the
rules: this is regression coverage, not a held-out machine-learning test set.
The benchmark uses 50 ms observations. Default 1 s live polling can miss pre-trip
transients; use 50 ms only in this local simulator, and establish a safe polling
budget with a controls engineer for real hardware. No trained AI accuracy, causal
identification, downtime savings, PLC equivalence or hard real-time guarantee is
claimed.

## When hardware is necessary

| Deployment need | Hardware decision | Verification before selection |
|---|---|---|
| Local demo, rule development and regression | No PLC or ESP32 needed | Run simulator and tests on a laptop |
| Reachable PLC tags + existing OT server/industrial PC | Install acquisition software on that host; custom device not automatically needed | Protocol support, permission, CPU/network budget and uptime |
| Isolated OT network, offline inference, buffering during WAN loss | A local edge host is needed; an existing approved gateway may suffice | Segmented network design, disk retention, clock sync, restart and outage testing |
| Legacy serial/proprietary link or electrical signal missing from PLC | Protocol gateway or isolated acquisition hardware may be needed | Supported protocol, isolation, sample rate and installation approval |
| Fast transients shorter than safe polling interval | PLC event buffer or suitable local acquisition may be needed | Measure aliasing and end-to-end timestamps; an ESP32 alone does not prove adequate capture |
| Rugged unattended production operation | Evaluate supported industrial hardware | Power/environment rating, watchdog, storage endurance, secure update and maintainability |

The ESP32 currently supplies a second observer of the same registers over Wi-Fi.
It cannot discover uninstrumented field causes and does not make the diagnostics
more accurate simply by existing. Keep it as an optional inexpensive demonstrator.
Do not commit to the Canvas hardware BOM or price until connectivity, installation,
certification/support and maintenance costs are measured. The architectural
recommendation is a deployable software collector with optional hardware profiles.

For production, design read-only acquisition, network zones, least privilege and
controlled access around OT performance requirements; see
[NIST SP 800-82 Rev. 3](https://csrc.nist.gov/pubs/sp/800/82/r3/final).
Simulator network API and addressing are based on the pinned library's
[PyModbus 3.8.3 server documentation](https://pymodbus.readthedocs.io/en/v3.8.3/source/server.html).
These references inform the proposed design, not a compliance claim.

## Proposed phases and exit gates (not implemented)

1. **Reproducible controller lab.** Export a versioned CODESYS project, task period,
   I/O mappings and runtime version. Compare scan-by-scan traces with the Python
   model for start/stop, trip and reset; explicitly document intended differences.
   Compile ST and ESP32 firmware; connect the ESP32 to the lab server and verify
   every register/scaling. Add delayed/dropped/duplicate responses, socket-level
   outages, clock drift, combined faults and task overruns. Exit: repeatable traces,
   all discrepancy decisions reviewed, no unauthorized write path.
2. **Production-shaped acquisition.** Typed tag contracts with asset identity,
   quality and source timestamps; bounded persistent event store, reconnect backoff,
   offline queue, authenticated UI/API and deployment packaging. Read-only Modbus
   first; OPC UA, EtherNet/IP and vendor adapters only with explicit test fixtures.
   Exit: 24-hour lab soak, agreed latency/PLC-load budget, outage recovery without
   mislabeled stale samples, backup/restore and access tests.
3. **AI assistance with evidence.** Collect technician-confirmed incident labels
   and resolution outcomes. Split by asset and time to prevent leakage. Compare
   temporal rules, statistical anomaly detection and learned classifiers on held-out
   incidents; add abstention and calibration. Retrieve approved manuals by asset and
   revision; an optional LLM can explain cited evidence but cannot issue commands.
   Test hallucination, unsupported claims and malicious retrieved text. Exit:
   pre-agreed recall/false-alarms-per-machine-hour and unknown-fault coverage,
   confidence intervals and technician usefulness measured on held-out data.
4. **Read-only pilot.** Operator timeline/dashboard, evidence bundle, ranked checks,
   technician confirmation and audit trail. Track time to first useful diagnosis,
   MTTR, alert burden and recurrence by incident class; record downtime baseline and
   operational changes that could confound results. Exit: representative supervised
   pilot demonstrates value with no interference with PLC control. Evaluate the
   Canvas 20–40% reduction target only here; report uncertainty and failures.
5. **Deployment/product decision.** Select supported edge hardware only where the
   measured connectivity/availability requirements justify it. Add signed updates,
   fleet inventory, rollback, retention policies and support procedures. Validate
   unit economics and customers' willingness to pay. Multi-site and fleet analytics
   follow explicit customer and security review.

No phase authorizes autonomous plant actuation. Control and safety stay in the
existing PLC/safety system; Resolvyn advises a qualified human.
