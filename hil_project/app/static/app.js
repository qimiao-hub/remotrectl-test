const state = {
  connected: false,
  baseUrl: "",
  ws: null,
  signals: [],
  signalMap: new Map(),
  values: {},
  owners: {},
  gamepadIndex: null,
  gamepadBindings: {},
  gamepadAxisBindings: {},
  disabledDefaultAxisBindings: {},
  axisCalibration: {},
  currentRawAxes: [],
  bindingTarget: null,
  lastButtonValues: {},
  lastAxisValues: {},
  lastAxisWriteValues: {},
};

const groupOrder = ["档位", "按钮/拨杆", "灯光", "雨刮"];
const numericNames = ["Steering", "Throttle", "Brake"];
const axisThreshold = 0.55;
const axisReleaseThreshold = 0.35;
const ignoredGamepadButtons = new Set([15, 16]);
const threePositionButtons = {
  left: 12,
  middle: 13,
  right: 14,
  physicalRight: 13,
};
const axisLabels = {
  0: "模拟输入 A0",
  1: "模拟输入 A1",
  2: "模拟输入 A2",
  3: "模拟输入 A3",
};
const defaultGamepadAxisBindings = {
  "axis:2:analog": { signal: "Steering", mode: "analog", direction: "analog" },
  "axis:3:negativeAnalog": { signal: "Throttle", mode: "analog-direction", direction: "negative" },
  "axis:3:positiveAnalog": { signal: "Brake", mode: "analog-direction", direction: "positive" },
};

const els = {
  hostInput: document.querySelector("#hostInput"),
  portInput: document.querySelector("#portInput"),
  connectButton: document.querySelector("#connectButton"),
  disconnectButton: document.querySelector("#disconnectButton"),
  connectionStatus: document.querySelector("#connectionStatus"),
  summaryOnline: document.querySelector("#summaryOnline"),
  summaryCount: document.querySelector("#summaryCount"),
  summaryFault: document.querySelector("#summaryFault"),
  summaryUpdated: document.querySelector("#summaryUpdated"),
  signalGroups: document.querySelector("#signalGroups"),
  faultSignals: document.querySelector("#faultSignals"),
  numericControls: document.querySelector("#numericControls"),
  frameHex: document.querySelector("#frameHex"),
  eventLog: document.querySelector("#eventLog"),
  refreshButton: document.querySelector("#refreshButton"),
  clearLogButton: document.querySelector("#clearLogButton"),
  copyFrameButton: document.querySelector("#copyFrameButton"),
  scanGamepadButton: document.querySelector("#scanGamepadButton"),
  gamepadName: document.querySelector("#gamepadName"),
  gamepadBindingStatus: document.querySelector("#gamepadBindingStatus"),
  gamepadButtons: document.querySelector("#gamepadButtons"),
  gamepadAxes: document.querySelector("#gamepadAxes"),
  bindingSignalSelect: document.querySelector("#bindingSignalSelect"),
  bindButton: document.querySelector("#bindButton"),
  clearBindingsButton: document.querySelector("#clearBindingsButton"),
  calibrateCenterButton: document.querySelector("#calibrateCenterButton"),
  calibrateNegativeButton: document.querySelector("#calibrateNegativeButton"),
  calibratePositiveButton: document.querySelector("#calibratePositiveButton"),
  clearCalibrationButton: document.querySelector("#clearCalibrationButton"),
  bindingList: document.querySelector("#bindingList"),
};

function defaultHost() {
  const host = window.location.hostname || "127.0.0.1";
  return host === "0.0.0.0" ? "127.0.0.1" : host;
}

function defaultPort() {
  return window.location.port || "8000";
}

function buildBaseUrl() {
  const host = els.hostInput.value.trim() || defaultHost();
  const port = els.portInput.value.trim() || defaultPort();
  return `${window.location.protocol}//${host}:${port}`;
}

