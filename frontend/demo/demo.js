import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { current, usable, validState } from "../model.mjs";

const $ = (id) => document.getElementById(id);
let state = null,
  busy = false,
  polling = false,
  exploded = false,
  sceneReady = false,
  revision = 0,
  actionError = null;
const view = { update() {}, focus() {}, explode() {} };
const info = {
  device: [
    "LOGICLATCH / ACTUAL CAD",
    "Base and lid are loaded from the repository’s STL files. ESP32 independently polls the PLC over WiFi; it does not supply telemetry to the Python bridge. Mounting and assembly are illustrative.",
  ],
  plc: [
    "CODESYS PLC / MODBUS TCP",
    "The PLC publishes 15 holding registers. FastAPI reads those registers and runs the diagnostic rules shown in the software. The rack is illustrative.",
  ],
  conveyor: [
    "CONVEYOR / MOTOR + PHOTOEYE",
    "A running command, blocked sensor and motor current of at least 8.0 A trigger the bridge’s jam rule. The 3D box position is illustrative, not measured telemetry.",
  ],
  laptop: [
    "LOGICLATCH / SOFTWARE",
    "The operator dashboard reads the same FastAPI backend as this 3D view. Open connected software to inspect all registers and the exact diagnostic findings.",
  ],
};
function selectPart(key) {
  $("part-label").textContent = info[key][0];
  $("part-info").textContent = info[key][1];
}
async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function renderState() {
  const ok = usable(state),
    mock = ok && state.source === "mock",
    r = state?.registers || {},
    jam = ok && r.Conveyor_Running && r.Sensor_Blocked && r.Motor_Current >= 80;
  $("source-badge").textContent = !state
    ? "API UNAVAILABLE"
    : mock
      ? "DEMO / BACKEND MOCK"
      : ok
        ? "PLC SOURCE / READ ONLY"
        : "PLC DISCONNECTED";
  $("connection-message").textContent =
    actionError ||
    (!state
      ? "Bridge unavailable. Values are hidden; reconnecting automatically."
      : !ok
        ? "PLC disconnected. Retained readings are hidden."
        : mock
          ? "Connected to the mock bridge. Controls update the same data as the dashboard."
          : "Physical PLC source. Scenario controls are disabled.");
  for (const id of ["run", "jam", "clear"]) $(id).disabled = !mock || busy;
  $("state").textContent = ok ? state.machine_state : "Unavailable";
  $("amps").textContent =
    current(state) === null ? "—" : `${current(state).toFixed(1)} A`;
  $("code").textContent = ok
    ? String(state.active_fault_code).padStart(3, "0")
    : "—";
  $("sensor").textContent = ok ? (r.Sensor_Blocked ? "Blocked" : "Clear") : "—";
  $("safety").textContent = ok
    ? r.Safety_OK
      ? "Healthy"
      : "Open / fault"
    : "—";
  $("latch").textContent = ok
    ? r.System_Fault_Latch
      ? "Latched"
      : "Clear"
    : "—";
  $("diagnosis-title").textContent = !ok
    ? "Telemetry unavailable"
    : jam
      ? "Conveyor jam or overload"
      : r.System_Fault_Latch
        ? state.active_fault_name
        : r.Conveyor_Running
          ? "Conveyor running normally"
          : "Healthy idle";
  $("diagnosis-text").textContent = !ok
    ? "The 3D motion is paused. No fallback readings are invented."
    : jam
      ? "The motor is commanded on, but the photoeye stays blocked and current is high. These signals satisfy the bridge’s jam rule. The scene illustrates a box caught against a guide."
      : r.System_Fault_Latch
        ? "Another backend scenario is active. Open the dashboard to inspect its diagnostic findings."
        : r.Conveyor_Running
          ? "The conveyor is commanded on, the photoeye is clear and the fault latch is reset. Introduce a jam to see the diagnosis change."
          : "No active fault. Run the conveyor to begin the demonstration.";
  $("evidence").textContent = jam
    ? "RUNNING = 1 · BLOCKED = 1\n95 ÷ 10 = 9.5 A · FAULT = 601"
    : ok
      ? `SAFETY_OK = ${r.Safety_OK} · FAULT = ${r.Fault_Code}`
      : "";
  document
    .querySelector(".diagnosis")
    .classList.toggle("fault", !!(ok && r.System_Fault_Latch));
  $("run").classList.toggle(
    "active",
    !!(ok && r.Conveyor_Running && !jam && !r.System_Fault_Latch),
  );
  $("jam").classList.toggle("active", !!jam);
  $("clear").classList.toggle(
    "active",
    !!(ok && !r.Conveyor_Running && !r.System_Fault_Latch),
  );
  view.update(state);
}
async function poll() {
  if (polling || busy) return;
  polling = true;
  const requestRevision = revision;
  try {
    const next = await request("/api/state");
    if (!validState(next)) throw new Error("Invalid state");
    if (requestRevision === revision) {
      state = next;
      actionError = null;
    }
  } catch {
    if (requestRevision === revision) state = null;
  } finally {
    polling = false;
    renderState();
  }
}
async function setScenario(scenario) {
  if (busy || !usable(state) || state.source !== "mock") return;
  busy = true;
  revision++;
  actionError = null;
  renderState();
  try {
    const next = await request("/api/mock/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario }),
    });
    if (!validState(next)) throw new Error("Invalid scenario response");
    state = next;
    renderState();
  } catch (e) {
    actionError = `Could not load scenario (${e.message}). Retrying state reads automatically.`;
    state = null;
  } finally {
    busy = false;
    renderState();
  }
}
$("run").onclick = () => setScenario("conveyor_running");
$("jam").onclick = () => setScenario("conveyor_jam");
$("clear").onclick = () => setScenario("idle");
$("explode").onclick = () => {
  exploded = !exploded;
  $("explode").setAttribute("aria-pressed", String(exploded));
  $("explode").textContent = exploded ? "Close enclosure" : "Explode enclosure";
  view.explode(exploded);
  selectPart("device");
  view.focus("device");
};
for (const button of document.querySelectorAll("[data-camera]"))
  button.onclick = () => {
    view.focus(button.dataset.camera);
    selectPart(
      button.dataset.camera === "overview" ? "device" : button.dataset.camera,
    );
    document
      .querySelectorAll("[data-camera]")
      .forEach((b) => b.classList.toggle("selected", b === button));
  };
