const appState = {
  controls: {
    throttle: 0,
    brake: 0,
    steering: 0,
    gear: "drive",
    parking_brake: false,
    ignition: "run",
  },
  responseConfig: {},
  mapPayload: null,
  mapSettingsDirty: false,
  brush: {
    mode: "raise",
    radius_m: 35,
    strength_m: 2,
  },
  editMode: false,
  editAction: "brush",
  selectedObjectId: null,
  objectTypes: {
    obstacle: ["cone", "barrier", "pedestrian"],
    v2v: ["car", "truck", "bus"],
  },
  latest: null,
  paused: false,
  olMap: null,
  mapLayer: null,
  markerHeading: null,
};

const els = {};
let responseTimer = null;
let responseConfigDirty = false;
let responseConfigRevision = 0;
let gpsDirty = false;
let brushTimer = null;

document.addEventListener("DOMContentLoaded", async () => {
  bindElements();
  bindControls();
  updateControlLabels();
  populateObjectSubtypes();
  applyEditMode();
  await loadElevationMap();
  await pollState();
  setInterval(pollState, 120);
});

function bindElements() {
  [
    "throttle",
    "brake",
    "steering",
    "throttleValue",
    "brakeValue",
    "steeringValue",
    "parkingBrake",
    "ignition",
    "workspace",
    "controlsPanel",
    "telemetryPanel",
    "toggleLeftPanel",
    "toggleRightPanel",
    "latitudeInput",
    "longitudeInput",
    "headingInput",
    "applyGps",
    "pauseButton",
    "mapView",
    "mapLabel",
    "editMapToggle",
    "editAction",
    "gridOverlay",
    "objectOverlay",
    "vehicleMarker",
    "clientCount",
    "pausedState",
    "speed",
    "rpm",
    "pitch",
    "heading",
    "grade",
    "elevation",
    "localPosition",
    "lat",
    "lon",
    "brushMode",
    "brushRadius",
    "brushStrength",
    "brushRadiusValue",
    "brushStrengthValue",
    "resetMap",
    "mapWidth",
    "mapHeight",
    "mapResolution",
    "baseElevation",
    "centerLatitude",
    "centerLongitude",
    "applyMapSettings",
    "exportMap",
    "importMapButton",
    "importMapFile",
    "objectCategory",
    "objectSubtype",
    "objectTypeMenu",
    "objectLatitude",
    "objectLongitude",
    "objectHeading",
    "updateObject",
    "deleteObject",
    "tcpMessages",
    "engineForce",
    "brakeForce",
    "rollingBase",
    "rollingSpeed",
    "aeroDrag",
    "maxRpm",
    "engineForceValue",
    "brakeForceValue",
    "rollingBaseValue",
    "rollingSpeedValue",
    "aeroDragValue",
    "maxRpmValue",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });
  els.gearButtons = document.getElementById("gearButtons");
}