function buildWsUrl() {
  const url = new URL(state.baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/v1/state";
  url.search = "";
  return url.toString();
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${state.baseUrl}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json();
}

async function connect() {
  state.baseUrl = buildBaseUrl();
  setConnectionUi("connecting");
  logEvent("连接", `正在连接 ${state.baseUrl}`);

  try {
    await requestJson("/health");
    const signalPayload = await requestJson("/api/v1/signals");
    const statePayload = await requestJson("/api/v1/state");
    state.signals = signalPayload.signals;
    state.signalMap = new Map(state.signals.map((signal) => [signal.name, signal]));
    applyStatePayload(statePayload);
    setConnectionUi("online");
    renderAll();
    openWebSocket();
    logEvent("连接", "服务连接成功");
  } catch (error) {
    setConnectionUi("offline");
    logEvent("错误", error.message);
  }
}

function disconnect() {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
  state.connected = false;
  setConnectionUi("offline");
  logEvent("连接", "已断开");
}

function openWebSocket() {
  if (state.ws) {
    state.ws.close();
  }
  const ws = new WebSocket(buildWsUrl());
  state.ws = ws;

  ws.addEventListener("open", () => {
    logEvent("WebSocket", "实时通道已建立");
  });

  ws.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "state.snapshot") {
      applyStatePayload(payload.payload);
    }
    if (payload.type === "state.updated" || payload.type === "state.replaced") {
      applyStatePayload({ signals: payload.payload.state || payload.payload.signals, owners: state.owners });
      logEvent("状态更新", summarizeSignals(payload.payload.signals || {}));
    }
    renderDynamicState();
  });

  ws.addEventListener("close", () => {
    if (state.connected) {
      setConnectionUi("offline");
      logEvent("WebSocket", "实时通道已关闭");
    }
  });

  ws.addEventListener("error", () => {
    logEvent("WebSocket", "实时通道异常");
  });
}

function applyStatePayload(payload) {
  state.values = { ...state.values, ...(payload.signals || {}) };
  state.owners = { ...state.owners, ...(payload.owners || {}) };
}

function renderAll() {
  renderSignalGroups();
  renderNumericControls();
  renderFaultSignals();
  renderBindingOptions();
  renderBindings();
  renderDynamicState();
}

function renderSignalGroups() {
  const grouped = groupOrder.map((group) => ({
    group,
    signals: state.signals.filter((signal) => signal.group === group && signal.is_bit),
  }));
  const flatSignals = grouped.flatMap((entry) => entry.signals);
  els.signalGroups.innerHTML = `
        <section class="signal-flat-container">
          <div class="flag-grid flat-flag-grid">
            ${flatSignals.map(renderFlagButton).join("")}
          </div>
        </section>
      `;

  els.signalGroups.querySelectorAll("[data-signal]").forEach((button) => {
    button.addEventListener("click", () => toggleBitSignal(button.dataset.signal));
  });
}

function renderFlagButton(signal) {
  return `
    <button class="flag-button" type="button" data-signal="${escapeAttr(signal.name)}">
      <span class="lamp" aria-hidden="true"></span>
      <span>
        <span class="flag-name">${escapeHtml(signal.name)}</span>
        <span class="flag-desc">${escapeHtml(signal.description)}</span>
      </span>
    </button>
  `;
}

function renderNumericControls() {
  const controls = numericNames
    .map((name) => state.signalMap.get(name))
    .filter(Boolean)
    .map(
      (signal) => `
        <div class="numeric-card" data-numeric="${escapeAttr(signal.name)}">
          <div class="numeric-head">
            <strong>${escapeHtml(signal.description)}</strong>
            <span>${escapeHtml(signal.name)}</span>
          </div>
          <div class="numeric-row">
            <input type="range" min="${signal.minimum}" max="${signal.maximum}" step="1" data-range="${escapeAttr(signal.name)}" />
            <input class="numeric-value" type="number" min="${signal.minimum}" max="${signal.maximum}" step="1" data-number="${escapeAttr(signal.name)}" />
          </div>
        </div>
      `,
    )
    .join("");
  els.numericControls.innerHTML = controls;

  els.numericControls.querySelectorAll("[data-range]").forEach((input) => {
    input.addEventListener("change", () => writeSignal(input.dataset.range, Number(input.value)));
    input.addEventListener("input", () => syncNumericDisplay(input.dataset.range, Number(input.value)));
  });
  els.numericControls.querySelectorAll("[data-number]").forEach((input) => {
    input.addEventListener("change", () => writeSignal(input.dataset.number, Number(input.value)));
  });
}

