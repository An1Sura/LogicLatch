// Run with PLAYWRIGHT_MODULE pointing to an installed Playwright package.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({
    channel: process.env.BROWSER_CHANNEL || "chrome",
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const base = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";
  const out = process.env.SCREENSHOT_DIR || "../../outputs";
  await page.goto(base + "/dashboard/");
  await page.waitForFunction(() =>
    document.getElementById("source").textContent.includes("Demo"),
  );
  await page.selectOption("#scenario", "conveyor_jam");
  await page.click("#load");
  await page.waitForFunction(
    () => document.getElementById("fault").textContent === "601",
  );
  await page.screenshot({
    path: path.join(out, "topology-desktop.png"),
    fullPage: true,
  });
  await page.click('[data-device="esp"]');
  assert.match(
    await page.locator("#detail-description").innerText(),
    /no ESP32 telemetry endpoint/,
  );
  await page.click('[role="tab"][data-view="telemetry"]');
  assert.equal(await page.locator("#registers tr").count(), 15);
  await page.click('[role="tab"][data-view="diagnostics"]');
  assert.match(
    await page.locator("#findings").innerText(),
    /Conveyor jam or overload/,
  );
  await page.screenshot({
    path: path.join(out, "diagnostics-desktop.png"),
    fullPage: true,
  });
  await page.selectOption("#scenario", "idle");
  await page.click("#load");
  await page.waitForFunction(
    () => document.getElementById("fault").textContent === "000",
  );
  await page.click('[role="tab"][data-view="topology"]');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(out, "topology-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    "No page-wide mobile overflow",
  );
  await page.setViewportSize({ width: 1440, height: 1100 });
  const disconnected = await (
    await page.request.get(base + "/api/state")
  ).json();
  disconnected.connected = false;
  disconnected.source = "modbus";
  await page.route("**/api/state", (route) =>
    route.fulfill({ json: disconnected }),
  );
  await page.click("#refresh");
  await page.waitForFunction(
    () => document.getElementById("plc").textContent === "Unavailable",
  );
  assert.equal(await page.locator("#current").innerText(), "— A");
  assert.ok(await page.locator("#load").isDisabled());
  await page.unroute("**/api/state");
  await page.route("**/api/**", (route) => route.abort());
  await page.click("#refresh");
  await page.waitForFunction(() =>
    document.getElementById("connection").textContent.includes("unavailable"),
  );
  assert.equal(await page.locator("#current").innerText(), "— A");
  await page.screenshot({
    path: path.join(out, "bridge-unavailable.png"),
    fullPage: true,
  });
  await page.unroute("**/api/**");
  await page.click("#refresh");
  await page.waitForFunction(() =>
    document.getElementById("source").textContent.includes("Demo"),
  );
  assert.deepEqual(errors, []);
  console.log(
    "PASS: scenario switch, node details, 15 registers, diagnostics, mobile overflow, disconnected PLC, API outage and recovery; no browser errors.",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