function bindControls() {
  ["throttle", "brake", "steering"].forEach((name) => {
    els[name].addEventListener("input", () => {
      appState.controls[name] = Number(els[name].value);
      updateControlLabels();
      sendControls();
    });
  });

  els.gearButtons.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-gear]");
    if (!button) return;
    appState.controls.gear = button.dataset.gear;
    [...els.gearButtons.querySelectorAll("button")].forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    sendControls();
  });

  els.parkingBrake.addEventListener("change", () => {
    appState.controls.parking_brake = els.parkingBrake.checked;
    sendControls();
  });

  els.ignition.addEventListener("change", () => {
    appState.controls.ignition = els.ignition.checked ? "run" : "off";
    sendControls();
  });

  els.toggleLeftPanel.addEventListener("click", () => togglePanel("left"));
  els.toggleRightPanel.addEventListener("click", () => togglePanel("right"));

  els.editMapToggle.addEventListener("click", () => {
    appState.editMode = !appState.editMode;
    applyEditMode();
  });

  els.editAction.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    appState.editAction = button.dataset.action;
    [...els.editAction.querySelectorAll("button")].forEach((item) => {
      item.classList.toggle("active", item === button);
    });
  });

  [els.latitudeInput, els.longitudeInput, els.headingInput].forEach((input) => {
    input.addEventListener("input", () => {
      gpsDirty = true;
    });
  });

  els.applyGps.addEventListener("click", async () => {
    const state = await postJSON("/api/control", {
      ...appState.controls,
      latitude: Number(els.latitudeInput.value),
      longitude: Number(els.longitudeInput.value),
      heading: Number(els.headingInput.value),
      reset_position: true,
    });
    gpsDirty = false;
    renderState(state);
  });

  els.pauseButton.addEventListener("click", async () => {
    appState.paused = !appState.paused;
    const state = await postJSON("/api/pause", { paused: appState.paused });
    renderState(state);
  });

  els.brushMode.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    appState.brush.mode = button.dataset.mode;
    [...els.brushMode.querySelectorAll("button")].forEach((item) => {
      item.classList.toggle("active", item === button);
    });
  });

  els.brushRadius.addEventListener("input", () => {
    appState.brush.radius_m = Number(els.brushRadius.value);
    updateBrushLabels();
  });

  els.brushStrength.addEventListener("input", () => {
    appState.brush.strength_m = Number(els.brushStrength.value);
    updateBrushLabels();
  });

  els.resetMap.addEventListener("click", async (event) => {
    event.stopPropagation();
    event.preventDefault();
    appState.mapPayload = await postJSON("/api/map/reset", {});
    syncObjectTypes(appState.mapPayload);
    appState.selectedObjectId = null;
    clearObjectInputs();
    refreshMapLayer();
    syncMapSettingsInputs(appState.mapPayload);
    renderMapObjects();
  });

  ["mapWidth", "mapHeight", "mapResolution", "baseElevation", "centerLatitude", "centerLongitude"].forEach((id) => {
    els[id].addEventListener("input", () => {
      appState.mapSettingsDirty = true;
    });
  });

  els.applyMapSettings.addEventListener("click", applyMapSettings);

  els.exportMap.addEventListener("click", exportMapJson);
  els.importMapButton.addEventListener("click", () => els.importMapFile.click());
  els.importMapFile.addEventListener("change", importMapJson);

  els.objectCategory.addEventListener("change", () => {
    populateObjectSubtypes();
    syncObjectTypeMenu();
  });
  els.objectSubtype.addEventListener("change", syncObjectTypeMenu);
  els.objectTypeMenu.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category][data-subtype]");
    if (!button) return;
    setObjectType(button.dataset.category, button.dataset.subtype);
  });
  els.updateObject.addEventListener("click", updateSelectedObject);
  els.deleteObject.addEventListener("click", deleteSelectedObject);

  [
    "engineForce",
    "brakeForce",
    "rollingBase",
    "rollingSpeed",
    "aeroDrag",
    "maxRpm",
  ].forEach((id) => {
    els[id].addEventListener("input", () => {
      collectResponseConfig();
      responseConfigDirty = true;
      responseConfigRevision += 1;
      updateResponseLabels();
      window.clearTimeout(responseTimer);
      responseTimer = window.setTimeout(() => sendResponseConfig(responseConfigRevision), 160);
    });
  });
}

function togglePanel(side) {
  const left = side === "left";
  const panel = left ? els.controlsPanel : els.telemetryPanel;
  const button = left ? els.toggleLeftPanel : els.toggleRightPanel;
  const className = left ? "left-collapsed" : "right-collapsed";
  const collapsed = !panel.classList.contains("collapsed");
  panel.classList.toggle("collapsed", collapsed);
  els.workspace.classList.toggle(className, collapsed);
  button.textContent = left ? (collapsed ? "›" : "‹") : (collapsed ? "‹" : "›");
  window.setTimeout(() => {
    appState.olMap?.updateSize();
    if (appState.latest) {
      centerMapOnVehicle(appState.latest.vehicle);
    }
  }, 40);
}