function renderFaultSignals() {
  const faults = state.signals.filter((signal) => signal.group === "故障状态");
  els.faultSignals.innerHTML = faults
    .map(
      (signal) => `
        <button class="fault-item" type="button" data-fault="${escapeAttr(signal.name)}">
          <span class="lamp" aria-hidden="true"></span>
          <span>
            <span class="fault-name">${escapeHtml(signal.description)}</span>
            <span class="fault-desc">${escapeHtml(signal.name)}</span>
          </span>
        </button>
      `,
    )
    .join("");

  els.faultSignals.querySelectorAll("[data-fault]").forEach((button) => {
    button.addEventListener("click", () => toggleBitSignal(button.dataset.fault));
  });
}

function renderBindingOptions() {
  const bindableSignals = state.signals.filter((signal) => signal.group !== "故障状态");
  els.bindingSignalSelect.innerHTML = bindableSignals
    .map((signal) => `<option value="${escapeAttr(signal.name)}">${escapeHtml(signal.name)} - ${escapeHtml(signal.description)}</option>`)
    .join("");
}

function renderBindings() {
  const buttonEntries = Object.entries(state.gamepadBindings).map(
    ([buttonIndex, signalName]) => `B${buttonIndex} -> ${escapeHtml(signalName)}`,
  );
  const axisEntries = Object.entries(state.gamepadAxisBindings).map(
    ([key, binding]) => `${formatAxisBindingName(key)} -> ${escapeHtml(binding.signal)}`,
  );
  const entries = [...buttonEntries, ...axisEntries];
  els.bindingList.innerHTML = entries.length
    ? entries.map((entry) => `<span class="binding-chip">${entry}</span>`).join("")
    : '<span class="binding-chip">暂无绑定</span>';
  els.gamepadBindingStatus.textContent = entries.length ? `${entries.length} 个绑定` : "未绑定";
}

function renderDynamicState() {
  updateFlags();
  updateNumerics();
  updateFaults();
  updateSummary();
  updateFrame();
}

function updateFlags() {
  document.querySelectorAll("[data-signal]").forEach((button) => {
    const value = Number(state.values[button.dataset.signal] || 0);
    button.classList.toggle("on", value === 1);
    button.setAttribute("aria-pressed", value === 1 ? "true" : "false");
  });
}

function updateNumerics() {
  numericNames.forEach((name) => syncNumericDisplay(name, Number(state.values[name] || 0)));
}

function syncNumericDisplay(name, value) {
  const range = document.querySelector(`[data-range="${cssEscape(name)}"]`);
  const number = document.querySelector(`[data-number="${cssEscape(name)}"]`);
  if (range && document.activeElement !== range) {
    range.value = String(value);
  }
  if (number && document.activeElement !== number) {
    number.value = String(value);
  }
}

function updateFaults() {
  document.querySelectorAll("[data-fault]").forEach((item) => {
    const value = Number(state.values[item.dataset.fault] || 0);
    item.classList.toggle("fault", value === 0);
    item.classList.toggle("ok", value === 1);
    item.setAttribute("aria-pressed", value === 0 ? "true" : "false");
    item.title = value === 0 ? "当前有故障，点击切换为无故障" : "当前无故障，点击切换为有故障";
  });
}