$("software").onclick = () => {
  const panel = $("software-panel");
  panel.hidden = false;
  if (!$("dashboard-frame").getAttribute("src"))
    $("dashboard-frame").src = "../";
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
};
$("close-software").onclick = () => {
  $("software-panel").hidden = true;
  $("software").focus();
};

function buildScene() {
  const host = $("stage"),
    scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x11191c, 0.025);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.3;
  host.append(renderer.domElement);
  renderer.domElement.setAttribute(
    "aria-label",
    "3D model of LogicLatch enclosure, PLC rack, conveyor and laptop",
  );
  renderer.domElement.setAttribute("role", "img");
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
  camera.position.set(10, 8.5, 12);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 1, 0);
  controls.minDistance = 3;
  controls.maxDistance = 23;
  controls.maxPolarAngle = Math.PI * 0.48;
  controls.minPolarAngle = 0.2;
  const ambient = new THREE.HemisphereLight(0xdbf4ff, 0x263520, 2.3);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffeed4, 4.5);
  key.position.set(-4, 10, 6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, {
    left: -10,
    right: 10,
    top: 10,
    bottom: -10,
  });
  key.shadow.bias = -0.001;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xa8caff, 2);
  rim.position.set(5, 6, -6);
  scene.add(rim);
  const mat = (color, metalness = 0.1, roughness = 0.6) =>
    new THREE.MeshStandardMaterial({ color, metalness, roughness });
  const steel = mat(0x929fa3, 0.78, 0.3),
    dark = mat(0x1c252b, 0.4, 0.45),
    black = mat(0x151a1c, 0.1, 0.8),
    lime = mat(0xc3ed75),
    cardboard = mat(0xa7804f, 0, 0.92),
    white = mat(0xc6ceca, 0.08, 0.6),
    green = mat(0x65e1a1),
    red = mat(0xff5d47);
  const picks = [];
  function box(w, h, d, m, x, y, z, parent = scene) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }
  function cylinder(radius, length, m, x, y, z, parent = scene) {
    const mesh = new THREE.Mesh(
      new THREE.CylinderGeometry(radius, radius, length, 24),
      m,
    );
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    parent.add(mesh);
    return mesh;
  }
  function pick(mesh, id) {
    mesh.userData.part = id;
    picks.push(mesh);
    return mesh;
  }
  function cable(points, color = 0x267eb0, radius = 0.025) {
    const curve = new THREE.CatmullRomCurve3(
      points.map((p) => new THREE.Vector3(...p)),
    );
    const mesh = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 28, radius, 8, false),
      mat(color, 0.1, 0.7),
    );
    scene.add(mesh);
    return mesh;
  }
  function label(text, x, y, z, width = 1.8, color = "#c4f281") {
    const c = document.createElement("canvas");
    c.width = 768;
    c.height = 128;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#142025";
    ctx.fillRect(0, 0, 768, 128);
    ctx.strokeStyle = "#47605b";
    ctx.lineWidth = 4;
    ctx.strokeRect(2, 2, 764, 124);
    ctx.fillStyle = color;
    ctx.font = "500 39px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, 384, 78);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: tex, depthTest: false }),
    );
    sprite.position.set(x, y, z);
    sprite.scale.set(width, width / 6, 1);
    scene.add(sprite);
    return sprite;
  }
  const floor = box(18, 0.12, 12, mat(0x202c30, 0.1, 0.8), 0, -0.08, 0);
  floor.receiveShadow = true;
  const grid = new THREE.GridHelper(18, 36, 0x394948, 0x2c3b3d);
  grid.position.y = -0.005;
  scene.add(grid);
  // Conveyor: frame, rollers, drive and guard rails.
  const conveyor = new THREE.Group();
  scene.add(conveyor);
  pick(box(6.7, 0.28, 1.65, dark, 0, 1.25, 0.5, conveyor), "conveyor");
  box(6.9, 0.18, 0.14, steel, 0, 1.32, -0.38, conveyor);
  box(6.9, 0.18, 0.14, steel, 0, 1.32, 1.38, conveyor);
  for (const x of [-2.7, 2.7])
    for (const z of [-0.25, 1.25]) {
      box(0.15, 1.2, 0.15, steel, x, 0.6, z, conveyor);
      box(0.45, 0.07, 0.4, dark, x, 0.03, z, conveyor);
    }
  const rollers = [];
  for (let x = -3.15; x < 3.3; x += 0.28) {
    const roller = cylinder(0.105, 1.57, steel, x, 1.45, 0.5, conveyor);
    roller.rotation.x = Math.PI / 2;
    rollers.push(roller);
  }
  for (const z of [-0.45, 1.45]) {
    box(6.8, 0.07, 0.07, steel, 0, 1.85, z, conveyor);
    for (const x of [-2.8, 0, 2.8])
      box(0.06, 0.5, 0.06, steel, x, 1.62, z, conveyor);
  }
  const motor = pick(
    box(0.8, 0.5, 0.65, dark, 2.45, 1.03, 1.9, conveyor),
    "conveyor",
  );
  for (let x = 2.12; x < 2.8; x += 0.1)
    box(0.035, 0.58, 0.72, steel, x, 1.03, 1.9, conveyor);
  const wheel = cylinder(0.25, 0.14, dark, 2.45, 1.06, 2.3, conveyor);
  wheel.rotation.x = Math.PI / 2;
  const carton = pick(
    box(0.85, 0.85, 0.78, cardboard, -2.4, 1.98, 0.5, conveyor),
    "conveyor",
  );
  box(0.12, 0.008, 0.79, mat(0xc2a474), 0, 0.43, 0, carton);
  box(0.12, 0.84, 0.009, mat(0xc2a474), 0, 0, 0.395, carton);
  // Photoeye and the conceptual stop causing the jam.
  const sensor = pick(
    box(0.18, 0.25, 0.25, dark, 0.72, 1.78, 1.6, conveyor),
    "conveyor",
  );
  const sensorLens = box(0.09, 0.09, 0.025, green, 0.72, 1.78, 1.46, conveyor);
  box(0.08, 0.45, 0.08, steel, 0.72, 1.48, 1.6, conveyor);
  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.012, 0.012, 1.8, 8),
    new THREE.MeshBasicMaterial({
      color: 0x7af0a3,
      transparent: true,
      opacity: 0.5,
    }),
  );
  beam.rotation.x = Math.PI / 2;
  beam.position.set(0.72, 1.78, 0.5);
  conveyor.add(beam);
  const obstruction = box(0.16, 0.28, 1.35, red, 1.28, 1.65, 0.5, conveyor);
  obstruction.visible = false;
  // Backplane and generic PLC modules.
  const rack = pick(box(3.85, 2.7, 0.16, steel, -2.2, 2.3, -2.1), "plc");
  for (const x of [-3.9, -0.5]) {
    box(0.12, 2, 0.12, dark, x, 1, -2.2);
  }
  for (const y of [1.55, 2.4, 3.25]) box(3.6, 0.09, 0.1, steel, -2.2, y, -1.97);
  const leds = [];
  for (let i = 0; i < 5; i++) {
    const x = -2.5 + i * 0.43;
    pick(box(0.36, 1.03, 0.35, dark, x, 2.45, -1.8), "plc");
    box(0.28, 0.34, 0.018, black, x, 2.54, -1.61);
    for (let j = 0; j < 5; j++) {
      const led = box(
        0.035,
        0.045,
        0.02,
        green,
        x - 0.09,
        2.83 - j * 0.09,
        -1.59,
      );
      leds.push(led);
    }
    for (let j = 0; j < 4; j++)
      box(0.04, 0.05, 0.04, steel, x - 0.1 + j * 0.06, 1.99, -1.6);
    cable(
      [
        [x, 1.94, -1.65],
        [x, 1.48, -1.62],
        [x + 0.25, 1.3, -1.9],
        [1.7, 1.1, -1.5],
      ],
      i % 2 ? 0x20659b : 0x23292a,
      0.018,
    );
  }
  const device = new THREE.Group();
  device.position.set(-3.55, 2.42, -1.63);
  device.rotation.y = Math.PI;
  scene.add(device);
  // Keep actual STL vertices; rotate CAD Z-up into Three.js Y-up.
  const loader = new STLLoader();
  let lid = null,
    base = null,
    lidRest = null;
  const loadPart = async (file) => {
    const geom = await loader.loadAsync(`models/${file}`);
    geom.computeVertexNormals();
    geom.center();
    const mesh = new THREE.Mesh(geom, white);
    mesh.scale.setScalar(0.013);
    mesh.rotation.x = -Math.PI / 2;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    pick(mesh, "device");
    device.add(mesh);
    return mesh;
  };
  Promise.all([loadPart("ESP32ModuleBase.stl"), loadPart("ESP32ModuleTop.stl")])
    .then(([b, l]) => {
      base = b;
      lid = l;
      lid.rotation.set(0, 0, Math.PI / 2);
      lid.position.set(0, 0, -0.38);
      lidRest = lid.position.clone();
      $("cad-badge").textContent = "2 / 2 ACTUAL STL PARTS";
      host.dataset.cad = "loaded";
    })
    .catch(() => {
      $("cad-badge").textContent = "CAD FAILED TO LOAD";
      $("part-info").textContent =
        "Could not load the enclosure STL files. Reload the page to retry.";
    });
  const pcb = box(0.34, 0.56, 0.035, mat(0x246744), 0, 0, 0, device);
  box(0.22, 0.25, 0.035, steel, 0, 0.05, 0.035, pcb);
  for (let i = 0; i < 9; i++)
    for (const x of [-0.14, 0.14])
      box(0.025, 0.025, 0.035, steel, x, -0.22 + i * 0.05, 0.025, pcb);
  pcb.visible = false;
  cable(
    [
      [-3.5, 1.97, -1.62],
      [-3.5, 1.55, -1.5],
      [-3, 1.24, -1.8],
      [-2.6, 1.1, -1.7],
    ],
    0x242d32,
    0.027,
  );
  label("LogicLatch / ESP32", -3.65, 3.45, -1.45, 2.2);
  label("CODESYS / PLC", -1.4, 3.75, -2, 2.1);
  label("PHOTOEYE", 0.72, 2.65, 1.5, 1.35);
  // Laptop with a canvas-rendered screen driven by the same API values.
  const laptop = new THREE.Group();
  laptop.position.set(3.2, 1.3, -1.6);
  laptop.rotation.y = -0.24;
  scene.add(laptop);
  box(2.7, 0.12, 1.8, steel, 0, -0.1, 0, laptop);
  for (let row = 0; row < 4; row++)
    for (let col = 0; col < 10; col++)
      box(
        0.17,
        0.012,
        0.13,
        dark,
        -0.97 + col * 0.215,
        -0.028,
        -0.3 + row * 0.18,
        laptop,
      );
  box(0.65, 0.012, 0.35, dark, 0, -0.027, 0.64, laptop);
  const screenGroup = new THREE.Group();
  screenGroup.position.set(0, 0, -0.82);
  screenGroup.rotation.x = -0.12;
  laptop.add(screenGroup);
  pick(box(2.7, 1.72, 0.09, dark, 0, 0.86, 0, screenGroup), "laptop");
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 640;
  const ctx = canvas.getContext("2d"),
    texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(2.51, 1.52),
    new THREE.MeshBasicMaterial({ map: texture }),
  );
  screen.position.set(0, 0.88, 0.051);
  screenGroup.add(screen);
  pick(screen, "laptop");
  box(2.8, 1.1, 0.08, dark, 3.2, 0.57, -2.35);
  box(0.15, 1.1, 1.7, steel, 2.05, 0.55, -1.6);
  box(0.15, 1.1, 1.7, steel, 4.35, 0.55, -1.6);
  label("DIAGNOSTICS / FASTAPI", 3.3, 3.5, -2, 2.5);
  function drawScreen(s) {
    const ok = usable(s),
      r = s?.registers || {},
      fault = ok && r.System_Fault_Latch;
    ctx.fillStyle = "#0c1215";
    ctx.fillRect(0, 0, 1024, 640);
    ctx.fillStyle = "#e4efea";
    ctx.font = "bold 38px sans-serif";
    ctx.fillText("LogicLatch", 38, 60);
    ctx.fillStyle = "#cafa77";
    ctx.font = "20px monospace";
    ctx.fillText(
      s?.source === "mock"
        ? "DEMO / SIMULATED CELL"
        : ok
          ? "PLC SOURCE / READ ONLY"
          : "DATA UNAVAILABLE",
      38,
      105,
    );
    ctx.fillStyle = fault ? "#ff806c" : "#dce9df";
    ctx.font = "30px sans-serif";
    ctx.fillText(ok ? s.machine_state : "Telemetry unavailable", 38, 168);
    ctx.font = "bold 46px monospace";
    ctx.fillText(
      ok ? `${current(s).toFixed(1)} A    ${s.active_fault_code}` : "— A    —",
      38,
      235,
    );
    ctx.strokeStyle = "#566e51";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(300, 310);
    ctx.lineTo(300, 395);
    ctx.lineTo(140, 395);
    ctx.lineTo(140, 465);
    ctx.moveTo(300, 395);
    ctx.lineTo(460, 395);
    ctx.lineTo(460, 465);
    ctx.stroke();
    for (const [x, y, text, color] of [
      [210, 280, "PLC", fault ? "#ff806c" : "#cafa77"],
      [40, 465, "CONVEYOR", fault ? "#ff806c" : "#cafa77"],
      [370, 465, "SAFETY", ok && !r.Safety_OK ? "#ff806c" : "#cafa77"],
    ]) {
      ctx.fillStyle = "#1c292d";
      ctx.fillRect(x, y, 190, 65);
      ctx.strokeStyle = ok ? color : "#73838b";
      ctx.strokeRect(x, y, 190, 65);
      ctx.fillStyle = ok ? color : "#73838b";
      ctx.font = "20px monospace";
      ctx.fillText(text, x + 18, y + 41);
    }
    ctx.fillStyle = "#9cadaa";
    ctx.font = "20px sans-serif";
    ctx.fillText("DIAGNOSTIC EVIDENCE", 620, 310);
    const lines = !ok
      ? ["Awaiting bridge"]
      : fault
        ? [
            "Sensor blocked",
            "High motor current",
            "Fault latch active",
            "Sequence timeout",
          ]
        : ["Photoeye clear", "Safety chain healthy", "Fault latch clear"];
    lines.forEach((t, i) => {
      ctx.fillStyle = fault ? "#e7b6a6" : "#c0dbb7";
      ctx.fillText(t, 620, 360 + i * 50);
    });
    ctx.fillStyle = "#7d9590";
    ctx.font = "17px monospace";
    ctx.fillText("ESP32: unobserved / Serial only", 38, 597);
    texture.needsUpdate = true;
  }
  let running = false,
    jammed = false,
    available = false,
    cameraGoal = null,
    targetGoal = null,
    explodeGoal = 0,
    wasJammed = false;
  view.update = (s) => {
    available = usable(s);
    const r = s?.registers || {};
    running = available && !!r.Conveyor_Running;
    jammed = running && !!r.Sensor_Blocked && r.Motor_Current >= 80;
    obstruction.visible = jammed;
    if (jammed && !wasJammed) carton.position.x = 0.72;
    if (!jammed && wasJammed) carton.position.x = -2.4;
    wasJammed = jammed;
    carton.rotation.y = jammed ? -0.28 : 0;
    beam.material.color.setHex(
      !available ? 0x708086 : jammed ? 0xff6553 : 0x77df9d,
    );
    beam.visible = available;
    sensorLens.material = jammed ? red : available ? green : dark;
    for (const led of leds) led.material = !available ? dark : green;
    if (available && r.System_Fault_Latch) leds[0].material = red;
    drawScreen(s);
  };
  const views = {
    overview: [
      [10, 8.5, 12],
      [0, 1, 0],
    ],
    device: [
      [-6.3, 4.4, 3.5],
      [-2.8, 2.3, -1.7],
    ],
    conveyor: [
      [5.7, 4.7, 7],
      [0.6, 1.2, 0.6],
    ],
  };
  view.focus = (name) => {
    const v = views[name] || views.overview;
    cameraGoal = new THREE.Vector3(...v[0]);
    targetGoal = new THREE.Vector3(...v[1]);
  };
  view.explode = (value) => {
    explodeGoal = value ? 1 : 0;
  };
  controls.addEventListener("start", () => {
    cameraGoal = null;
    targetGoal = null;
  });
  const raycaster = new THREE.Raycaster(),
    pointer = new THREE.Vector2();
  let down = null;
  renderer.domElement.addEventListener(
    "pointerdown",
    (e) => (down = [e.clientX, e.clientY]),
  );
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5)
      return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      (-(e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(picks, false)[0];
    if (hit) selectPart(hit.object.userData.part);
  });
  const resize = () => {
    const w = host.clientWidth,
      h = host.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.zoom = Math.min(1, Math.max(0.4, camera.aspect / 1.15));
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(host);
  resize();
  renderer.domElement.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    $("loading").hidden = false;
    $("loading").textContent =
      "3D graphics paused. Reload to restore the scene. The telemetry controls remain available.";
  });
  let last = performance.now();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    if (document.hidden) return;
    if (running && !jammed && !reduced) {
      carton.position.x += dt * 0.9;
      if (carton.position.x > 3) carton.position.x = -3;
      wheel.rotation.y += dt * 3;
      rollers.forEach((r) => (r.rotation.y += dt * 3));
    }
    if (cameraGoal) {
      camera.position.lerp(cameraGoal, 0.075);
      controls.target.lerp(targetGoal, 0.075);
      if (camera.position.distanceTo(cameraGoal) < 0.02) {
        cameraGoal = null;
        targetGoal = null;
      }
    }
    if (lid && lidRest) {
      lid.position.z = THREE.MathUtils.lerp(
        lid.position.z,
        lidRest.z - explodeGoal * 0.9,
        0.09,
      );
      lid.position.y = THREE.MathUtils.lerp(
        lid.position.y,
        lidRest.y + explodeGoal * 0.35,
        0.09,
      );
      pcb.visible = explodeGoal > 0;
    }
    controls.update();
    renderer.render(scene, camera);
  }
  requestAnimationFrame(animate);
  sceneReady = true;
  host.dataset.ready = "true";
  $("loading").hidden = true;
  drawScreen(state);
  view.update(state);
}
try {
  buildScene();
} catch (error) {
  $("loading").textContent =
    "3D could not start. This demo requires WebGL 2. The scenario controls and connected dashboard remain usable.";
  $("cad-badge").textContent = "3D UNAVAILABLE";
  console.error(error);
}
renderState();
poll();
setInterval(poll, 1500);