function applyEditMode() {
  const editor = document.querySelector(".elevation-editor");
  editor?.classList.toggle("editing", appState.editMode);
  els.editMapToggle.classList.toggle("active", appState.editMode);
  els.editMapToggle.textContent = appState.editMode ? "完成编辑" : "编辑地图";
  els.mapView.classList.toggle("editing", appState.editMode);
}

function populateObjectSubtypes(selectedSubtype) {
  const category = els.objectCategory.value;
  const subtypes = appState.objectTypes[category] || [];
  const labels = {
    cone: "锥桶",
    barrier: "路障",
    pedestrian: "行人",
    car: "轿车",
    truck: "货车",
    bus: "公交",
  };
  els.objectSubtype.innerHTML = "";
  subtypes.forEach((subtype) => {
    const option = document.createElement("option");
    option.value = subtype;
    option.textContent = labels[subtype] || subtype;
    els.objectSubtype.appendChild(option);
  });
  if (selectedSubtype && subtypes.includes(selectedSubtype)) {
    els.objectSubtype.value = selectedSubtype;
  }
  syncObjectTypeMenu();
}

function syncObjectTypes(payload) {
  if (payload?.object_types) {
    appState.objectTypes = payload.object_types;
  }
  populateObjectSubtypes();
}

function setObjectType(category, subtype) {
  els.objectCategory.value = category;
  populateObjectSubtypes(subtype);
  els.objectSubtype.value = subtype;
  syncObjectTypeMenu();
}

function syncObjectTypeMenu() {
  if (!els.objectTypeMenu) return;
  const category = els.objectCategory.value;
  const subtype = els.objectSubtype.value;
  els.objectTypeMenu.querySelectorAll(".type-group").forEach((group) => {
    group.open = group.dataset.category === category;
  });
  els.objectTypeMenu.querySelectorAll("button[data-category][data-subtype]").forEach((button) => {
    button.classList.toggle(
      "active",
      button.dataset.category === category && button.dataset.subtype === subtype,
    );
  });
}

function updateControlLabels() {
  els.throttleValue.textContent = `${appState.controls.throttle.toFixed(0)}%`;
  els.brakeValue.textContent = `${appState.controls.brake.toFixed(0)}%`;
  els.steeringValue.textContent = `${appState.controls.steering.toFixed(0)}°`;
}

function updateBrushLabels() {
  els.brushRadiusValue.textContent = `${appState.brush.radius_m.toFixed(0)} m`;
  els.brushStrengthValue.textContent = `${appState.brush.strength_m.toFixed(1)} m`;
}

function updateResponseLabels() {
  els.engineForceValue.textContent = formatNumber(appState.responseConfig.engine_force_base, 1);
  els.brakeForceValue.textContent = formatNumber(appState.responseConfig.brake_force, 1);
  els.rollingBaseValue.textContent = formatNumber(appState.responseConfig.rolling_drag_base, 2);
  els.rollingSpeedValue.textContent = formatNumber(appState.responseConfig.rolling_drag_speed, 3);
  els.aeroDragValue.textContent = formatNumber(appState.responseConfig.aero_drag, 4);
  els.maxRpmValue.textContent = formatNumber(appState.responseConfig.max_rpm, 0);
}

async function sendControls(extra = {}) {
  const state = await postJSON("/api/control", { ...appState.controls, ...extra });
  renderState(state);
}

async function sendResponseConfig(revision) {
  const payload = { ...appState.responseConfig };
  const saved = await postJSON("/api/response-config", payload);
  if (revision === responseConfigRevision) {
    appState.responseConfig = saved;
    responseConfigDirty = false;
    updateResponseInputs();
  }
}

async function pollState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    renderState(await response.json());
  } catch (error) {
    els.pausedState.textContent = "后端未连接";
  }
}

async function postJSON(url, payload) {
  return requestJSON(url, payload, "POST");
}

async function requestJSON(url, payload, method = "POST") {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json();
}

async function loadElevationMap() {
  const response = await fetch("/api/map", { cache: "no-store" });
  appState.mapPayload = await response.json();
  syncObjectTypes(appState.mapPayload);
  createMap();
  refreshMapLayer();
  syncMapSettingsInputs(appState.mapPayload);
  renderMapObjects();
}