function updateSummary() {
  const faults = state.signals.filter((signal) => signal.group === "故障状态");
  const activeFaults = faults.filter((signal) => Number(state.values[signal.name] || 0) === 0).length;
  els.summaryOnline.textContent = state.connected ? "在线" : "离线";
  els.summaryCount.textContent = String(state.signals.length || "--");
  els.summaryFault.textContent = faults.length ? `${activeFaults}/${faults.length}` : "--";
  els.summaryUpdated.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

async function updateFrame() {
  if (!state.connected) {
    els.frameHex.textContent = "--";
    return;
  }
  try {
    const payload = await requestJson("/api/v1/state/frame");
    els.frameHex.textContent = formatHex(payload.hex);
  } catch (error) {
    els.frameHex.textContent = "读取失败";
  }
}

async function toggleBitSignal(name) {
  const current = Number(state.values[name] || 0);
  await writeSignal(name, current ? 0 : 1);
}

async function writeSignal(name, value) {
  if (!state.connected) {
    logEvent("写入失败", "服务未连接");
    return;
  }
  const signal = state.signalMap.get(name);
  const nextValue = clamp(Number(value), signal.minimum, signal.maximum);
  try {
    const payload = await requestJson("/api/v1/state", {
      method: "PATCH",
      body: JSON.stringify({ source: "api", signals: { [name]: nextValue } }),
    });
    applyStatePayload(payload);
    renderDynamicState();
    logEvent("写入", `${name} = ${nextValue}`);
  } catch (error) {
    logEvent("写入失败", error.message);
    renderDynamicState();
  }
}

function setConnectionUi(mode) {
  state.connected = mode === "online";
  els.connectButton.disabled = mode === "connecting" || mode === "online";
  els.disconnectButton.disabled = mode !== "online";
  els.connectionStatus.classList.toggle("online", mode === "online");
  els.connectionStatus.classList.toggle("offline", mode !== "online");
  document.querySelector("#connectionStatusText").textContent =
    mode === "online" ? "已连接" : mode === "connecting" ? "连接中" : "未连接";
  els.summaryOnline.textContent = mode === "online" ? "在线" : "离线";
}

function loadGamepadBindings() {
  try {
    state.gamepadBindings = JSON.parse(localStorage.getItem("hilGamepadBindings") || "{}");
  } catch {
    state.gamepadBindings = {};
  }
  let savedAxisBindings = {};
  try {
    savedAxisBindings = JSON.parse(localStorage.getItem("hilGamepadAxisBindings") || "{}");
  } catch {
    savedAxisBindings = {};
  }
  try {
    state.disabledDefaultAxisBindings = JSON.parse(localStorage.getItem("hilGamepadDisabledDefaultAxisBindings") || "{}");
  } catch {
    state.disabledDefaultAxisBindings = {};
  }
  state.gamepadAxisBindings = { ...defaultGamepadAxisBindings, ...savedAxisBindings };
  Object.keys(state.disabledDefaultAxisBindings).forEach((key) => {
    if (defaultGamepadAxisBindings[key] && !savedAxisBindings[key]) {
      delete state.gamepadAxisBindings[key];
    }
  });
  try {
    state.axisCalibration = JSON.parse(localStorage.getItem("hilGamepadAxisCalibration") || "{}");
  } catch {
    state.axisCalibration = {};
  }
}

function saveGamepadBindings() {
  localStorage.setItem("hilGamepadBindings", JSON.stringify(state.gamepadBindings));
  localStorage.setItem("hilGamepadAxisBindings", JSON.stringify(state.gamepadAxisBindings));
  localStorage.setItem("hilGamepadDisabledDefaultAxisBindings", JSON.stringify(state.disabledDefaultAxisBindings));
  renderBindings();
}

function saveAxisCalibration() {
  localStorage.setItem("hilGamepadAxisCalibration", JSON.stringify(state.axisCalibration));
}

function scanGamepads() {
  const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
  if (!pads.length) {
    els.gamepadName.textContent = "未检测到手柄";
    logEvent("手柄", "未检测到手柄，请按下手柄任意按键后重试");
    return;
  }
  const pad = pads[0];
  state.gamepadIndex = pad.index;
  els.gamepadName.textContent = pad.id;
  logEvent("手柄", `已检测到 ${pad.id}`);
}

function pollGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const pad = state.gamepadIndex === null ? Array.from(pads).find(Boolean) : pads[state.gamepadIndex];
  if (pad) {
    state.gamepadIndex = pad.index;
    els.gamepadName.textContent = pad.id;
    renderGamepadState(pad);
    applyGamepadBindings(pad);
  }
  requestAnimationFrame(pollGamepad);
}