function createMap() {
  if (!window.ol) {
    els.mapLabel.textContent = "OpenLayers 未加载";
    return;
  }

  const projection = new ol.proj.Projection({
    code: "SIM-METERS",
    units: "m",
    extent: mapExtent(appState.mapPayload),
  });

  appState.mapLayer = new ol.layer.Image({
    source: createElevationSource(projection),
  });
  appState.mapLayer.setZIndex(0);

  appState.olMap = new ol.Map({
    target: "mapView",
    layers: [appState.mapLayer],
    view: new ol.View({
      projection,
      center: [0, 0],
      zoom: 4,
      minZoom: 1,
      maxZoom: 8,
    }),
  });

  appState.olMap.on("click", (event) => handleMapClick(event.coordinate));
  appState.olMap.on("pointerdrag", (event) => {
    if (!appState.editMode || appState.editAction !== "brush") return;
    window.clearTimeout(brushTimer);
    brushTimer = window.setTimeout(() => applyBrush(event.coordinate), 35);
  });
  appState.olMap.on("moveend", renderMapObjects);
}

function refreshMapLayer() {
  if (!appState.olMap || !appState.mapLayer || !appState.mapPayload) return;
  const projection = appState.olMap.getView().getProjection();
  appState.mapLayer.setSource(createElevationSource(projection));
  appState.olMap.getView().fit(mapExtent(appState.mapPayload), {
    size: appState.olMap.getSize(),
    nearest: true,
    duration: 0,
  });
  if (appState.latest) {
    centerMapOnVehicle(appState.latest.vehicle);
    updateGridOverlay(appState.latest.vehicle);
  }
  renderMapObjects();
}

function createElevationSource(projection) {
  return new ol.source.ImageStatic({
    url: elevationImageUrl(appState.mapPayload),
    imageExtent: mapExtent(appState.mapPayload),
    projection,
  });
}

function mapExtent(payload) {
  const maxX = payload.origin_x_m + payload.width * payload.resolution_m;
  const maxY = payload.origin_y_m + payload.height * payload.resolution_m;
  return [payload.origin_x_m, payload.origin_y_m, maxX, maxY];
}

function gridInterval(maxDimensionM) {
  if (maxDimensionM <= 250) return 25;
  if (maxDimensionM <= 800) return 50;
  if (maxDimensionM <= 1800) return 100;
  return 200;
}