function renderGamepadState(pad) {
  els.gamepadButtons.innerHTML = getEffectiveGamepadButtons(pad)
    .map(
      (button) =>
        `<span class="pad-button ${button.pressed ? "pressed" : ""} ${button.virtual ? "virtual" : ""}">B${button.index}</span>`,
    )
    .join("");
  state.currentRawAxes = Array.from(pad.axes, Number);
  els.gamepadAxes.innerHTML = pad.axes
    .map((value, index) => {
      const rawValue = Number(value);
      const numericValue = normalizeAxisValue(index, rawValue);
      const label = axisLabels[index] || `模拟输入 A${index}`;
      const fillStart = numericValue < 0 ? 50 + numericValue * 50 : 50;
      const fillWidth = Math.abs(numericValue) * 50;
      return `
        <div class="axis-item">
          <span>${escapeHtml(label)}</span>
          <span class="axis-stack">
            <span class="axis-meter">
              <span class="axis-center" aria-hidden="true"></span>
              <span class="axis-fill" style="left: ${fillStart}%; width: ${fillWidth}%"></span>
            </span>
            <span class="axis-scale" aria-hidden="true">
              <span>-1</span>
              <span>0</span>
              <span>1</span>
            </span>
          </span>
          <span>${numericValue.toFixed(2)}</span>
        </div>
      `;
    })
    .join("");
}

function applyGamepadBindings(pad) {
  const buttons = getEffectiveGamepadButtons(pad);
  const currentBySignal = {};
  const previousBySignal = {};

  buttons.forEach((button) => {
    const index = button.index;
    const previous = Boolean(state.lastButtonValues[index]);
    const current = Boolean(button.pressed);

    if (state.bindingTarget && current && !previous) {
      state.gamepadBindings[index] = state.bindingTarget;
      state.bindingTarget = null;
      saveGamepadBindings();
      logEvent("手柄绑定", `B${index} -> ${state.gamepadBindings[index]}`);
    }

    const signalName = buttonBinding(index);
    if (signalName) {
      currentBySignal[signalName] = Boolean(currentBySignal[signalName]) || current;
      previousBySignal[signalName] = Boolean(previousBySignal[signalName]) || previous;
    }
    state.lastButtonValues[index] = current;
  });

  Object.entries(currentBySignal).forEach(([signalName, current]) => {
    const previous = Boolean(previousBySignal[signalName]);
    if (current !== previous) {
      writeSignal(signalName, current ? 1 : 0);
    }
  });

  pad.axes.forEach((rawValue, index) => {
    const value = normalizeAxisValue(index, Number(rawValue));
    const previous = Number(state.lastAxisValues[index] || 0);
    const direction = value > axisThreshold ? "positive" : value < -axisThreshold ? "negative" : null;
    const previousDirection =
      previous > axisReleaseThreshold ? "positive" : previous < -axisReleaseThreshold ? "negative" : null;

    if (state.bindingTarget && direction && direction !== previousDirection) {
      bindAxisAction(index, direction);
    }

    applyAxisAnalogBinding(index, value);
    applyAxisDirectionBinding(index, direction);
    reportUnmappedAxisAction(index, direction, previousDirection);
    state.lastAxisValues[index] = value;
  });
}

function getEffectiveGamepadButtons(pad) {
  const leftPressed = Boolean(pad.buttons[threePositionButtons.left]?.pressed);
  const rightPressed = Boolean(pad.buttons[threePositionButtons.physicalRight]?.pressed);
  const maxButtonCount = Math.max(pad.buttons.length, threePositionButtons.right + 1);
  const buttons = [];
  for (let index = 0; index < maxButtonCount; index += 1) {
    if (ignoredGamepadButtons.has(index)) {
      continue;
    }
    if (index === threePositionButtons.right) {
      continue;
    }
    if (index === threePositionButtons.physicalRight) {
      buttons.push({
        index: threePositionButtons.middle,
        pressed: !leftPressed && !rightPressed,
        virtual: true,
      });
      buttons.push({
        index: threePositionButtons.right,
        pressed: rightPressed,
        virtual: false,
      });
      continue;
    }
    buttons.push({
      index,
      pressed: Boolean(pad.buttons[index]?.pressed),
      virtual: false,
    });
  }
  return buttons;
}

function buttonBinding(index) {
  return state.gamepadBindings[index];
}

function bindAxisAction(index, direction) {
  const signal = state.signalMap.get(state.bindingTarget);
  if (!signal) {
    return;
  }
  const isSignedAnalog = !signal.is_bit && signal.minimum < 0 && signal.maximum > 0;
  const key = signal.is_bit ? axisKey(index, direction) : isSignedAnalog ? axisKey(index, "analog") : axisKey(index, `${direction}Analog`);
  state.gamepadAxisBindings[key] = {
    signal: state.bindingTarget,
    mode: signal.is_bit ? "direction" : isSignedAnalog ? "analog" : "analog-direction",
    direction: signal.is_bit || !isSignedAnalog ? direction : "analog",
  };
  delete state.disabledDefaultAxisBindings[key];
  state.bindingTarget = null;
  saveGamepadBindings();
  logEvent("手柄绑定", `${formatAxisBindingName(key)} -> ${state.gamepadAxisBindings[key].signal}`);
}

function applyAxisAnalogBinding(index, value) {
  const fullKey = axisKey(index, "analog");
  const fullBinding = state.gamepadAxisBindings[fullKey];
  if (fullBinding) {
    const signal = state.signalMap.get(fullBinding.signal);
    if (signal && !signal.is_bit) {
      const normalized = (value + 1) / 2;
      const mappedValue = mapAxisMagnitudeToSignal(signal, normalized);
      writeAxisSignal(fullKey, fullBinding, mappedValue);
    }
  }

  [
    { key: axisKey(index, "negativeAnalog"), magnitude: value < 0 ? Math.abs(value) : 0 },
    { key: axisKey(index, "positiveAnalog"), magnitude: value > 0 ? value : 0 },
  ].forEach(({ key, magnitude }) => {
    const binding = state.gamepadAxisBindings[key];
    const signal = binding ? state.signalMap.get(binding.signal) : null;
    if (!signal || signal.is_bit) {
      return;
    }
    const mappedValue = mapAxisMagnitudeToSignal(signal, magnitude);
    writeAxisSignal(key, binding, mappedValue);
  });
}

function applyAxisDirectionBinding(index, direction) {
  ["positive", "negative"].forEach((candidate) => {
    const key = axisKey(index, candidate);
    const binding = state.gamepadAxisBindings[axisKey(index, candidate)];
    if (!binding) {
      return;
    }
    const signal = state.signalMap.get(binding.signal);
    if (!signal || !signal.is_bit) {
      return;
    }
    const value = direction === candidate ? 1 : 0;
    if (state.lastAxisWriteValues[key] === value) {
      return;
    }
    state.lastAxisWriteValues[key] = value;
    writeSignal(binding.signal, value);
  });
}