function elevationImageUrl(payload) {
  const canvas = document.createElement("canvas");
  canvas.width = payload.width;
  canvas.height = payload.height;
  const ctx = canvas.getContext("2d");
  const image = ctx.createImageData(payload.width, payload.height);
  const min = payload.min_elevation_m;
  const max = payload.max_elevation_m;
  const range = Math.max(max - min, 1);

  for (let row = 0; row < payload.height; row += 1) {
    const sourceRow = payload.height - 1 - row;
    for (let col = 0; col < payload.width; col += 1) {
      const value = payload.elevation[sourceRow][col];
      const color = elevationColor((value - min) / range);
      const index = (row * payload.width + col) * 4;
      image.data[index] = color[0];
      image.data[index + 1] = color[1];
      image.data[index + 2] = color[2];
      image.data[index + 3] = 255;
    }
  }

  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

function elevationColor(t) {
  const stops = [
    [34, 92, 138],
    [69, 152, 137],
    [142, 184, 96],
    [222, 191, 100],
    [175, 113, 83],
    [235, 237, 232],
  ];
  const scaled = clamp(t, 0, 1) * (stops.length - 1);
  const index = Math.floor(scaled);
  const next = Math.min(index + 1, stops.length - 1);
  const mix = scaled - index;
  return [
    Math.round(lerp(stops[index][0], stops[next][0], mix)),
    Math.round(lerp(stops[index][1], stops[next][1], mix)),
    Math.round(lerp(stops[index][2], stops[next][2], mix)),
  ];
}

async function handleMapClick(coordinate) {
  if (!appState.editMode) return;
  if (appState.editAction === "object") {
    await addMapObject(coordinate);
    return;
  }
  await applyBrush(coordinate);
}

async function applyBrush(coordinate) {
  const payload = {
    x_m: coordinate[0],
    y_m: coordinate[1],
    mode: appState.brush.mode,
    radius_m: appState.brush.radius_m,
    strength_m: appState.brush.strength_m,
  };
  const result = await postJSON("/api/elevation/brush", payload);
  appState.mapPayload = result.map;
  refreshMapLayer();
  syncMapSettingsInputs(appState.mapPayload);
}

async function addMapObject(coordinate) {
  const response = await postJSON("/api/map/objects", {
    category: els.objectCategory.value,
    subtype: els.objectSubtype.value,
    heading: Number(els.objectHeading.value || 0),
    x_m: coordinate[0],
    y_m: coordinate[1],
  });
  appState.mapPayload = response.map;
  appState.selectedObjectId = response.object.id;
  selectMapObject(response.object.id);
  renderMapObjects();
}

async function updateSelectedObject() {
  if (!appState.selectedObjectId) return;
  const response = await requestJSON(`/api/map/objects/${encodeURIComponent(appState.selectedObjectId)}`, {
    category: els.objectCategory.value,
    subtype: els.objectSubtype.value,
    latitude: Number(els.objectLatitude.value),
    longitude: Number(els.objectLongitude.value),
    heading: Number(els.objectHeading.value || 0),
  }, "PUT");
  appState.mapPayload = response.map;
  appState.selectedObjectId = response.object.id;
  selectMapObject(response.object.id);
  renderMapObjects();
}

async function deleteSelectedObject() {
  if (!appState.selectedObjectId) return;
  const response = await requestJSON(`/api/map/objects/${encodeURIComponent(appState.selectedObjectId)}`, {}, "DELETE");
  appState.mapPayload = response.map;
  appState.selectedObjectId = null;
  clearObjectInputs();
  renderMapObjects();
}

async function applyMapSettings() {
  const payload = {
    width_m: Number(els.mapWidth.value),
    height_m: Number(els.mapHeight.value),
    resolution_m: Number(els.mapResolution.value),
    base_elevation_m: Number(els.baseElevation.value),
    center_latitude: Number(els.centerLatitude.value),
    center_longitude: Number(els.centerLongitude.value),
  };
  appState.mapPayload = await postJSON("/api/map/settings", payload);
  syncObjectTypes(appState.mapPayload);
  appState.mapSettingsDirty = false;
  refreshMapLayer();
  syncMapSettingsInputs(appState.mapPayload);
  if (appState.selectedObjectId) {
    selectMapObject(appState.selectedObjectId);
  }
  renderMapObjects();
}

function renderState(state) {
  if (!state || !state.vehicle) return;
  appState.latest = state;
  appState.paused = state.paused;
  appState.controls = { ...appState.controls, ...state.controls };
  if (!responseConfigDirty) {
    appState.responseConfig = { ...appState.responseConfig, ...state.response_config };
  }

  updateControlLabels();
  if (!responseConfigDirty) {
    updateResponseInputs();
  }
  if (!appState.mapSettingsDirty && state.map) {
    syncMapSettingsInputs(state.map);
  }
  syncControlValues();
  renderTelemetry(state);
  centerMapOnVehicle(state.vehicle);
}

function syncControlValues() {
  const controls = appState.controls;
  if (document.activeElement !== els.throttle) els.throttle.value = controls.throttle;
  if (document.activeElement !== els.brake) els.brake.value = controls.brake;
  if (document.activeElement !== els.steering) els.steering.value = controls.steering;
  els.parkingBrake.checked = controls.parking_brake;
  els.ignition.checked = controls.ignition !== "off";
  [...els.gearButtons.querySelectorAll("button")].forEach((button) => {
    button.classList.toggle("active", button.dataset.gear === controls.gear);
  });
}

function updateResponseInputs() {
  const config = appState.responseConfig;
  const active = document.activeElement;
  if (active !== els.engineForce) els.engineForce.value = config.engine_force_base ?? 6.8;
  if (active !== els.brakeForce) els.brakeForce.value = config.brake_force ?? 12;
  if (active !== els.rollingBase) els.rollingBase.value = config.rolling_drag_base ?? 0.18;
  if (active !== els.rollingSpeed) els.rollingSpeed.value = config.rolling_drag_speed ?? 0.016;
  if (active !== els.aeroDrag) els.aeroDrag.value = config.aero_drag ?? 0.0045;
  if (active !== els.maxRpm) els.maxRpm.value = config.max_rpm ?? 6200;
  updateResponseLabels();
}

function collectResponseConfig() {
  appState.responseConfig = {
    engine_force_base: Number(els.engineForce.value),
    brake_force: Number(els.brakeForce.value),
    rolling_drag_base: Number(els.rollingBase.value),
    rolling_drag_speed: Number(els.rollingSpeed.value),
    aero_drag: Number(els.aeroDrag.value),
    max_rpm: Number(els.maxRpm.value),
  };
}

function renderTelemetry(state) {
  const vehicle = state.vehicle;
  els.speed.textContent = vehicle.speed_kph.toFixed(1);
  els.rpm.textContent = `${vehicle.engine_rpm.toFixed(0)} rpm`;
  els.pitch.textContent = `${vehicle.pitch.toFixed(1)}°`;
  els.heading.textContent = `${vehicle.heading.toFixed(0)}°`;
  els.grade.textContent = `${vehicle.terrain_grade.toFixed(1)}°`;
  els.elevation.textContent = `${vehicle.elevation_m.toFixed(1)} m`;
  els.localPosition.textContent = `${vehicle.x_m.toFixed(1)}, ${vehicle.y_m.toFixed(1)}`;
  els.lat.textContent = vehicle.latitude.toFixed(6);
  els.lon.textContent = vehicle.longitude.toFixed(6);
  els.mapLabel.textContent = `x ${vehicle.x_m.toFixed(1)} m · y ${vehicle.y_m.toFixed(1)} m`;
  els.clientCount.textContent = state.tcp.clients;
  els.pausedState.textContent = state.paused ? "已暂停" : "运行中";
  els.pauseButton.textContent = state.paused ? "▶" : "II";
  if (!gpsDirty && document.activeElement !== els.latitudeInput) {
    els.latitudeInput.value = vehicle.latitude.toFixed(6);
  }
  if (!gpsDirty && document.activeElement !== els.longitudeInput) {
    els.longitudeInput.value = vehicle.longitude.toFixed(6);
  }
  if (!gpsDirty && document.activeElement !== els.headingInput) {
    els.headingInput.value = vehicle.heading.toFixed(1);
  }
  els.vehicleMarker.style.transform = `translate(-50%, -50%) rotate(${continuousHeading(vehicle.heading)}deg)`;
  updateGridOverlay(vehicle);

  if (state.tcp.received.length) {
    els.tcpMessages.textContent = state.tcp.received
      .slice(0, 8)
      .map((message) => `${message.address}  ${message.payload}`)
      .join("\n");
  }
}

function centerMapOnVehicle(vehicle) {
  if (!appState.olMap) return;
  appState.olMap.getView().setCenter([vehicle.x_m, vehicle.y_m]);
  renderMapObjects();
}

function renderMapObjects() {
  if (!els.objectOverlay || !appState.olMap || !appState.mapPayload) return;
  const objects = appState.mapPayload.objects || [];
  els.objectOverlay.innerHTML = "";
  objects.forEach((item) => {
    const pixel = appState.olMap.getPixelFromCoordinate([item.x_m, item.y_m]);
    if (!pixel) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `map-object ${item.category} ${item.subtype}`;
    button.classList.toggle("selected", item.id === appState.selectedObjectId);
    button.style.left = `${pixel[0]}px`;
    button.style.top = `${pixel[1]}px`;
    button.style.transform = `translate(-50%, -50%) rotate(${item.heading || 0}deg)`;
    button.title = objectLabel(item);
    button.innerHTML = objectIcon(item);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      appState.selectedObjectId = item.id;
      selectMapObject(item.id);
      renderMapObjects();
    });
    els.objectOverlay.appendChild(button);
  });
}

function selectMapObject(objectId) {
  const item = (appState.mapPayload?.objects || []).find((candidate) => candidate.id === objectId);
  if (!item) {
    clearObjectInputs();
    return;
  }
  els.objectCategory.value = item.category;
  populateObjectSubtypes(item.subtype);
  els.objectLatitude.value = Number(item.latitude).toFixed(6);
  els.objectLongitude.value = Number(item.longitude).toFixed(6);
  els.objectHeading.value = Number(item.heading || 0).toFixed(1);
}

function clearObjectInputs() {
  els.objectLatitude.value = "";
  els.objectLongitude.value = "";
  els.objectHeading.value = "0";
}

function objectLabel(item) {
  const categories = { obstacle: "障碍物", v2v: "V2V" };
  const subtypes = {
    cone: "锥桶",
    barrier: "路障",
    pedestrian: "行人",
    car: "轿车",
    truck: "货车",
    bus: "公交",
  };
  return `${categories[item.category] || item.category} · ${subtypes[item.subtype] || item.subtype}`;
}

function objectIcon(item) {
  const icons = {
    cone: '<svg viewBox="0 0 40 40" aria-hidden="true"><path class="object-main" d="M20 5 31 34H9L20 5z"/><path d="M14 24h12M17 15h6" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>',
    barrier: '<svg viewBox="0 0 40 40" aria-hidden="true"><rect class="object-main" x="6" y="12" width="28" height="16" rx="3"/><path d="m11 26 14-12m-3 12 7-6" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>',
    pedestrian: '<svg viewBox="0 0 40 40" aria-hidden="true"><circle class="object-main" cx="20" cy="11" r="6"/><path class="object-main" d="M14 34 20 18l6 16M12 22h16"/></svg>',
    car: '<svg viewBox="0 0 40 48" aria-hidden="true"><path class="object-main" d="M20 4 32 21 28 43H12L8 21 20 4z"/><path d="M14 35h12" stroke="#172326" stroke-width="3" stroke-linecap="round"/></svg>',
    truck: '<svg viewBox="0 0 44 48" aria-hidden="true"><path class="object-main" d="M22 4 35 18v24H9V18L22 4z"/><path d="M13 28h18M13 36h18" stroke="#172326" stroke-width="3" stroke-linecap="round"/></svg>',
    bus: '<svg viewBox="0 0 44 52" aria-hidden="true"><path class="object-main" d="M22 4 34 15v30H10V15L22 4z"/><path d="M14 20h16M14 29h16M16 42h2M26 42h2" stroke="#172326" stroke-width="3" stroke-linecap="round"/></svg>',
  };
  return icons[item.subtype] || icons.car;
}