function reportUnmappedAxisAction(index, direction, previousDirection) {
  if (!direction || direction === previousDirection) {
    return;
  }
  const key = axisKey(index, direction);
  const analogKey = axisKey(index, "analog");
  const directionalAnalogKey = axisKey(index, `${direction}Analog`);
  if (state.gamepadAxisBindings[key] || state.gamepadAxisBindings[analogKey] || state.gamepadAxisBindings[directionalAnalogKey]) {
    return;
  }
  logEvent("未识别动作", `${formatAxisBindingName(key)}，可选择信号后绑定`);
}

function mapAxisMagnitudeToSignal(signal, magnitude) {
  return clamp(signal.minimum + clampFloat(magnitude, 0, 1) * (signal.maximum - signal.minimum), signal.minimum, signal.maximum);
}

function writeAxisSignal(key, binding, value) {
  if (state.lastAxisWriteValues[key] === value) {
    return;
  }
  state.lastAxisWriteValues[key] = value;
  writeSignal(binding.signal, value);
}

function normalizeAxisValue(index, rawValue) {
  const calibration = state.axisCalibration[index];
  if (!calibration || calibration.center === undefined) {
    return clampFloat(rawValue, -1, 1);
  }

  const center = Number(calibration.center);
  if (rawValue === center) {
    return 0;
  }

  if (rawValue < center) {
    const negative = calibration.negative === undefined ? -1 : Number(calibration.negative);
    const span = center - negative;
    if (Math.abs(span) < 0.0001) {
      return 0;
    }
    return clampFloat((rawValue - center) / span, -1, 0);
  }

  const positive = calibration.positive === undefined ? 1 : Number(calibration.positive);
  const span = positive - center;
  if (Math.abs(span) < 0.0001) {
    return 0;
  }
  return clampFloat((rawValue - center) / span, 0, 1);
}

function captureAxisCalibration(point) {
  if (!state.currentRawAxes.length) {
    logEvent("手柄校准", "未检测到模拟轴，请先扫描或移动手柄");
    return;
  }
  state.currentRawAxes.forEach((rawValue, index) => {
    state.axisCalibration[index] = {
      ...(state.axisCalibration[index] || {}),
      [point]: Number(rawValue),
    };
  });
  saveAxisCalibration();
  state.lastAxisValues = {};
  logEvent("手柄校准", `已记录${calibrationPointLabel(point)}`);
}

function calibrationPointLabel(point) {
  if (point === "center") {
    return "中位";
  }
  if (point === "negative") {
    return "负向极限";
  }
  return "正向极限";
}

function logEvent(type, message) {
  const item = document.createElement("li");
  const time = document.createElement("time");
  const text = document.createElement("span");
  time.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  text.textContent = `${type}：${message || ""}`;
  item.append(time, text);
  els.eventLog.prepend(item);
  while (els.eventLog.children.length > 80) {
    els.eventLog.lastElementChild.remove();
  }
}

function summarizeSignals(signals) {
  const entries = Object.entries(signals);
  if (!entries.length) {
    return "无变更";
  }
  return entries
    .slice(0, 4)
    .map(([name, value]) => `${name}=${value}`)
    .join("，");
}

function formatHex(hex) {
  return (hex || "").match(/.{1,2}/g)?.join(" ") || "--";
}

function axisKey(index, direction) {
  return `axis:${index}:${direction}`;
}

function formatAxisBindingName(key) {
  const [, index, direction] = key.split(":");
  const label = axisLabels[index] || `模拟输入 A${index}`;
  if (direction === "analog") {
    return `${label} 模拟量`;
  }
  if (direction === "negativeAnalog") {
    return `${label} - 模拟量`;
  }
  if (direction === "positiveAnalog") {
    return `${label} + 模拟量`;
  }
  return `${label} ${direction === "positive" ? "+" : "-"}`;
}

function clamp(value, minimum, maximum) {
  if (Number.isNaN(value)) {
    return minimum;
  }
  return Math.min(maximum, Math.max(minimum, Math.round(value)));
}