function updateGridOverlay(vehicle) {
  if (!els.gridOverlay || !appState.mapPayload) return;
  const mapRect = els.mapView.getBoundingClientRect();
  if (!mapRect.width || !mapRect.height) return;

  const viewResolution = appState.olMap?.getView().getResolution() || appState.mapPayload.resolution_m || 5;
  let interval = gridInterval(Math.max(appState.mapPayload.width_m, appState.mapPayload.height_m));
  let minorPx = interval / viewResolution;
  while (minorPx < 28) {
    interval *= 2;
    minorPx = interval / viewResolution;
  }

  const majorPx = minorPx * 5;
  const overlayWidth = mapRect.width * 3;
  const overlayHeight = mapRect.height * 3;
  const baseX = overlayWidth / 2 - (vehicle.x_m / interval) * minorPx;
  const baseY = overlayHeight / 2 + (vehicle.y_m / interval) * minorPx;
  const majorBaseX = overlayWidth / 2 - (vehicle.x_m / (interval * 5)) * majorPx;
  const majorBaseY = overlayHeight / 2 + (vehicle.y_m / (interval * 5)) * majorPx;

  els.gridOverlay.style.width = `${overlayWidth}px`;
  els.gridOverlay.style.height = `${overlayHeight}px`;
  els.gridOverlay.style.transform = `translate(${-mapRect.width}px, ${-mapRect.height}px)`;
  els.gridOverlay.style.backgroundImage = [
    "linear-gradient(to right, rgba(70, 76, 78, 0.42) 1px, transparent 1px)",
    "linear-gradient(to bottom, rgba(70, 76, 78, 0.42) 1px, transparent 1px)",
    "linear-gradient(to right, rgba(42, 48, 50, 0.66) 2px, transparent 2px)",
    "linear-gradient(to bottom, rgba(42, 48, 50, 0.66) 2px, transparent 2px)",
  ].join(", ");
  els.gridOverlay.style.backgroundSize = [
    `${minorPx}px ${minorPx}px`,
    `${minorPx}px ${minorPx}px`,
    `${majorPx}px ${majorPx}px`,
    `${majorPx}px ${majorPx}px`,
  ].join(", ");
  els.gridOverlay.style.backgroundPosition = [
    `${modulo(baseX, minorPx)}px ${modulo(baseY, minorPx)}px`,
    `${modulo(baseX, minorPx)}px ${modulo(baseY, minorPx)}px`,
    `${modulo(majorBaseX, majorPx)}px ${modulo(majorBaseY, majorPx)}px`,
    `${modulo(majorBaseX, majorPx)}px ${modulo(majorBaseY, majorPx)}px`,
  ].join(", ");
}

async function exportMapJson() {
  const response = await fetch("/api/map/export", { cache: "no-store" });
  const payload = await response.json();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "sim-elevation-map.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function importMapJson() {
  const file = els.importMapFile.files[0];
  if (!file) return;
  const text = await file.text();
  const payload = JSON.parse(text);
  appState.mapPayload = await postJSON("/api/map/import", payload);
  syncObjectTypes(appState.mapPayload);
  appState.mapSettingsDirty = false;
  appState.selectedObjectId = null;
  clearObjectInputs();
  refreshMapLayer();
  syncMapSettingsInputs(appState.mapPayload);
  renderMapObjects();
  els.importMapFile.value = "";
}

function syncMapSettingsInputs(payload) {
  if (!payload) return;
  const active = document.activeElement;
  const widthM = payload.width_m ?? payload.width * payload.resolution_m;
  const heightM = payload.height_m ?? payload.height * payload.resolution_m;
  if (active !== els.mapWidth) els.mapWidth.value = roundForInput(widthM, 2);
  if (active !== els.mapHeight) els.mapHeight.value = roundForInput(heightM, 2);
  if (active !== els.mapResolution) els.mapResolution.value = roundForInput(payload.resolution_m, 3);
  if (active !== els.baseElevation) els.baseElevation.value = roundForInput(payload.base_elevation_m ?? 30, 2);
  if (active !== els.centerLatitude) els.centerLatitude.value = Number(payload.center_latitude ?? 31.230416).toFixed(6);
  if (active !== els.centerLongitude) els.centerLongitude.value = Number(payload.center_longitude ?? 121.473701).toFixed(6);
}

function formatNumber(value, digits) {
  return Number(value || 0).toFixed(digits);
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function roundForInput(value, digits) {
  return Number(value || 0).toFixed(digits).replace(/\.?0+$/, "");
}

function continuousHeading(rawHeading) {
  const heading = Number(rawHeading || 0);
  if (appState.markerHeading === null) {
    appState.markerHeading = heading;
    return appState.markerHeading;
  }
  let delta = heading - normalizeHeading(appState.markerHeading);
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  appState.markerHeading += delta;
  return appState.markerHeading;
}

function normalizeHeading(value) {
  return ((value % 360) + 360) % 360;
}

function modulo(value, divisor) {
  return ((value % divisor) + divisor) % divisor;
}