function clampFloat(value, minimum, maximum) {
  if (Number.isNaN(value)) {
    return minimum;
  }
  return Math.min(maximum, Math.max(minimum, value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS && window.CSS.escape) {
    return window.CSS.escape(value);
  }
  return String(value).replaceAll('"', '\\"');
}

els.hostInput.value = defaultHost();
els.portInput.value = defaultPort();
els.connectButton.addEventListener("click", connect);
els.disconnectButton.addEventListener("click", disconnect);
els.refreshButton.addEventListener("click", async () => {
  if (!state.connected) {
    logEvent("刷新失败", "服务未连接");
    return;
  }
  const payload = await requestJson("/api/v1/state");
  applyStatePayload(payload);
  renderDynamicState();
  logEvent("刷新", "状态已更新");
});
els.clearLogButton.addEventListener("click", () => {
  els.eventLog.innerHTML = "";
});
els.copyFrameButton.addEventListener("click", async () => {
  const text = els.frameHex.textContent || "";
  await navigator.clipboard.writeText(text.replaceAll(" ", ""));
  logEvent("TCP 帧", "已复制");
});
els.scanGamepadButton.addEventListener("click", scanGamepads);
els.bindButton.addEventListener("click", () => {
  const signalName = els.bindingSignalSelect.value;
  if (!signalName) {
    logEvent("手柄绑定", "请先选择信号");
    return;
  }
  state.bindingTarget = signalName;
  els.gamepadBindingStatus.textContent = `等待输入 -> ${signalName}`;
  logEvent("手柄绑定", `请按下按钮或推动摇杆来绑定 ${signalName}`);
});
els.clearBindingsButton.addEventListener("click", () => {
  const signalName = els.bindingSignalSelect.value;
  if (!signalName) {
    logEvent("手柄绑定", "请先选择要清空的信号");
    return;
  }
  let removedCount = 0;
  Object.entries(state.gamepadBindings).forEach(([buttonIndex, boundSignal]) => {
    if (boundSignal === signalName) {
      delete state.gamepadBindings[buttonIndex];
      removedCount += 1;
    }
  });
  Object.entries(state.gamepadAxisBindings).forEach(([key, binding]) => {
    if (binding.signal === signalName) {
      delete state.gamepadAxisBindings[key];
      if (defaultGamepadAxisBindings[key]?.signal === signalName) {
        state.disabledDefaultAxisBindings[key] = true;
      }
      removedCount += 1;
    }
  });
  if (state.bindingTarget === signalName) {
    state.bindingTarget = null;
  }
  state.lastAxisWriteValues = {};
  saveGamepadBindings();
  logEvent("手柄绑定", removedCount ? `已清空 ${signalName} 的 ${removedCount} 个绑定` : `${signalName} 没有可清空绑定`);
});
els.calibrateCenterButton.addEventListener("click", () => {
  captureAxisCalibration("center");
});
els.calibrateNegativeButton.addEventListener("click", () => {
  captureAxisCalibration("negative");
});
els.calibratePositiveButton.addEventListener("click", () => {
  captureAxisCalibration("positive");
});
els.clearCalibrationButton.addEventListener("click", () => {
  state.axisCalibration = {};
  state.lastAxisValues = {};
  state.lastAxisWriteValues = {};
  saveAxisCalibration();
  logEvent("手柄校准", "已清空校准");
});
window.addEventListener("gamepadconnected", (event) => {
  state.gamepadIndex = event.gamepad.index;
  els.gamepadName.textContent = event.gamepad.id;
  logEvent("手柄", `已连接 ${event.gamepad.id}`);
});
window.addEventListener("gamepaddisconnected", () => {
  state.gamepadIndex = null;
  els.gamepadName.textContent = "未检测到手柄";
  logEvent("手柄", "手柄已断开");
});

loadGamepadBindings();
renderBindings();
pollGamepad();
logEvent("系统", "界面已就绪");
if (new URLSearchParams(window.location.search).get("autoconnect") === "1") {
  connect();
}
