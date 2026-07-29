"use strict";

const RISK_COLORS = {
  5: "#57c84d",
  15: "#f0df35",
  40: "#e14b3f",
  70: "#d94ad7",
};
const CONTINUOUS_RISK_STOPS = [
  { threshold: 0, color: "#12331d" },
  { threshold: 5, color: RISK_COLORS[5] },
  { threshold: 15, color: RISK_COLORS[15] },
  { threshold: 40, color: RISK_COLORS[40] },
  { threshold: 70, color: RISK_COLORS[70] },
  { threshold: 100, color: "#31004d" },
];
const MAP_DATA_VERSION = "7";

const PRODUCT_META = {
  ml_r40: {
    short: "ML r40 (25 mi)",
    title: "40-km radius ML",
    note: "40-km radius ML forecasts are typically the most conservative in both risk size and severity.",
    detail: "Predicts rainfall exceeding Flash Flood Guidance within 40 km. Test-set analysis shows that this configuration often produces risk areas that are too small and too weak.",
    dash: null,
  },
  ml_r60: {
    short: "ML r60 (37 mi)",
    title: "60-km radius ML",
    note: "60-km radius ML forecasts were the most balanced overall in the test-set analysis.",
    detail: "Predicts rainfall exceeding Flash Flood Guidance within 60 km. This configuration provided the best balance between missed events and overly broad or severe risk areas in the test set.",
    dash: "8 4",
  },
  ml_r75: {
    short: "ML r75 (47 mi)",
    title: "75-km radius ML",
    note: "75-km radius ML forecasts were a close second to 60 km for overall balance.",
    detail: "Predicts rainfall exceeding Flash Flood Guidance within 75 km. It performed similarly to 60 km while tending toward somewhat broader and stronger risk areas.",
    dash: "3 5",
  },
  ml_r100: {
    short: "ML r100 (62 mi)",
    title: "100-km radius ML",
    note: "100-km radius ML forecasts are typically the most aggressive.",
    detail: "Predicts rainfall exceeding Flash Flood Guidance within 100 km. Test-set analysis shows that risk areas can be too large and that high probabilities can be issued too frequently.",
    dash: "12 5 3 5",
  },
  ml_mean: {
    short: "ML Ensemble Mean",
    title: "ML Ensemble Mean",
    note: "The ensemble mean provides a single consensus forecast from the available ML radius configurations.",
    detail: "Averages the available r40, r60, r75, and r100 probabilities independently at each grid point. Unlike probability matching, it does not redistribute values or preserve pooled extremes.",
    dash: "10 3 2 3",
  },
  wpc: {
    short: "WPC ERO",
    title: "WPC Excessive Rainfall Outlook",
    note: "WPC ERO is the official reference forecast shown alongside the experimental ML guidance.",
    detail: "The WPC Excessive Rainfall Outlook is the official categorical reference forecast, expressed here as the probability of rainfall exceeding Flash Flood Guidance within 40 km (25 mi) of a point. Average test-set results indicate better ML skill for Moderate-or-greater risks, but performance varies by event.",
    dash: "1 4",
  },
  pp: {
    short: "Practically Perfect",
    title: "Practically Perfect verification",
    note: "Practically Perfect is an observation-based benchmark, not a forecast.",
    detail: "Built after the valid period from observed flood-proxy locations, then spatially expanded and smoothed to show idealized risk placement. It typically becomes available around 11:10 AM CT the following day.",
    dash: "7 3",
  },
};

const PRODUCT_ORDER = ["ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"];
const THRESHOLDS = [5, 15, 40, 70];
const OBSERVATION_META = {
  stage4_ffg: { label: "Stage IV > FFG", color: "#00e5ff" },
  stage4_ari: { label: "Stage IV ARI", color: "#ff9d36" },
  usgs: { label: "USGS", color: "#58a6ff" },
  flash_lsr: { label: "Flash-flood reports", color: "#ffffff" },
};
const LSR_META = {
  flash_flood: { label: "Flash flood", color: "#ff4fd8" },
  flood: { label: "Flood", color: "#38d9ff" },
  rain: { label: "Rain total", color: "#ffffff" },
  mping_flood: { label: "mPING flood impact", color: "#ff9f43" },
};
const LSR_REFRESH_MS = 5 * 60 * 1000;
const RADAR_API_URL = "https://api.rainviewer.com/public/weather-maps.json";
const RADAR_FRAME_MS = 700;
const RADAR_REFRESH_MS = 10 * 60 * 1000;
const RADAR_OPACITY = 0.58;
const RADAR_CROSSFADE_MS = 260;
const NEXRAD_STATIONS_URL = "https://mesonet.agron.iastate.edu/geojson/network.py?network=NEXRAD&only_online=1";
const SINGLE_RADAR_SCANS_URL = "https://mesonet.agron.iastate.edu/json/radar.py";
const SINGLE_RADAR_TMS_URL = "https://mesonet.agron.iastate.edu/c/tile.py/1.0.0";
const SINGLE_RADAR_PRODUCT = "N0B";
const SINGLE_RADAR_FRAME_MS = 700;
const SINGLE_RADAR_REFRESH_MS = 5 * 60 * 1000;
const SINGLE_RADAR_OPACITY = 0.72;
const SINGLE_RADAR_LOOP_MINUTES = 90;
const SINGLE_RADAR_MAX_FRAMES = 10;
const NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual&message_type=alert";
const FLOOD_ALERT_REFRESH_MS = 5 * 60 * 1000;
const SURFACE_HEIGHT_METERS_PER_PERCENT = 1600;
const SEPARATED_POINT_RADIUS_PIXELS = 0.043;
const COMPACT_POINT_RADIUS_PIXELS = 0.13;
const OBSERVATION_CLEARANCE_METERS = 32000;
const EXPANSION_RADIUS_METERS = 40000;
const WPC_LOCAL_RISK_DISTANCE_KM = 350;
const CONUS_LONGITUDE_SCALE = Math.cos(40 * Math.PI / 180);
const BRIEFING_SEARCH_RADIUS_KM = 40;
const BRIEFING_MAX_GRID_DISTANCE_KM = 100;
const SITE_VIEWS = new Set(["forecast", "skill", "running", "explainability", "about", "creator"]);
const SITE_VIEW_ORDER = ["forecast", "skill", "running", "explainability", "about", "creator"];
const METRIC_META = {
  risk_occurrence_ets: { label: "Day-level ETS", direction: "Higher is better", optimum: "max" },
  risk_occurrence_csi: { label: "Day-level CSI", direction: "Higher is better", optimum: "max" },
  ets: { label: "Pixel ETS", direction: "Higher is better", optimum: "max" },
  csi: { label: "Pixel CSI", direction: "Higher is better", optimum: "max" },
  pod: { label: "Pixel POD", direction: "Higher is better", optimum: "max" },
  far: { label: "Pixel FAR", direction: "Lower is better", optimum: "min" },
  frequency_bias: { label: "Pixel frequency bias", direction: "Closer to 1 is better", optimum: "one" },
  brier_score: { label: "Brier Score", direction: "Lower is better", optimum: "min" },
};
const SIGNED_METRICS = new Set(["ets", "risk_occurrence_ets"]);

const state = {
  forecastDay: 1,
  archive: [],
  day2Archive: null,
  data: null,
  previousDay2Data: null,
  previousDay2Entry: null,
  comparisonBlend: 100,
  comparisonRequest: 0,
  comparisonStatus: "checking",
  selected: "ml_r60",
  contours: new Set(),
  observations: new Set(),
  fillOpacity: 1,
  continuousProbabilities: false,
  fillLayer: null,
  domainLayer: null,
  contourLayer: null,
  observationLayer: null,
  lsrReports: [],
  lsrTypes: new Set(["flash_flood", "flood"]),
  lsrLayer: null,
  lsrTimer: null,
  lsrRequest: 0,
  lsrAvailability: "loading",
  radarEnabled: false,
  radarFrames: [],
  radarHost: "",
  radarFrameIndex: 0,
  radarLayer: null,
  radarLayers: [],
  radarTimer: null,
  radarRefreshTimer: null,
  radarRequest: 0,
  singleRadarEnabled: false,
  singleRadarStations: [],
  selectedSingleRadar: "",
  singleRadarLayer: null,
  singleRadarLayers: [],
  singleRadarFrames: [],
  singleRadarFrameIndex: 0,
  singleRadarTimer: null,
  singleRadarPlaying: true,
  singleRadarRefreshTimer: null,
  singleRadarStationRequest: 0,
  singleRadarRequest: 0,
  radarStationLayer: null,
  selectedPredictor: null,
  selectedPredictorRadius: 60,
  predictorLayer: null,
  floodAlerts: [],
  floodAlertTypes: new Set(["watch", "warning"]),
  floodAlertLayer: null,
  floodAlertTimer: null,
  floodAlertRequest: 0,
  floodAlertAvailability: "loading",
  floodZoneCache: new Map(),
  liveLayersAvailable: false,
  mpingReports: [],
  mpingVisible: false,
  mpingAvailability: "unavailable",
  viewMode: "2d",
  map3d: null,
  deckOverlay: null,
  render3dFrame: null,
  render3dWaiting: false,
  surface3dCache: new Map(),
  separated3dPoints: false,
  showExpansionRings: false,
  siteView: "forecast",
  selectedLocation: null,
  selectedLocationMarker: null,
  briefingText: "",
  skillManifest: null,
  riskOccurrence: null,
  runningVerification: null,
  explainabilityManifest: null,
  tabTransitionTimer: null,
  viewTransition: null,
};

function horizonRoot() {
  return state.forecastDay === 2 ? "day2/" : "";
}

function horizonAsset(path) {
  if (!path) return path;
  if (path.startsWith("day2/") || /^https?:/i.test(path)) return path;
  return `${horizonRoot()}${path}`;
}

function comparisonLayerAvailable() {
  return Boolean(
    state.forecastDay === 1
    && state.previousDay2Data?.layers?.[state.selected]
    && state.data?.layers?.[state.selected]
    && state.selected !== "pp"
    && !state.selectedPredictor
    && state.viewMode === "2d"
  );
}

function activeComparisonWeights() {
  return comparisonLayerAvailable()
    ? window.XGBFFPForecastComparison.blendWeights(state.comparisonBlend)
    : { day1: 1, day2: 0 };
}

function comparisonBlendLabel() {
  const { day1, day2 } = window.XGBFFPForecastComparison.blendWeights(state.comparisonBlend);
  if (day1 >= 1) return "Current Day 1";
  if (day2 >= 1) return "Previous Day 2";
  return `Day 2 ${Math.round(day2 * 100)}% · Day 1 ${Math.round(day1 * 100)}%`;
}

function displayDate(value) {
  const normalized = window.XGBFFPForecastComparison.normalizeDate(value);
  return /^\d{8}$/.test(normalized)
    ? `${normalized.slice(0, 4)}-${normalized.slice(4, 6)}-${normalized.slice(6, 8)}`
    : String(value || "unknown");
}

function updateForecastComparisonUI() {
  const panel = document.getElementById("forecast-comparison");
  const slider = document.getElementById("forecast-comparison-slider");
  const output = document.getElementById("forecast-comparison-value");
  const status = document.getElementById("forecast-comparison-status");
  const day2Label = document.getElementById("forecast-comparison-day2-label");
  panel.hidden = state.forecastDay !== 1;
  if (panel.hidden) return;

  slider.value = String(state.comparisonBlend);
  const hasPrior = Boolean(state.previousDay2Data && state.previousDay2Entry);
  const available = comparisonLayerAvailable();
  slider.disabled = !available;
  panel.classList.toggle("is-unavailable", !available);
  output.textContent = hasPrior ? comparisonBlendLabel() : "Day 1 only";
  day2Label.textContent = hasPrior
    ? `Previous Day 2 · issued ${displayDate(state.previousDay2Entry.issue_date || state.previousDay2Data.issue_date)}`
    : "Previous Day 2";

  if (!hasPrior) {
    status.textContent = state.comparisonStatus === "error"
      ? "The Day‑2 archive could not be checked. The Day‑1 forecast remains available."
      : state.comparisonStatus === "checking"
        ? "Checking for the prior Day‑2 forecast with this valid period…"
        : "No archived prior Day‑2 forecast is available for this valid period.";
    return;
  }
  if (state.viewMode !== "2d") {
    status.textContent = "Forecast evolution is available in the 2D view; the 3D surface remains the current Day‑1 forecast.";
    return;
  }
  if (state.selectedPredictor) {
    status.textContent = "Turn off the predictor overlay to compare the two forecasts.";
    return;
  }
  if (state.selected === "pp") {
    status.textContent = "Select an ML or WPC forecast to compare. Practically Perfect remains available as a contour overlay.";
    return;
  }
  if (!state.previousDay2Data.layers?.[state.selected]) {
    status.textContent = "This product is not available in the matching Day‑2 forecast.";
    return;
  }
  const verificationNote = state.data?.layers?.pp
    ? " Practically Perfect can be enabled under Contour overlays."
    : "";
  status.textContent = `Both forecasts verify during ${state.data.valid_period_label}. Radar and LSR availability remain tied to this Day‑1 valid period.${verificationNote}`;
}

async function ensureDay2Archive() {
  if (Array.isArray(state.day2Archive)) return state.day2Archive;
  const response = await fetch(`day2/archive/index.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Day 2 archive index unavailable (${response.status})`);
  const payload = await response.json();
  state.day2Archive = Array.isArray(payload) ? payload : payload.entries || [];
  return state.day2Archive;
}

async function loadPreviousDay2Forecast() {
  const request = ++state.comparisonRequest;
  state.previousDay2Data = null;
  state.previousDay2Entry = null;
  state.comparisonBlend = 100;
  state.comparisonStatus = state.forecastDay === 1 ? "checking" : "unavailable";
  updateForecastComparisonUI();
  if (state.forecastDay !== 1 || !state.data?.date) return;

  try {
    const entries = await ensureDay2Archive();
    if (request !== state.comparisonRequest) return;
    const entry = window.XGBFFPForecastComparison.findMatchingDay2Entry(entries, state.data.date);
    if (!entry) {
      state.comparisonStatus = "unavailable";
      updateForecastComparisonUI();
      return;
    }
    const mapHref = entry.map_href || `day2/archive/${entry.date}/map.json`;
    const version = entry.map_updated_utc || entry.site_updated_utc || Date.now();
    const response = await fetch(`${mapHref}?v=${encodeURIComponent(version)}-${MAP_DATA_VERSION}`);
    if (!response.ok) throw new Error(`Matching Day 2 map unavailable (${response.status})`);
    const previous = await response.json();
    if (request !== state.comparisonRequest || String(state.data?.date) !== String(entry.date)) return;
    if (!window.XGBFFPForecastComparison.sameValidPeriod(state.data, previous)) {
      throw new Error("Matching Day 2 map has a different valid period");
    }
    state.previousDay2Data = previous;
    state.previousDay2Entry = entry;
    state.comparisonStatus = "available";
  } catch (error) {
    if (request !== state.comparisonRequest) return;
    state.comparisonStatus = "error";
    console.warn(error.message);
  }
  updateForecastComparisonUI();
}

const map = L.map("map", {
  zoomControl: false,
  preferCanvas: true,
  minZoom: 3,
  maxZoom: 9,
  zoomSnap: 0.25,
  zoomDelta: 0.25,
  wheelPxPerZoomLevel: 180,
  wheelDebounceTime: 20,
}).setView([39.5, -92.5], 5);

map.createPane("forecastPriorPane");
map.getPane("forecastPriorPane").style.zIndex = 349;
map.getPane("forecastPriorPane").style.pointerEvents = "none";
map.createPane("forecastPane");
map.getPane("forecastPane").style.zIndex = 350;
map.getPane("forecastPane").style.pointerEvents = "none";
map.createPane("radarPane");
map.getPane("radarPane").style.zIndex = 365;
map.getPane("radarPane").style.pointerEvents = "none";
map.createPane("radarStationPane");
map.getPane("radarStationPane").style.zIndex = 490;
map.createPane("predictorPane");
map.getPane("predictorPane").style.zIndex = 375;
map.getPane("predictorPane").style.pointerEvents = "none";
map.createPane("statePane");
map.getPane("statePane").style.zIndex = 430;
map.getPane("statePane").style.pointerEvents = "none";
map.createPane("domainPane");
map.getPane("domainPane").style.zIndex = 440;
map.getPane("domainPane").style.pointerEvents = "none";
map.createPane("contourPane");
map.getPane("contourPane").style.zIndex = 450;
map.createPane("labelPane");
map.getPane("labelPane").style.zIndex = 500;
map.getPane("labelPane").style.pointerEvents = "none";
map.createPane("observationPane");
map.getPane("observationPane").style.zIndex = 475;
map.createPane("lsrPane");
map.getPane("lsrPane").style.zIndex = 485;
map.createPane("floodAlertPane");
map.getPane("floodAlertPane").style.zIndex = 470;
map.getPane("floodAlertPane").style.pointerEvents = "none";
map.createPane("briefingPane");
map.getPane("briefingPane").style.zIndex = 520;

L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png", {
  subdomains: "abcd",
  maxZoom: 20,
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
}).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png", {
  pane: "labelPane",
  subdomains: "abcd",
  maxZoom: 20,
}).addTo(map);

let stateBoundaryData = null;
fetch("https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json")
  .then((response) => {
    if (!response.ok) throw new Error(`State boundaries unavailable (${response.status})`);
    return response.json();
  })
  .then((data) => {
    stateBoundaryData = data;
    L.geoJSON(data, {
      pane: "statePane",
      interactive: false,
      style: { color: "#b9c5cc", weight: 1.15, opacity: 0.8, fill: false },
    }).addTo(map);
    add3dStateLines();
  })
  .catch((error) => console.warn(error.message));

const canvasRenderer = L.canvas({ pane: "forecastPane", padding: 0.4, tolerance: 3 });
const priorCanvasRenderer = L.canvas({ pane: "forecastPriorPane", padding: 0.4, tolerance: 3 });

function colorRgba(hex, alpha = 255) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255, alpha];
}

function continuousRiskColor(probability, alpha = 255) {
  const value = Math.max(0, Math.min(100, Number(probability) || 0));
  const stops = CONTINUOUS_RISK_STOPS.map((stop) => ({
    threshold: stop.threshold,
    color: colorRgba(stop.color),
  }));
  if (value <= stops[0].threshold) return [...stops[0].color.slice(0, 3), alpha];
  if (value >= stops.at(-1).threshold) return [...stops.at(-1).color.slice(0, 3), alpha];
  for (let index = 1; index < stops.length; index += 1) {
    const upper = stops[index];
    const lower = stops[index - 1];
    if (value > upper.threshold) continue;
    const fraction = (value - lower.threshold) / (upper.threshold - lower.threshold);
    return [
      Math.round(lower.color[0] + (upper.color[0] - lower.color[0]) * fraction),
      Math.round(lower.color[1] + (upper.color[1] - lower.color[1]) * fraction),
      Math.round(lower.color[2] + (upper.color[2] - lower.color[2]) * fraction),
      alpha,
    ];
  }
  return [...stops.at(-1).color.slice(0, 3), alpha];
}

function continuousRiskCss(encodedValue) {
  const [red, green, blue] = continuousRiskColor(Number(encodedValue) / 10);
  return `rgb(${red},${green},${blue})`;
}

function isMlProduct(key) {
  return String(key || "").startsWith("ml_");
}

function continuousProbabilityActive() {
  return state.continuousProbabilities
    && state.viewMode === "2d"
    && isMlProduct(state.selected)
    && !state.selectedPredictor;
}

function updateContinuousProbabilityUI() {
  const checkbox = document.getElementById("continuous-probability-toggle");
  const control = document.getElementById("continuous-probability-control");
  const note = document.getElementById("continuous-probability-note");
  const available = state.viewMode === "2d" && isMlProduct(state.selected) && !state.selectedPredictor;
  checkbox.disabled = !available;
  checkbox.checked = available && state.continuousProbabilities;
  control.classList.toggle("is-unavailable", !available);
  note.textContent = available
    ? "Interpolates the raw ML values from 0–100%; WPC and verification remain categorical."
    : "Available for ML products on the 2D map.";
  const continuous = continuousProbabilityActive();
  document.getElementById("categorical-probability-legend").hidden = continuous;
  document.getElementById("continuous-probability-legend").hidden = !continuous;
  document.getElementById("probability-legend").classList.toggle("continuous", continuous);
}

function forecastDomainBounds() {
  const latitudes = state.data?.grid?.lat || [];
  const longitudes = state.data?.grid?.lon || [];
  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (let index = 0; index < Math.min(latitudes.length, longitudes.length); index += 1) {
    const latitude = Number(latitudes[index]);
    const longitude = Number(longitudes[index]);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    minLat = Math.min(minLat, latitude);
    maxLat = Math.max(maxLat, latitude);
    minLon = Math.min(minLon, longitude);
    maxLon = Math.max(maxLon, longitude);
  }
  return [minLat, maxLat, minLon, maxLon].every(Number.isFinite)
    ? { minLat, maxLat, minLon, maxLon }
    : null;
}

function renderForecastDomain() {
  if (state.domainLayer) map.removeLayer(state.domainLayer);
  state.domainLayer = null;
  const bounds = forecastDomainBounds();
  if (!bounds) return;
  const rectangle = L.rectangle(
    [[bounds.minLat, bounds.minLon], [bounds.maxLat, bounds.maxLon]],
    {
      pane: "domainPane",
      color: "#aeb8be",
      weight: 1.7,
      opacity: 0.9,
      fill: false,
      dashArray: "7 6",
      interactive: false,
    },
  );
  const label = L.tooltip({
    permanent: true,
    direction: "top",
    className: "domain-label",
    opacity: 0.92,
    offset: [0, -4],
  })
    .setLatLng([bounds.maxLat, (bounds.minLon + bounds.maxLon) / 2])
    .setContent("XGBFFP forecast domain");
  state.domainLayer = L.layerGroup([rectangle, label]).addTo(map);
}

function add3dStateLines() {
  const map3d = state.map3d;
  if (!map3d?.isStyleLoaded() || !stateBoundaryData) return;
  if (!map3d.getSource("state-boundaries")) {
    map3d.addSource("state-boundaries", { type: "geojson", data: stateBoundaryData });
  }
  if (!map3d.getLayer("state-boundaries-top")) {
    map3d.addLayer({
      id: "state-boundaries-top",
      type: "line",
      source: "state-boundaries",
      paint: {
        "line-color": "#d5dde2",
        "line-width": 1.25,
        "line-opacity": 0.82,
      },
    });
  }
}

function first3dLabelLayer() {
  return state.map3d?.getStyle()?.layers?.find((layer) => layer.type === "symbol")?.id;
}

function createBoundaryIndex(lines) {
  const cellSize = 1;
  const buckets = new Map();
  let count = 0;
  for (const line of lines || []) {
    for (const coordinate of line) {
      const lat = Number(coordinate[0]);
      const lon = Number(coordinate[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const x = lon * CONUS_LONGITUDE_SCALE;
      const y = lat;
      const key = `${Math.floor(x / cellSize)},${Math.floor(y / cellSize)}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push([x, y]);
      count += 1;
    }
  }
  return { buckets, cellSize, count };
}

function nearestBoundaryKm(index, lat, lon) {
  if (!index?.count) return Infinity;
  const x = lon * CONUS_LONGITUDE_SCALE;
  const y = lat;
  const baseX = Math.floor(x / index.cellSize);
  const baseY = Math.floor(y / index.cellSize);
  let bestSquared = Infinity;
  for (let ring = 0; ring <= 16; ring += 1) {
    for (let dx = -ring; dx <= ring; dx += 1) {
      for (let dy = -ring; dy <= ring; dy += 1) {
        if (ring && Math.abs(dx) !== ring && Math.abs(dy) !== ring) continue;
        const points = index.buckets.get(`${baseX + dx},${baseY + dy}`) || [];
        for (const point of points) {
          const distanceSquared = (point[0] - x) ** 2 + (point[1] - y) ** 2;
          if (distanceSquared < bestSquared) bestSquared = distanceSquared;
        }
      }
    }
    if (Number.isFinite(bestSquared) && Math.sqrt(bestSquared) <= Math.max(0.1, ring - 1) * index.cellSize) break;
  }
  return Math.sqrt(bestSquared) * 111.2;
}

function wpcSurfaceValues() {
  const cacheKey = `${state.data.date}:wpc-probabilities`;
  if (state.surface3dCache.has(cacheKey)) return state.surface3dCache.get(cacheKey);
  const encodedValues = state.data.layers.wpc.values;
  const contours = state.data.contours?.wpc || {};
  const present = [...new Set(encodedValues.filter((value) => value >= 50))].sort((a, b) => a - b);
  const maximumCategory = present.at(-1) || 0;
  const upperBound = { 50: 150, 150: 400, 400: 700, 700: 1000 };
  const boundaryIndexes = Object.fromEntries(THRESHOLDS.map((threshold) => [threshold * 10, createBoundaryIndex(contours[String(threshold)] || [])]));
  const probabilities = new Float32Array(encodedValues.length);

  for (let index = 0; index < encodedValues.length; index += 1) {
    const category = encodedValues[index];
    if (category < 50) continue;
    const upper = upperBound[category] || 1000;
    if (category === maximumCategory) {
      probabilities[index] = category / 10;
      continue;
    }
    const lat = state.data.grid.lat[index];
    const lon = state.data.grid.lon[index];
    const distanceOuter = nearestBoundaryKm(boundaryIndexes[category], lat, lon);
    const distanceInner = nearestBoundaryKm(boundaryIndexes[upper], lat, lon);
    if (!Number.isFinite(distanceOuter) || !Number.isFinite(distanceInner) || distanceInner > WPC_LOCAL_RISK_DISTANCE_KM) {
      probabilities[index] = category / 10;
      continue;
    }
    const fraction = distanceOuter / Math.max(0.001, distanceOuter + distanceInner);
    probabilities[index] = (category + (upper - category) * fraction) / 10;
  }
  state.surface3dCache.set(cacheKey, probabilities);
  return probabilities;
}

function surface3dData(key) {
  const cacheKey = `${state.data.date}:${key}`;
  if (state.surface3dCache.has(cacheKey)) return state.surface3dCache.get(cacheKey);
  const encodedValues = state.data.layers[key].values;
  const probabilities = key === "wpc" ? wpcSurfaceValues() : null;
  const points = [];
  for (let index = 0; index < encodedValues.length; index += 1) {
    if (encodedValues[index] < 50) continue;
    points.push({
      position: [state.data.grid.lon[index], state.data.grid.lat[index]],
      encoded: encodedValues[index],
      probability: probabilities ? probabilities[index] : encodedValues[index] / 10,
      wpcRange: key === "wpc" ? wpcRiskRange(encodedValues[index]) : null,
    });
  }
  state.surface3dCache.set(cacheKey, points);
  return points;
}

function wpcRiskRange(encodedValue) {
  if (encodedValue >= 700) return "70–100%";
  if (encodedValue >= 400) return "40–70%";
  if (encodedValue >= 150) return "15–40%";
  if (encodedValue >= 50) return "5–15%";
  return "Below 5%";
}

function nearestVisibleCity(position, x, y) {
  if (!state.map3d || !position || !Number.isFinite(x) || !Number.isFinite(y)) return "";
  const placeLayers = (state.map3d.getStyle()?.layers || [])
    .filter((layer) => layer.type === "symbol"
      && layer["source-layer"] === "place"
      && /place_(city|capital|town|village|hamlet)/.test(layer.id))
    .map((layer) => layer.id);
  if (!placeLayers.length) return "";
  let features = state.map3d.queryRenderedFeatures(
    [[x - 220, y - 220], [x + 220, y + 220]],
    { layers: placeLayers },
  );
  if (!features.some((feature) => feature.properties?.name_en || feature.properties?.name)) {
    features = state.map3d.querySourceFeatures("carto", { sourceLayer: "place" })
      .filter((feature) => ["city", "town", "village", "hamlet"].includes(
        String(feature.properties?.class || feature.properties?.type || "").toLowerCase(),
      ));
  }
  const origin = state.map3d.project(position);
  let nearest = null;
  let nearestDistance = Infinity;
  for (const feature of features) {
    const coordinates = feature.geometry?.coordinates;
    const name = feature.properties?.name_en || feature.properties?.name;
    if (!Array.isArray(coordinates) || !name) continue;
    const projected = state.map3d.project(coordinates);
    const distance = Math.hypot(projected.x - origin.x, projected.y - origin.y);
    if (distance < nearestDistance) {
      nearest = String(name);
      nearestDistance = distance;
    }
  }
  return nearest || "";
}

function visible3dReports() {
  const threshold = Number(document.getElementById("rain-threshold").value);
  const localReports = state.lsrReports.filter((report) => state.lsrTypes.has(report.kind)
    && (report.kind !== "rain" || (Number.isFinite(report.amount) && report.amount >= threshold)));
  return state.mpingVisible ? localReports.concat(state.mpingReports) : localReports;
}

function build3dLayers() {
  if (!state.data?.layers?.[state.selected] || !window.deck) return [];
  const surface = surface3dData(state.selected);
  let maximumProbability = 5;
  for (const point of surface) maximumProbability = Math.max(maximumProbability, point.probability);
  const referenceHeight = maximumProbability * SURFACE_HEIGHT_METERS_PER_PERCENT + OBSERVATION_CLEARANCE_METERS;
  const beforeId = first3dLabelLayer();
  const pointRadius = state.separated3dPoints ? SEPARATED_POINT_RADIUS_PIXELS : COMPACT_POINT_RADIUS_PIXELS;
  const shared = beforeId ? { beforeId } : {};
  const layers = [new deck.ColumnLayer({
    ...shared,
    id: `forecast-surface-${state.data.date}-${state.selected}`,
    data: surface,
    diskResolution: 8,
    radius: pointRadius,
    radiusUnits: "pixels",
    extruded: true,
    filled: true,
    wireframe: false,
    opacity: state.fillOpacity,
    pickable: true,
    getPosition: (point) => point.position,
    getElevation: (point) => point.probability * SURFACE_HEIGHT_METERS_PER_PERCENT,
    getFillColor: (point) => continuousRiskColor(point.probability),
    transitions: { getElevation: 350 },
  })];
  const domain = forecastDomainBounds();
  if (domain) {
    const domainHeight = 2500;
    const path = [
      [domain.minLon, domain.minLat, domainHeight],
      [domain.maxLon, domain.minLat, domainHeight],
      [domain.maxLon, domain.maxLat, domainHeight],
      [domain.minLon, domain.maxLat, domainHeight],
      [domain.minLon, domain.minLat, domainHeight],
    ];
    layers.push(new deck.PathLayer({
      ...shared,
      id: `xgbffp-domain-3d-${state.data.date}`,
      data: [{ path, domainBoundary: true }],
      getPath: (item) => item.path,
      getColor: [174, 184, 190, 235],
      getWidth: 3000,
      widthUnits: "meters",
      widthMinPixels: 2,
      getDashArray: [10000, 7500],
      dashJustified: true,
      extensions: [new deck.PathStyleExtension({ dash: true })],
      pickable: true,
    }));
    layers.push(new deck.TextLayer({
      ...shared,
      id: `xgbffp-domain-label-3d-${state.data.date}`,
      data: [{
        position: [
          (domain.minLon + domain.maxLon) / 2,
          domain.maxLat + 0.12,
          domainHeight + 2000,
        ],
        text: "XGBFFP forecast domain",
        domainBoundary: true,
      }],
      getPosition: (item) => item.position,
      getText: (item) => item.text,
      getColor: [218, 224, 228, 255],
      getSize: 14,
      sizeUnits: "pixels",
      getTextAnchor: "middle",
      getAlignmentBaseline: "bottom",
      background: true,
      getBackgroundColor: [25, 30, 34, 220],
      backgroundPadding: [5, 3],
      billboard: true,
      pickable: true,
    }));
  }

  for (const key of state.contours) {
    const source = state.data.contours?.[key];
    if (!source) continue;
    for (const threshold of THRESHOLDS) {
      const paths = (source[String(threshold)] || []).map((line) => ({
        path: line.map(([lat, lon]) => [lon, lat, referenceHeight + threshold * 180]),
        key,
        threshold,
      }));
      if (!paths.length) continue;
      layers.push(new deck.PathLayer({
        ...shared,
        id: `contour-3d-${key}-${threshold}`,
        data: paths,
        getPath: (item) => item.path,
        getColor: colorRgba(RISK_COLORS[threshold]),
        getWidth: key === "pp" ? 5200 : 4200,
        widthUnits: "meters",
        widthMinPixels: key === "pp" ? 3 : 2,
        jointRounded: true,
        capRounded: true,
        getDashArray: PRODUCT_META[key]?.dash?.split(/\s+/).map(Number) || [0, 0],
        dashJustified: true,
        extensions: [new deck.PathStyleExtension({ dash: true })],
        pickable: true,
      }));
    }
  }

  const observations = [];
  for (const key of state.observations) {
    const source = state.data.observations?.[key];
    const meta = OBSERVATION_META[key] || { label: source?.label || key, color: "#fff" };
    for (const [lat, lon] of source?.points || []) observations.push({ position: [lon, lat], meta });
  }
  if (observations.length) layers.push(new deck.ColumnLayer({
    ...shared,
    id: "verification-observations-3d",
    data: observations,
    diskResolution: 10,
    radius: pointRadius,
    radiusUnits: "pixels",
    extruded: true,
    filled: true,
    wireframe: false,
    opacity: state.fillOpacity,
    pickable: true,
    getPosition: (item) => item.position,
    getElevation: referenceHeight,
    getFillColor: (item) => colorRgba(item.meta.color),
  }));

  const reports = visible3dReports();
  if (reports.length) layers.push(new deck.ColumnLayer({
    ...shared,
    id: "local-storm-reports-3d",
    data: reports,
    diskResolution: 12,
    radius: pointRadius,
    radiusUnits: "pixels",
    extruded: true,
    filled: true,
    wireframe: false,
    opacity: state.fillOpacity,
    pickable: true,
    getPosition: (report) => [report.lon, report.lat],
    getElevation: referenceHeight + 10000,
    getFillColor: (report) => colorRgba(LSR_META[report.kind].color),
  }));
  if (state.showExpansionRings) {
    const ringHeight = referenceHeight + 22000;
    const rings = observations.map((item) => ({
      position: [item.position[0], item.position[1], ringHeight],
      meta: item.meta,
      expansionRing: true,
    }));
    for (const report of reports) {
      if (report.provider === "mping") continue;
      rings.push({
        position: [report.lon, report.lat, ringHeight],
        meta: LSR_META[report.kind],
        expansionRing: true,
      });
    }
    if (rings.length) layers.push(new deck.ScatterplotLayer({
      ...shared,
      id: "forty-km-expansion-rings-3d",
      data: rings,
      radiusUnits: "meters",
      lineWidthUnits: "pixels",
      stroked: true,
      filled: true,
      pickable: true,
      getPosition: (item) => item.position,
      getRadius: EXPANSION_RADIUS_METERS,
      getLineColor: (item) => colorRgba(item.meta.color, 230),
      getFillColor: (item) => colorRgba(item.meta.color, 10),
      getLineWidth: 1.5,
      lineWidthMinPixels: 1.25,
    }));
  }
  if (state.selectedLocation) {
    layers.push(new deck.ScatterplotLayer({
      ...shared,
      id: "location-briefing-selection-3d",
      data: [{
        position: [
          state.selectedLocation.longitude,
          state.selectedLocation.latitude,
          SURFACE_HEIGHT_METERS_PER_PERCENT * 75,
        ],
      }],
      radiusUnits: "pixels",
      stroked: true,
      filled: true,
      pickable: false,
      getPosition: (item) => item.position,
      getRadius: 8,
      getLineColor: [255, 255, 255, 255],
      getFillColor: [9, 11, 13, 245],
      getLineWidth: 3,
      lineWidthUnits: "pixels",
    }));
  }
  return layers;
}

function render3d() {
  if (state.viewMode !== "3d" || !state.deckOverlay || !state.map3d) return;
  if (!state.map3d.isStyleLoaded()) {
    if (!state.render3dWaiting) {
      state.render3dWaiting = true;
      state.map3d.once("idle", () => {
        state.render3dWaiting = false;
        render3d();
      });
    }
    return;
  }
  state.render3dWaiting = false;
  state.deckOverlay.setProps({
    layers: build3dLayers(),
    getTooltip: ({ object, x, y }) => {
      if (!object) return null;
      if (object.domainBoundary) return "XGBFFP forecast domain";
      if (object.expansionRing) return `${object.meta.label} · 40-km expansion`;
      if (object.probability) {
        const city = nearestVisibleCity(object.position, x, y);
        const value = state.selected === "wpc"
          ? `${PRODUCT_META.wpc.short}: ${object.wpcRange} category`
          : `${PRODUCT_META[state.selected].short}: ${object.probability.toFixed(1)}%`;
        return city ? `${value}\nNearest city: ${city}` : value;
      }
      if (object.threshold) return `${PRODUCT_META[object.key]?.short || object.key}: >${object.threshold}% contour`;
      if (object.kind) return `${LSR_META[object.kind]?.label || object.type}${Number.isFinite(object.amount) ? ` · ${object.amount.toFixed(2)} in` : ""}`;
      if (object.meta) return object.meta.label;
      return null;
    },
  });
  add3dStateLines();
}

function schedule3dRender() {
  if (state.viewMode !== "3d") return;
  cancelAnimationFrame(state.render3dFrame);
  state.render3dFrame = requestAnimationFrame(render3d);
}

function updateUrl(mode = "replace") {
  const parameters = new URLSearchParams();
  parameters.set("view", state.siteView);
  parameters.set("day", String(state.forecastDay));
  if (state.data?.date) parameters.set("date", state.data.date);
  if (state.viewMode === "3d") parameters.set("map", "3d");
  const method = mode === "push" ? "pushState" : "replaceState";
  history[method]({ view: state.siteView }, "", `?${parameters}`);
}

function initialize3dMap() {
  if (state.map3d) return;
  if (!window.maplibregl || !window.deck?.MapboxOverlay) throw new Error("The 3D map libraries did not load");
  const center = map.getCenter();
  state.map3d = new maplibregl.Map({
    container: "map-3d",
    style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    center: [center.lng, center.lat],
    zoom: map.getZoom(),
    pitch: 20,
    bearing: 0,
    minPitch: 20,
    maxPitch: 20,
    minZoom: 3,
    maxZoom: 9,
    dragRotate: false,
    touchPitch: false,
    pitchWithRotate: false,
    antialias: true,
    attributionControl: true,
  });
  state.map3d.touchZoomRotate.disableRotation();
  state.map3d.scrollZoom.setWheelZoomRate?.(1 / 900);
  state.map3d.scrollZoom.setZoomRate?.(1 / 180);
  state.map3d.touchZoomRotate.setZoomRate?.(0.5);
  state.map3d.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  state.deckOverlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
  state.map3d.addControl(state.deckOverlay);
  state.map3d.on("load", () => {
    add3dStateLines();
    schedule3dRender();
  });
  state.map3d.on("click", (event) => {
    selectBriefingLocation(event.lngLat.lat, event.lngLat.lng);
  });
  state.map3d.on("error", (event) => {
    console.error("3D map error", event.error || event);
    if (state.viewMode === "3d") {
      document.getElementById("product-message").textContent = "The 3D basemap could not be loaded. The standard 2D view remains available.";
    }
  });
}

function setViewMode(mode) {
  if (mode === state.viewMode) return;
  if (mode === "3d" && state.selectedPredictor) {
    state.selectedPredictor = null;
    buildLayerControls();
  }
  const map2dElement = document.getElementById("map");
  const map3dElement = document.getElementById("map-3d");
  if (mode === "3d") {
    map3dElement.hidden = false;
    state.viewMode = "3d";
    try {
      initialize3dMap();
    } catch (error) {
      state.viewMode = "2d";
      map3dElement.hidden = true;
      document.getElementById("product-message").textContent = "This browser could not start the 3D map. The standard 2D view remains available.";
      console.error(error);
      return;
    }
    const center = map.getCenter();
    state.map3d.jumpTo({ center: [center.lng, center.lat], zoom: map.getZoom(), pitch: 20, bearing: 0 });
    map2dElement.hidden = true;
    clearInterval(state.radarTimer);
    state.radarTimer = null;
    clearRadarLayer();
    clearTimeout(state.singleRadarRefreshTimer);
    state.singleRadarRefreshTimer = null;
    stopSingleRadarAnimation();
    clearSingleRadarLayer();
    clearRadarStationMarkers();
    state.map3d.resize();
    schedule3dRender();
  } else {
    const center = state.map3d.getCenter();
    map.setView([center.lat, center.lng], state.map3d.getZoom(), { animate: false });
    state.viewMode = "2d";
    map3dElement.hidden = true;
    map2dElement.hidden = false;
    map.invalidateSize();
    renderFilledLayer();
    renderContours();
    renderObservations();
    renderLsrs();
    renderPredictorLayer();
    renderForecastDomain();
    renderRadarStationMarkers();
    if (state.radarEnabled && state.radarFrames.length) preloadRadarLayers();
    if (state.singleRadarEnabled) fetchSingleRadarFrames(true);
  }
  document.getElementById("height-legend").hidden = mode !== "3d";
  document.getElementById("predictor-legend").hidden = mode !== "2d" || !state.selectedPredictor;
  document.getElementById("point-gap-control").hidden = mode !== "3d";
  updateContinuousProbabilityUI();
  updateForecastComparisonUI();
  if (mode === "3d" && (state.radarEnabled || state.singleRadarEnabled)) {
    document.getElementById("radar-status").textContent = "Radar overlays are available in 2D view.";
  }
  updateSingleRadarPlaybackControls();
  document.getElementById("opacity-control-label").textContent = mode === "3d" ? "3D point opacity" : "Forecast opacity";
  for (const candidate of ["2d", "3d"]) {
    const button = document.getElementById(`view-${candidate}`);
    const active = candidate === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  if (state.data?.date) updateUrl();
}

function riskColor(encodedValue) {
  if (encodedValue >= 700) return RISK_COLORS[70];
  if (encodedValue >= 400) return RISK_COLORS[40];
  if (encodedValue >= 150) return RISK_COLORS[15];
  if (encodedValue >= 50) return RISK_COLORS[5];
  return null;
}

function riskLabel(encodedValue) {
  if (encodedValue >= 700) return ">70%";
  if (encodedValue >= 400) return ">40%";
  if (encodedValue >= 150) return ">15%";
  if (encodedValue >= 50) return ">5%";
  return "<5%";
}

function formatBriefingProbability(value) {
  return window.XGBFFPBriefing.formatProbability(value);
}

function pointInRing(longitude, latitude, ring) {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const [x, y] = ring[index];
    const [previousX, previousY] = ring[previous];
    const intersects = ((y > latitude) !== (previousY > latitude))
      && (longitude < (previousX - x) * (latitude - y) / ((previousY - y) || Number.EPSILON) + x);
    if (intersects) inside = !inside;
  }
  return inside;
}

function pointInPolygon(longitude, latitude, polygon) {
  if (!polygon?.length || !pointInRing(longitude, latitude, polygon[0])) return false;
  return !polygon.slice(1).some((hole) => pointInRing(longitude, latitude, hole));
}

function geometryContains(geometry, latitude, longitude) {
  if (geometry?.type === "Polygon") return pointInPolygon(longitude, latitude, geometry.coordinates);
  if (geometry?.type === "MultiPolygon") {
    return geometry.coordinates.some((polygon) => pointInPolygon(longitude, latitude, polygon));
  }
  return false;
}

function nearbyItems(items, latitude, longitude, radiusKm = BRIEFING_SEARCH_RADIUS_KM) {
  return items.map((item) => ({
    ...item,
    distanceKm: window.XGBFFPBriefing.haversineKm(latitude, longitude, item.lat, item.lon),
  })).filter((item) => item.distanceKm <= radiusKm).sort((left, right) => left.distanceKm - right.distanceKm);
}

function currentAlertContext(latitude, longitude) {
  if (state.floodAlertAvailability !== "available") {
    return { watch: "Not available", warning: "Not available", matches: [] };
  }
  const matches = state.floodAlerts.filter((feature) => geometryContains(feature.geometry, latitude, longitude));
  return {
    watch: matches.some((feature) => feature.properties.kind === "watch") ? "Yes" : "No",
    warning: matches.some((feature) => feature.properties.kind === "warning") ? "Yes" : "No",
    matches,
  };
}

function selectedPredictorDiagnostics(index) {
  const radiusKey = `r${state.selectedPredictorRadius}`;
  return Object.values(state.data?.predictors?.[radiusKey] || {})
    .sort((left, right) => left.rank - right.rank)
    .map((predictor) => {
      const decoded = window.XGBFFPBriefing.decodePredictor(predictor, index);
      return decoded ? { ...predictor, ...decoded } : null;
    })
    .filter(Boolean);
}

function observationContext(latitude, longitude) {
  const results = [];
  for (const [key, source] of Object.entries(state.data?.observations || {})) {
    const points = (source.points || []).map(([lat, lon]) => ({ lat, lon }));
    const nearest = nearbyItems(points, latitude, longitude)[0];
    if (nearest) results.push({
      key,
      label: OBSERVATION_META[key]?.label || source.label || key,
      distanceKm: nearest.distanceKm,
    });
  }
  return results;
}

function reportContext(latitude, longitude) {
  const lsr = state.lsrAvailability === "available"
    ? nearbyItems(state.lsrReports, latitude, longitude)
    : [];
  const mping = state.mpingAvailability === "available"
    ? nearbyItems(state.mpingReports, latitude, longitude)
    : [];
  return { lsr, mping };
}

function productProbabilityRows(index) {
  const keys = ["ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"];
  return keys.map((key) => {
    const probability = window.XGBFFPBriefing.probabilityPercent(state.data?.layers?.[key], index);
    return {
      key,
      label: PRODUCT_META[key]?.short || key,
      probability,
      category: window.XGBFFPBriefing.riskCategory(probability),
      active: state.selected === key,
    };
  });
}

function previousDay2ProductAtLocation(key, latitude, longitude) {
  if (!state.previousDay2Data?.layers?.[key] || key === "pp") return null;
  const nearest = window.XGBFFPBriefing.nearestGridPoint(
    state.previousDay2Data.grid,
    latitude,
    longitude,
    BRIEFING_MAX_GRID_DISTANCE_KM,
  );
  if (!nearest) return null;
  const probability = window.XGBFFPBriefing.probabilityPercent(
    state.previousDay2Data.layers[key],
    nearest.index,
  );
  return {
    ...nearest,
    probability,
    category: window.XGBFFPBriefing.riskCategory(probability),
  };
}

function deterministicBriefingInterpretation(agreement, wpcCategory, ppProbability) {
  if (!agreement) {
    return "Standard ML radius guidance is not available at this archived grid point.";
  }
  const mlCategory = window.XGBFFPBriefing.riskCategory(agreement.mean);
  const exceedance = agreement.exceedanceCounts[15];
  let text = `${exceedance} of ${agreement.count} standard ML neighborhood configurations meet or exceed Slight-level guidance. `;
  text += `Their mean is ${mlCategory.label}, with ${agreement.qualitative.toLowerCase()} agreement under the documented spread/category rule. `;
  if (wpcCategory.rank >= 0 && wpcCategory.rank !== mlCategory.rank) {
    const direction = wpcCategory.rank < mlCategory.rank ? "lower" : "higher";
    text += `The WPC outlook is ${wpcCategory.label}, ${direction} than the standard-ML mean category. `;
  } else if (wpcCategory.rank >= 0) {
    text += `The WPC outlook is in the same ${wpcCategory.label} category as the standard-ML mean. `;
  }
  if (Number.isFinite(ppProbability)) {
    text += `Post-event Practically Perfect context is ${formatBriefingProbability(ppProbability)} at the nearest grid point. `;
  }
  return `${text}This is experimental guidance, not an official NWS forecast, watch, or warning.`;
}

function addDefinitionListRow(list, term, value, className = "") {
  const wrapper = document.createElement("div");
  if (className) wrapper.className = className;
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value;
  wrapper.append(dt, dd);
  list.append(wrapper);
}

function renderLocationBriefing() {
  const panel = document.getElementById("location-briefing");
  const content = document.getElementById("location-briefing-content");
  if (!state.selectedLocation || !state.data) {
    panel.hidden = true;
    content.replaceChildren();
    return;
  }
  panel.hidden = false;
  content.replaceChildren();
  const { latitude, longitude, index, distanceKm } = state.selectedLocation;
  const rows = productProbabilityRows(index);
  const agreement = window.XGBFFPBriefing.agreementSummary(state.data, index);
  const wpc = rows.find((row) => row.key === "wpc");
  const pp = rows.find((row) => row.key === "pp");
  const alertContext = currentAlertContext(latitude, longitude);
  const reports = reportContext(latitude, longitude);
  const observations = observationContext(latitude, longitude);

  const location = document.createElement("p");
  location.className = "briefing-location";
  location.textContent = `${Math.abs(latitude).toFixed(3)}°${latitude >= 0 ? "N" : "S"}, ${Math.abs(longitude).toFixed(3)}°${longitude >= 0 ? "E" : "W"} · nearest grid point ${distanceKm.toFixed(1)} km away`;
  const valid = document.createElement("p");
  valid.className = "briefing-valid";
  valid.textContent = `Valid ${state.data.valid_period_label}`;
  content.append(location, valid);

  const probabilitySection = document.createElement("section");
  const probabilityHeading = document.createElement("h3");
  probabilityHeading.textContent = "Forecast probabilities";
  const probabilityList = document.createElement("dl");
  probabilityList.className = "briefing-data-list";
  for (const row of rows) {
    const suffix = row.category.rank >= 0 ? ` · ${row.category.label}` : "";
    addDefinitionListRow(
      probabilityList,
      `${row.label}${row.active ? " (displayed)" : ""}`,
      `${formatBriefingProbability(row.probability)}${suffix}`,
      row.active ? "active-product-row" : "",
    );
  }
  probabilitySection.append(probabilityHeading, probabilityList);
  content.append(probabilitySection);

  const activeRow = rows.find((row) => row.key === state.selected);
  const previousDay2 = previousDay2ProductAtLocation(state.selected, latitude, longitude);
  if (previousDay2 && activeRow) {
    const evolutionSection = document.createElement("section");
    const evolutionHeading = document.createElement("h3");
    evolutionHeading.textContent = "Forecast evolution at this point";
    const evolutionList = document.createElement("dl");
    evolutionList.className = "briefing-data-list";
    const priorSuffix = previousDay2.category.rank >= 0 ? ` · ${previousDay2.category.label}` : "";
    const currentSuffix = activeRow.category.rank >= 0 ? ` · ${activeRow.category.label}` : "";
    addDefinitionListRow(
      evolutionList,
      `Previous Day 2 · issued ${displayDate(state.previousDay2Entry?.issue_date || state.previousDay2Data.issue_date)}`,
      `${formatBriefingProbability(previousDay2.probability)}${priorSuffix}`,
    );
    addDefinitionListRow(
      evolutionList,
      "Current Day 1",
      `${formatBriefingProbability(activeRow.probability)}${currentSuffix}`,
      "active-product-row",
    );
    if (Number.isFinite(previousDay2.probability) && Number.isFinite(activeRow.probability)) {
      const difference = activeRow.probability - previousDay2.probability;
      addDefinitionListRow(
        evolutionList,
        "Day 1 change",
        `${difference >= 0 ? "+" : ""}${difference.toFixed(1)} percentage points`,
      );
    }
    if (Number.isFinite(pp?.probability)) {
      addDefinitionListRow(
        evolutionList,
        "Practically Perfect",
        `${formatBriefingProbability(pp.probability)} · ${pp.category.label}`,
      );
    }
    const evolutionNote = document.createElement("p");
    evolutionNote.className = "briefing-note";
    evolutionNote.textContent = `Both forecasts cover ${state.data.valid_period_label}. Move the map slider to compare their full spatial placement.`;
    evolutionSection.append(evolutionHeading, evolutionList, evolutionNote);
    content.append(evolutionSection);
  }

  const agreementSection = document.createElement("section");
  const agreementHeading = document.createElement("h3");
  agreementHeading.textContent = "Standard ML agreement";
  const agreementList = document.createElement("dl");
  agreementList.className = "briefing-data-list";
  if (agreement) {
    addDefinitionListRow(agreementList, "Agreement", agreement.qualitative);
    addDefinitionListRow(agreementList, "Minimum / maximum", `${agreement.minimum.toFixed(1)}% / ${agreement.maximum.toFixed(1)}%`);
    addDefinitionListRow(agreementList, "Probability range", `${agreement.range.toFixed(1)} percentage points`);
    addDefinitionListRow(agreementList, "Mean / standard deviation", `${agreement.mean.toFixed(1)}% / ${agreement.standardDeviation.toFixed(1)} points`);
    for (const threshold of [5, 15, 40, 70]) {
      addDefinitionListRow(agreementList, `At or above ${threshold}%`, `${agreement.exceedanceCounts[threshold]} of ${agreement.count}`);
    }
  } else {
    addDefinitionListRow(agreementList, "Agreement", "Not available");
  }
  const agreementRule = document.createElement("p");
  agreementRule.className = "briefing-note";
  agreementRule.textContent = "High: all four members share a category and span ≤10 points. Moderate: adjacent categories or span ≤20 points. Otherwise Low.";
  agreementSection.append(agreementHeading, agreementList, agreementRule);
  content.append(agreementSection);

  const interpretationSection = document.createElement("section");
  const interpretationHeading = document.createElement("h3");
  interpretationHeading.textContent = "Interpretation";
  const interpretation = document.createElement("p");
  interpretation.textContent = deterministicBriefingInterpretation(agreement, wpc?.category || { rank: -1 }, pp?.probability);
  interpretationSection.append(interpretationHeading, interpretation);
  content.append(interpretationSection);

  const predictorSection = document.createElement("section");
  const predictorHeading = document.createElement("h3");
  predictorHeading.textContent = `${state.selectedPredictorRadius}-km predictor diagnostics`;
  const predictors = selectedPredictorDiagnostics(index);
  const predictorList = document.createElement("dl");
  predictorList.className = "briefing-data-list";
  for (const predictor of predictors) {
    addDefinitionListRow(
      predictorList,
      `#${predictor.rank} ${predictor.label}`,
      `${predictor.value.toFixed(3)} ${predictor.units} · ${predictor.percentilePosition.toFixed(0)}% of published display scale`,
    );
  }
  if (!predictors.length) addDefinitionListRow(predictorList, "Predictors", "Not available for this archive");
  const predictorNote = document.createElement("p");
  predictorNote.className = "briefing-note";
  predictorNote.textContent = "These are raw predictor values and normalized display positions, not local SHAP contributions.";
  predictorSection.append(predictorHeading, predictorList, predictorNote);
  content.append(predictorSection);

  const contextSection = document.createElement("section");
  const contextHeading = document.createElement("h3");
  contextHeading.textContent = `Alerts and observations within ${BRIEFING_SEARCH_RADIUS_KM} km`;
  const contextList = document.createElement("dl");
  contextList.className = "briefing-data-list";
  addDefinitionListRow(contextList, "Inside active flood watch", alertContext.watch);
  addDefinitionListRow(contextList, "Inside active flood warning", alertContext.warning);
  for (const kind of ["flash_flood", "flood", "rain"]) {
    const rainThreshold = Number(document.getElementById("rain-threshold").value);
    const nearest = reports.lsr.find((report) => (
      report.kind === kind
      && (kind !== "rain" || (Number.isFinite(report.amount) && report.amount >= rainThreshold))
    ));
    addDefinitionListRow(
      contextList,
      `Nearby ${LSR_META[kind].label}`,
      nearest ? `${nearest.distanceKm.toFixed(1)} km${nearest.valid ? ` · ${new Date(nearest.valid).toLocaleString()}` : ""}` : (state.lsrAvailability === "available" ? "None found" : "Not available"),
    );
  }
  const nearestMping = reports.mping[0];
  addDefinitionListRow(contextList, "Nearby mPING flood impact", nearestMping ? `${nearestMping.distanceKm.toFixed(1)} km` : (state.mpingAvailability === "available" ? "None found" : "Not available"));
  addDefinitionListRow(contextList, "Nearby UFVS flood proxy", observations.length ? observations.map((item) => `${item.label} ${item.distanceKm.toFixed(1)} km`).join("; ") : (state.data.layers.pp ? "None found" : "Not available"));
  contextSection.append(contextHeading, contextList);
  content.append(contextSection);

  if (state.data.layers.pp) {
    const active = rows.find((row) => row.key === state.selected);
    const activeThreshold = [70, 40, 15, 5].find((threshold) => active?.probability >= threshold);
    const localVerification = document.createElement("p");
    localVerification.className = "briefing-note";
    localVerification.textContent = activeThreshold
      ? `Displayed-product ${THRESHOLD_LABELS_CLIENT[activeThreshold]} threshold verified locally against Practically Perfect: ${pp.probability >= activeThreshold ? "Yes" : "No"}.`
      : "The displayed product is below the Marginal threshold at this grid point; categorical local verification is not applicable.";
    content.append(localVerification);
    const verificationNote = document.createElement("p");
    verificationNote.className = "warning-callout";
    verificationNote.textContent = "This location-level result is event context, not a statistically complete measure of overall model performance.";
    content.append(verificationNote);
  }

  state.briefingText = window.XGBFFPBriefing.copyBriefingText({
    latitude,
    longitude,
    validPeriod: state.data.valid_period_label,
    ensembleMean: rows.find((row) => row.key === "ml_mean")?.probability,
    agreement,
    wpcCategory: wpc?.category?.label,
    activeWarning: alertContext.warning,
  });
}

function updateLocationBriefing() {
  if (!state.selectedLocation || !state.data) return;
  const nearest = window.XGBFFPBriefing.nearestGridPoint(
    state.data.grid,
    state.selectedLocation.clickedLatitude,
    state.selectedLocation.clickedLongitude,
    BRIEFING_MAX_GRID_DISTANCE_KM,
  );
  if (!nearest) {
    clearBriefingLocation();
    document.getElementById("product-message").textContent = "That point is outside the XGBFFP forecast grid.";
    return;
  }
  state.selectedLocation = {
    ...nearest,
    clickedLatitude: state.selectedLocation.clickedLatitude,
    clickedLongitude: state.selectedLocation.clickedLongitude,
  };
  if (state.selectedLocationMarker) map.removeLayer(state.selectedLocationMarker);
  state.selectedLocationMarker = L.circleMarker([nearest.latitude, nearest.longitude], {
    pane: "briefingPane",
    radius: 8,
    color: "#ffffff",
    weight: 3,
    fillColor: "#090b0d",
    fillOpacity: 0.95,
  }).addTo(map);
  renderLocationBriefing();
  schedule3dRender();
}

function selectBriefingLocation(latitude, longitude) {
  if (state.siteView !== "forecast" || !state.data) return;
  state.selectedLocation = {
    clickedLatitude: Number(latitude),
    clickedLongitude: Number(longitude),
  };
  updateLocationBriefing();
}

function clearBriefingLocation() {
  state.selectedLocation = null;
  state.briefingText = "";
  if (state.selectedLocationMarker) map.removeLayer(state.selectedLocationMarker);
  state.selectedLocationMarker = null;
  document.getElementById("location-briefing").hidden = true;
  document.getElementById("location-briefing-content").replaceChildren();
  schedule3dRender();
}

function predictorColor(encodedValue) {
  const stops = [
    [0, [35, 59, 139]],
    [350, [60, 180, 197]],
    [700, [240, 223, 115]],
    [1000, [216, 74, 63]],
  ];
  const value = Math.max(0, Math.min(1000, Number(encodedValue) || 0));
  for (let index = 1; index < stops.length; index += 1) {
    if (value > stops[index][0]) continue;
    const [lowValue, lowColor] = stops[index - 1];
    const [highValue, highColor] = stops[index];
    const fraction = (value - lowValue) / (highValue - lowValue);
    const rgb = lowColor.map((channel, channelIndex) => (
      Math.round(channel + (highColor[channelIndex] - channel) * fraction)
    ));
    return `rgb(${rgb.join(",")})`;
  }
  return "rgb(216,74,63)";
}

function mapPath(entry) {
  return entry.map_href || `${horizonRoot()}archive/${entry.date}/map.json`;
}

function showLoading(message = "Loading map data…") {
  const loading = document.getElementById("loading");
  loading.textContent = message;
  loading.hidden = false;
}

function hideLoading() {
  document.getElementById("loading").hidden = true;
}

function formatRadarFrameTime(frame) {
  if (!frame?.time) return "";
  return new Date(frame.time * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function radarTileUrl(frame) {
  return `${state.radarHost}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`;
}

function clearRadarLayer() {
  for (const layer of state.radarLayers) map.removeLayer(layer);
  state.radarLayers = [];
  state.radarLayer = null;
}

function stopRadarLoop() {
  clearInterval(state.radarTimer);
  state.radarTimer = null;
  clearTimeout(state.radarRefreshTimer);
  state.radarRefreshTimer = null;
  clearRadarLayer();
}

function showRadarFrame() {
  if (!state.radarEnabled || state.viewMode !== "2d" || state.radarLayers.length === 0) return;
  const frame = state.radarFrames[state.radarFrameIndex % state.radarFrames.length];
  const nextLayer = state.radarLayers[state.radarFrameIndex % state.radarLayers.length];
  const previousLayer = state.radarLayer;
  nextLayer.bringToFront();
  nextLayer.setOpacity(RADAR_OPACITY);
  state.radarLayer = nextLayer;
  if (previousLayer && previousLayer !== nextLayer) {
    setTimeout(() => {
      if (previousLayer !== state.radarLayer) previousLayer.setOpacity(0);
    }, RADAR_CROSSFADE_MS);
  }
  document.getElementById("radar-status").textContent = `Radar loop: ${formatRadarFrameTime(frame)}.`;
  state.radarFrameIndex = (state.radarFrameIndex + 1) % state.radarFrames.length;
}

function startRadarAnimation() {
  clearInterval(state.radarTimer);
  if (!state.radarLayers.length) return;
  showRadarFrame();
  state.radarTimer = setInterval(showRadarFrame, RADAR_FRAME_MS);
}

function preloadRadarLayers() {
  clearRadarLayer();
  let loaded = 0;
  state.radarLayers = state.radarFrames.map((frame) => {
    const layer = L.tileLayer(radarTileUrl(frame), {
      pane: "radarPane",
      opacity: 0,
      maxZoom: 9,
      maxNativeZoom: 7,
      tileSize: 256,
      className: "radar-frame-layer",
      attribution: "Radar &copy; RainViewer",
    });
    layer.once("load", () => {
      loaded += 1;
      if (loaded === state.radarLayers.length && state.radarEnabled && state.viewMode === "2d") {
        document.getElementById("radar-status").textContent = "Radar loop loaded.";
        startRadarAnimation();
      }
    });
    layer.addTo(map);
    return layer;
  });
}

async function fetchRadarFrames(scheduleRefresh = false) {
  if (!state.radarEnabled || !selectedCaseSupportsLiveLayers()) return;
  const request = ++state.radarRequest;
  const status = document.getElementById("radar-status");
  status.textContent = "Loading current radar loop...";
  try {
    const response = await fetch(`${RADAR_API_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`RainViewer request failed (${response.status})`);
    const payload = await response.json();
    if (request !== state.radarRequest || !state.radarEnabled) return;
    const frames = [
      ...(payload?.radar?.past || []),
      ...(payload?.radar?.nowcast || []),
    ].filter((frame) => (
      frame?.path
      && frame?.time
      && selectedCaseSupportsLiveLayers(state.data?.date, Number(frame.time) * 1000)
    ));
    if (!payload?.host || frames.length === 0) throw new Error("RainViewer returned no radar frames");
    state.radarHost = payload.host;
    state.radarFrames = frames.slice(-12);
    state.radarFrameIndex = 0;
    if (state.viewMode === "2d") preloadRadarLayers();
    else status.textContent = "Radar loop is available in 2D view.";
  } catch (error) {
    if (request !== state.radarRequest) return;
    stopRadarLoop();
    status.textContent = "Radar loop is temporarily unavailable.";
    console.warn(error.message);
  } finally {
    if (scheduleRefresh && state.radarEnabled) {
      clearTimeout(state.radarRefreshTimer);
      state.radarRefreshTimer = setTimeout(() => fetchRadarFrames(true), RADAR_REFRESH_MS);
    }
  }
}

function setRadarEnabled(enabled) {
  if (enabled && !selectedCaseSupportsLiveLayers()) {
    document.getElementById("radar-loop-toggle").checked = false;
    document.getElementById("radar-status").textContent = "Current radar does not match this archived forecast valid period.";
    enabled = false;
  }
  state.radarEnabled = enabled;
  if (!enabled) {
    state.radarRequest += 1;
    stopRadarLoop();
    if (!state.singleRadarEnabled) {
      document.getElementById("radar-status").textContent = "Radar overlay is off.";
    }
    return;
  }
  fetchRadarFrames(true);
}

function clearSingleRadarLayer() {
  for (const layer of state.singleRadarLayers) map.removeLayer(layer);
  state.singleRadarLayers = [];
  state.singleRadarLayer = null;
}

function stopSingleRadarAnimation() {
  clearInterval(state.singleRadarTimer);
  state.singleRadarTimer = null;
}

function stopSingleRadar() {
  stopSingleRadarAnimation();
  clearTimeout(state.singleRadarRefreshTimer);
  state.singleRadarRefreshTimer = null;
  clearSingleRadarLayer();
  state.singleRadarFrames = [];
  state.singleRadarFrameIndex = 0;
  updateSingleRadarPlaybackControls();
}

function clearRadarStationMarkers() {
  if (state.radarStationLayer) map.removeLayer(state.radarStationLayer);
  state.radarStationLayer = null;
}

function nearestRadarStation() {
  const center = map.getCenter();
  return state.singleRadarStations.reduce((nearest, station) => {
    const latitudeDifference = station.lat - center.lat;
    const longitudeDifference = (station.lon - center.lng) * Math.cos(center.lat * Math.PI / 180);
    const distance = latitudeDifference ** 2 + longitudeDifference ** 2;
    return !nearest || distance < nearest.distance ? { station, distance } : nearest;
  }, null)?.station;
}

function populateRadarStationSelect() {
  const select = document.getElementById("radar-station-select");
  select.replaceChildren();
  for (const station of state.singleRadarStations) {
    const option = document.createElement("option");
    option.value = station.id;
    option.textContent = `${station.id} — ${station.name}, ${station.state}`;
    select.append(option);
  }
  const selected = state.singleRadarStations.find((station) => station.id === state.selectedSingleRadar)
    || nearestRadarStation()
    || state.singleRadarStations[0];
  state.selectedSingleRadar = selected?.id || "";
  select.value = state.selectedSingleRadar;
  select.disabled = !state.singleRadarEnabled;
}

function activateSingleRadarStation(stationId) {
  if (!selectedCaseSupportsLiveLayers()) return;
  state.selectedSingleRadar = stationId;
  document.getElementById("radar-station-select").value = stationId;
  document.getElementById("radar-loop-toggle").checked = false;
  document.getElementById("single-radar-toggle").checked = true;
  setRadarEnabled(false);
  setSingleRadarEnabled(true);
}

function renderRadarStationMarkers() {
  clearRadarStationMarkers();
  if (
    state.viewMode !== "2d"
    || !state.singleRadarEnabled
    || !state.singleRadarStations.length
  ) return;
  const markers = state.singleRadarStations.map((station) => {
    const selected = state.singleRadarEnabled && station.id === state.selectedSingleRadar;
    const size = selected ? 16 : 12;
    const icon = L.divIcon({
      className: "radar-site-icon",
      html: `<span class="radar-site-symbol${selected ? " active" : ""}" aria-hidden="true"></span>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
    const marker = L.marker([station.lat, station.lon], {
      pane: "radarStationPane",
      icon,
      bubblingMouseEvents: false,
    });
    marker.bindTooltip(
      `${station.id} — ${station.name}, ${station.state}<br>Click to start the single-site ${SINGLE_RADAR_PRODUCT} loop.`,
      { direction: "top", offset: [0, -4] },
    );
    marker.on("click", () => activateSingleRadarStation(station.id));
    return marker;
  });
  state.radarStationLayer = L.layerGroup(markers).addTo(map);
}

async function fetchRadarStations() {
  if (state.singleRadarStations.length) {
    populateRadarStationSelect();
    renderRadarStationMarkers();
    if (state.singleRadarEnabled) fetchSingleRadarFrames(true);
    return;
  }
  const request = ++state.singleRadarStationRequest;
  const status = document.getElementById("radar-status");
  if (state.singleRadarEnabled) status.textContent = "Loading online NEXRAD stations…";
  try {
    const response = await fetch(`${NEXRAD_STATIONS_URL}&v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`NEXRAD station request failed (${response.status})`);
    const collection = await response.json();
    if (request !== state.singleRadarStationRequest) return;
    state.singleRadarStations = (collection.features || []).map((feature) => {
      const [lon, lat] = feature.geometry?.coordinates || [];
      return {
        id: String(feature.properties?.sid || feature.id || "").toUpperCase(),
        name: feature.properties?.sname || "NEXRAD",
        state: feature.properties?.state || feature.properties?.country || "",
        lat: Number(lat),
        lon: Number(lon),
      };
    }).filter((station) => (
      station.id
      && Number.isFinite(station.lat)
      && Number.isFinite(station.lon)
      && station.lat >= 27
      && station.lat <= 53
      && station.lon >= -109
      && station.lon <= -76
    )).sort((left, right) => (
      left.state.localeCompare(right.state)
      || left.name.localeCompare(right.name)
      || left.id.localeCompare(right.id)
    ));
    if (!state.singleRadarStations.length) throw new Error("No online NEXRAD stations were returned");
    populateRadarStationSelect();
    renderRadarStationMarkers();
    if (state.singleRadarEnabled) fetchSingleRadarFrames(true);
  } catch (error) {
    if (request !== state.singleRadarStationRequest) return;
    state.singleRadarStations = [];
    state.selectedSingleRadar = "";
    document.getElementById("radar-station-select").disabled = true;
    if (state.singleRadarEnabled) {
      status.textContent = "Single-site NEXRAD radar is temporarily unavailable.";
    }
    console.warn(error.message);
  }
}

function singleRadarTimestamp(frame) {
  return String(frame.ts || "").replace(/[-:TZ]/g, "").slice(0, 12);
}

function singleRadarTileUrl(frame) {
  const layer = `ridge::${state.selectedSingleRadar}-${SINGLE_RADAR_PRODUCT}-${singleRadarTimestamp(frame)}`;
  return `${SINGLE_RADAR_TMS_URL}/${layer}/{z}/{x}/{y}.png`;
}

function formatSingleRadarFrameTime(frame) {
  const timestamp = Date.parse(frame?.ts);
  if (!Number.isFinite(timestamp)) return "";
  return new Date(timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function updateSingleRadarPlaybackControls() {
  const button = document.getElementById("single-radar-play-toggle");
  const frameTime = document.getElementById("single-radar-frame-time");
  const available = state.singleRadarEnabled && state.singleRadarFrames.length > 0;
  button.disabled = !available || state.viewMode !== "2d";
  button.textContent = available && state.singleRadarPlaying ? "Pause loop" : "Play loop";
  if (!available) frameTime.textContent = "No single-site loop selected.";
}

function showSingleRadarFrame() {
  if (
    !state.singleRadarEnabled
    || !state.singleRadarPlaying
    || state.viewMode !== "2d"
    || !state.singleRadarLayers.length
  ) return;
  const index = state.singleRadarFrameIndex % state.singleRadarLayers.length;
  const frame = state.singleRadarFrames[index];
  const nextLayer = state.singleRadarLayers[index];
  const previousLayer = state.singleRadarLayer;
  nextLayer.bringToFront();
  nextLayer.setOpacity(SINGLE_RADAR_OPACITY);
  state.singleRadarLayer = nextLayer;
  if (previousLayer && previousLayer !== nextLayer) previousLayer.setOpacity(0);
  document.getElementById("single-radar-frame-time").textContent = (
    `${state.selectedSingleRadar} ${formatSingleRadarFrameTime(frame)}`
  );
  document.getElementById("radar-status").textContent = (
    `${state.selectedSingleRadar} single-site NEXRAD ${SINGLE_RADAR_PRODUCT} loop `
    + `(${state.singleRadarFrames.length} recent scans).`
  );
  state.singleRadarFrameIndex = (index + 1) % state.singleRadarLayers.length;
}

function startSingleRadarAnimation() {
  stopSingleRadarAnimation();
  if (!state.singleRadarLayers.length || !state.singleRadarPlaying) return;
  showSingleRadarFrame();
  state.singleRadarTimer = setInterval(showSingleRadarFrame, SINGLE_RADAR_FRAME_MS);
}

function setSingleRadarPlaying(playing) {
  state.singleRadarPlaying = playing;
  if (playing) startSingleRadarAnimation();
  else stopSingleRadarAnimation();
  updateSingleRadarPlaybackControls();
}

function preloadSingleRadarLayers() {
  clearSingleRadarLayer();
  stopSingleRadarAnimation();
  let loaded = 0;
  state.singleRadarLayers = state.singleRadarFrames.map((frame) => {
    const layer = L.tileLayer(singleRadarTileUrl(frame), {
      pane: "radarPane",
      opacity: 0,
      maxZoom: 12,
      tileSize: 256,
      className: "single-radar-frame-layer",
      attribution: "Single-site NEXRAD &copy; NOAA/NWS via Iowa Environmental Mesonet",
    });
    layer.once("load", () => {
      loaded += 1;
      if (
        loaded === state.singleRadarLayers.length
        && state.singleRadarEnabled
        && state.viewMode === "2d"
      ) {
        startSingleRadarAnimation();
      }
    });
    layer.addTo(map);
    return layer;
  });
  updateSingleRadarPlaybackControls();
}

async function fetchSingleRadarFrames(scheduleRefresh = false) {
  if (!state.singleRadarEnabled || !state.selectedSingleRadar || !selectedCaseSupportsLiveLayers()) return;
  clearTimeout(state.singleRadarRefreshTimer);
  state.singleRadarRefreshTimer = null;
  const request = ++state.singleRadarRequest;
  stopSingleRadarAnimation();
  clearSingleRadarLayer();
  const status = document.getElementById("radar-status");
  status.textContent = `Loading the recent ${state.selectedSingleRadar} ${SINGLE_RADAR_PRODUCT} loop…`;
  const end = new Date();
  const start = new Date(end.getTime() - SINGLE_RADAR_LOOP_MINUTES * 60 * 1000);
  const parameters = new URLSearchParams({
    operation: "list",
    radar: state.selectedSingleRadar,
    product: SINGLE_RADAR_PRODUCT,
    start: `${start.toISOString().slice(0, 16)}Z`,
    end: `${end.toISOString().slice(0, 16)}Z`,
  });
  try {
    const response = await fetch(`${SINGLE_RADAR_SCANS_URL}?${parameters}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`NEXRAD scan request failed (${response.status})`);
    const payload = await response.json();
    if (request !== state.singleRadarRequest || !state.singleRadarEnabled) return;
    state.singleRadarFrames = (payload.scans || [])
      .filter((frame) => (
        Number.isFinite(Date.parse(frame.ts))
        && selectedCaseSupportsLiveLayers(state.data?.date, Date.parse(frame.ts))
      ))
      .slice(-SINGLE_RADAR_MAX_FRAMES);
    if (!state.singleRadarFrames.length) throw new Error("No recent NEXRAD scans were returned");
    state.singleRadarFrameIndex = 0;
    if (state.viewMode === "2d") preloadSingleRadarLayers();
    else status.textContent = "Radar overlays are available in 2D view.";
  } catch (error) {
    if (request !== state.singleRadarRequest) return;
    state.singleRadarFrames = [];
    clearSingleRadarLayer();
    status.textContent = `${state.selectedSingleRadar} single-site radar loop is temporarily unavailable.`;
    console.warn(error.message);
  } finally {
    updateSingleRadarPlaybackControls();
    if (scheduleRefresh && state.singleRadarEnabled) {
      clearTimeout(state.singleRadarRefreshTimer);
      state.singleRadarRefreshTimer = setTimeout(
        () => fetchSingleRadarFrames(true),
        SINGLE_RADAR_REFRESH_MS,
      );
    }
  }
}

function setSingleRadarEnabled(enabled) {
  if (enabled && !selectedCaseSupportsLiveLayers()) {
    document.getElementById("single-radar-toggle").checked = false;
    document.getElementById("radar-status").textContent = "Current radar does not match this archived forecast valid period.";
    enabled = false;
  }
  state.singleRadarEnabled = enabled;
  document.getElementById("radar-station-select").disabled = !enabled || !state.singleRadarStations.length;
  if (!enabled) {
    state.singleRadarRequest += 1;
    stopSingleRadar();
    renderRadarStationMarkers();
    if (!state.radarEnabled) {
      document.getElementById("radar-status").textContent = "Radar overlay is off.";
    }
    return;
  }
  state.singleRadarPlaying = true;
  if (state.singleRadarStations.length) {
    populateRadarStationSelect();
    renderRadarStationMarkers();
    fetchSingleRadarFrames(true);
  } else {
    fetchRadarStations();
  }
}

function setProductMessageExpanded(expanded) {
  const panel = document.querySelector(".product-message");
  const button = document.getElementById("product-message-toggle");
  panel.classList.toggle("expanded", expanded);
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute(
    "aria-label",
    expanded ? "Collapse product description" : "Show full product description",
  );
  button.textContent = expanded ? "Less" : "More";
}

function setMessage(key) {
  const radius = { ml_r40: "40 km (25 mi)", ml_r60: "60 km (37 mi)", ml_r75: "75 km (47 mi)", ml_r100: "100 km (62 mi)" }[key];
  let prediction = "";
  if (radius) prediction = ` It predicts the probability that observed rainfall will exceed Flash Flood Guidance within ${radius} of a point.`;
  if (key === "ml_mean") prediction = " It averages the available ML radius configurations at each grid point.";
  if (key === "wpc") prediction = " It predicts the probability of rainfall exceeding Flash Flood Guidance within 40 km (25 mi) of a point.";
  if (key === "pp") prediction = " It shows an observation-based, idealized placement of risk after the valid period—not a forecast.";
  const display = continuousProbabilityActive()
    ? " The 2D fill uses the continuous raw ML probability scale."
    : "";
  const comparison = comparisonLayerAvailable()
    ? ` The forecast-evolution slider is showing ${comparisonBlendLabel().toLowerCase()} for the same valid period.`
    : "";
  document.getElementById("product-message").textContent = `${PRODUCT_META[key]?.note || ""}${prediction}${display}${comparison}`;
  setProductMessageExpanded(false);
}

function applyForecastFillOpacity() {
  const weights = activeComparisonWeights();
  map.getPane("forecastPane").style.opacity = String(state.fillOpacity * weights.day1);
  map.getPane("forecastPriorPane").style.opacity = String(state.fillOpacity * weights.day2);
}

function add2dForecastFill(group, data, key, continuous, radius, pane, renderer) {
  const values = data?.layers?.[key]?.values;
  const lat = data?.grid?.lat;
  const lon = data?.grid?.lon;
  if (!values || !lat || !lon) return;
  for (let index = 0; index < values.length; index += 1) {
    const encodedValue = Number(values[index]) || 0;
    const color = continuous
      ? (encodedValue > 0 ? continuousRiskCss(encodedValue) : null)
      : riskColor(encodedValue);
    if (!color) continue;
    L.circleMarker([lat[index], lon[index]], {
      pane,
      renderer,
      radius,
      stroke: false,
      fill: true,
      fillColor: color,
      fillOpacity: 1,
      interactive: false,
    }).addTo(group);
  }
}

function renderFilledLayer() {
  if (state.fillLayer) {
    map.removeLayer(state.fillLayer);
    state.fillLayer = null;
  }
  const probabilityLegend = document.getElementById("probability-legend");
  probabilityLegend.hidden = Boolean(state.selectedPredictor);
  updateContinuousProbabilityUI();
  updateForecastComparisonUI();
  if (state.selectedPredictor) return;
  if (!state.data || !state.data.layers[state.selected]) return;
  if (state.viewMode === "3d") {
    setMessage(state.selected);
    schedule3dRender();
    return;
  }

  const group = L.layerGroup();
  const radius = Math.max(2.2, Math.min(4.2, 2.2 + (map.getZoom() - 4) * 0.35));
  const continuous = continuousProbabilityActive();
  add2dForecastFill(
    group,
    state.previousDay2Data,
    state.selected,
    continuous,
    radius,
    "forecastPriorPane",
    priorCanvasRenderer,
  );
  add2dForecastFill(
    group,
    state.data,
    state.selected,
    continuous,
    radius,
    "forecastPane",
    canvasRenderer,
  );

  state.fillLayer = group.addTo(map);
  applyForecastFillOpacity();
  setMessage(state.selected);
}

function renderPredictorLayer() {
  if (state.predictorLayer) {
    map.removeLayer(state.predictorLayer);
    state.predictorLayer = null;
  }
  const legend = document.getElementById("predictor-legend");
  const predictor = state.data?.predictors?.[`r${state.selectedPredictorRadius}`]?.[state.selectedPredictor];
  legend.hidden = !predictor || state.viewMode !== "2d";
  renderFilledLayer();
  renderContours();
  if (!predictor || state.viewMode !== "2d") {
    if (!state.selectedPredictor) setMessage(state.selected);
    return;
  }

  const group = L.layerGroup();
  const radius = Math.max(2.4, Math.min(4.4, 2.4 + (map.getZoom() - 4) * 0.35));
  for (let index = 0; index < predictor.values.length; index += 1) {
    L.circleMarker([state.data.grid.lat[index], state.data.grid.lon[index]], {
      pane: "predictorPane",
      radius,
      stroke: false,
      fill: true,
      fillColor: predictorColor(predictor.values[index]),
      fillOpacity: 0.72,
      interactive: false,
    }).addTo(group);
  }
  state.predictorLayer = group.addTo(map);
  document.getElementById("predictor-legend-title").textContent = predictor.label;
  document.getElementById("predictor-legend-min").textContent = `${predictor.scale_min} ${predictor.units}`;
  document.getElementById("predictor-legend-max").textContent = `${predictor.scale_max} ${predictor.units}`;
  document.getElementById("product-message").textContent = `${state.selectedPredictorRadius}-km model predictor #${predictor.rank}. ${predictor.direction} Mean |SHAP|: ${predictor.mean_abs_shap.toFixed(3)}.`;
  setProductMessageExpanded(false);
}

function renderContours() {
  if (state.viewMode === "3d") {
    schedule3dRender();
    return;
  }
  if (state.contourLayer) map.removeLayer(state.contourLayer);
  state.contourLayer = null;
  if (state.selectedPredictor) return;
  const group = L.layerGroup();

  for (const key of state.contours) {
    const layerContours = state.data?.contours?.[key];
    if (!layerContours) continue;
    for (const threshold of THRESHOLDS) {
      const lines = layerContours[String(threshold)] || [];
      for (const line of lines) {
        L.polyline(line, {
          pane: "contourPane",
          color: "#080a0c",
          weight: key === "pp" ? 6.4 : 5.8,
          opacity: 0.9,
          interactive: false,
        }).addTo(group);
        const polyline = L.polyline(line, {
          pane: "contourPane",
          color: RISK_COLORS[threshold],
          weight: key === "pp" ? 3.8 : 3.3,
          opacity: 1,
          dashArray: PRODUCT_META[key]?.dash,
          interactive: true,
        });
        polyline.bindTooltip(`${PRODUCT_META[key]?.short || key} · >${threshold}%`, {
          sticky: true,
          direction: "top",
        });
        polyline.addTo(group);
      }
    }
  }
  state.contourLayer = group.addTo(map);
}

function renderObservations() {
  if (state.viewMode === "3d") {
    schedule3dRender();
    return;
  }
  if (state.observationLayer) map.removeLayer(state.observationLayer);
  const group = L.layerGroup();
  for (const key of state.observations) {
    const source = state.data?.observations?.[key];
    if (!source) continue;
    const meta = OBSERVATION_META[key] || { label: source.label || key, color: "#fff" };
    for (const point of source.points || []) {
      if (state.showExpansionRings) {
        L.circle(point, {
          pane: "observationPane",
          radius: EXPANSION_RADIUS_METERS,
          color: meta.color,
          weight: 1.5,
          opacity: 0.85,
          fill: true,
          fillColor: meta.color,
          fillOpacity: 0.025,
          interactive: false,
        }).addTo(group);
      }
      L.circleMarker(point, {
        pane: "observationPane",
        radius: 4.2,
        color: "#07090b",
        weight: 1.5,
        fillColor: meta.color,
        fillOpacity: 1,
      }).bindTooltip(meta.label, { direction: "top" }).addTo(group);
    }
  }
  state.observationLayer = group.addTo(map);
}

function lsrPopup(report) {
  const container = document.createElement("div");
  container.className = "lsr-popup";
  const heading = document.createElement("strong");
  const formattedAmount = Number.isFinite(report.amount) ? Number(report.amount.toFixed(2)).toString() : "";
  const amount = report.kind === "rain" && formattedAmount ? ` · ${formattedAmount} in` : "";
  heading.textContent = `${LSR_META[report.kind]?.label || report.type}${amount}`;
  container.append(heading);
  const lines = [
    report.valid ? new Date(report.valid).toLocaleString() : "",
    [report.city, report.county, report.state].filter(Boolean).join(", "),
    report.provider === "mping" ? "Source: mPING citizen report" : (report.source ? `Source: ${report.source}` : ""),
    report.remark || "",
  ].filter(Boolean);
  for (const text of lines) {
    const line = document.createElement("div");
    line.textContent = text;
    container.append(line);
  }
  return container;
}

function renderLsrs() {
  if (state.lsrLayer) map.removeLayer(state.lsrLayer);
  state.lsrLayer = null;
  if (!selectedCaseSupportsLiveLayers()) {
    if (state.viewMode === "3d") schedule3dRender();
    return;
  }
  if (state.viewMode === "3d") {
    schedule3dRender();
    return;
  }
  const threshold = Number(document.getElementById("rain-threshold").value);
  const group = L.layerGroup();
  const reports = state.mpingVisible ? state.lsrReports.concat(state.mpingReports) : state.lsrReports;
  for (const report of reports) {
    if (report.provider !== "mping" && !state.lsrTypes.has(report.kind)) continue;
    if (report.kind === "rain" && (!Number.isFinite(report.amount) || report.amount < threshold)) continue;
    const meta = LSR_META[report.kind];
    if (state.showExpansionRings && report.provider !== "mping") {
      L.circle([report.lat, report.lon], {
        pane: "lsrPane",
        radius: EXPANSION_RADIUS_METERS,
        color: meta.color,
        weight: 1.5,
        opacity: 0.85,
        fill: true,
        fillColor: meta.color,
        fillOpacity: 0.025,
        interactive: false,
      }).addTo(group);
    }
    L.circleMarker([report.lat, report.lon], {
      pane: "lsrPane",
      radius: report.kind === "rain" ? 5 : 6,
      color: "#050607",
      weight: 2,
      fillColor: meta.color,
      fillOpacity: 1,
    }).bindPopup(lsrPopup(report), { maxWidth: 330 }).addTo(group);
  }
  state.lsrLayer = group.addTo(map);
}

function forecastWindow(date) {
  const start = new Date(`${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T12:00:00Z`);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 1);
  return { start, end };
}

function selectedCaseSupportsLiveLayers(date = state.data?.date, now = Date.now()) {
  if (!/^\d{8}$/.test(String(date || ""))) return false;
  const window = forecastWindow(String(date));
  const timestamp = Number(now);
  return Number.isFinite(timestamp)
    && timestamp >= window.start.getTime()
    && timestamp < window.end.getTime();
}

function setLiveSectionAvailability(sectionId, available) {
  document.getElementById(sectionId).classList.toggle("live-layer-unavailable", !available);
}

function updateTemporalLayerAvailability() {
  const available = selectedCaseSupportsLiveLayers();
  state.liveLayersAvailable = available;

  const radarToggle = document.getElementById("radar-loop-toggle");
  const singleRadarToggle = document.getElementById("single-radar-toggle");
  radarToggle.disabled = !available;
  singleRadarToggle.disabled = !available;
  setLiveSectionAvailability("radar-section", available);

  document.querySelectorAll("#lsr-section input, #lsr-section select").forEach((control) => {
    control.disabled = !available;
  });
  setLiveSectionAvailability("lsr-section", available);

  document.querySelectorAll("#flood-alert-section input").forEach((control) => {
    control.disabled = !available;
  });
  setLiveSectionAvailability("flood-alert-section", available);

  if (available) {
    document.getElementById("radar-station-select").disabled = !state.singleRadarEnabled || !state.singleRadarStations.length;
    if (!state.radarEnabled && !state.singleRadarEnabled) {
      document.getElementById("radar-status").textContent = "Radar overlay is off.";
    }
    updateSingleRadarPlaybackControls();
    return true;
  }

  radarToggle.checked = false;
  singleRadarToggle.checked = false;
  setRadarEnabled(false);
  setSingleRadarEnabled(false);
  clearRadarStationMarkers();
  document.getElementById("radar-status").textContent = "Unavailable: current radar scans fall outside this archived forecast valid period.";

  state.lsrRequest += 1;
  clearTimeout(state.lsrTimer);
  state.lsrTimer = null;
  state.lsrReports = [];
  state.lsrAvailability = "historical";
  renderLsrs();
  document.getElementById("lsr-status").textContent = "Unavailable for archived cases. Use Flood proxy observations when verification is available.";

  state.floodAlertRequest += 1;
  clearTimeout(state.floodAlertTimer);
  state.floodAlertTimer = null;
  state.floodAlerts = [];
  state.floodAlertAvailability = "historical";
  renderFloodAlerts();
  document.getElementById("flood-alert-status").textContent = "Unavailable: active NWS alerts apply only to the currently valid case.";
  updateLocationBriefing();
  return false;
}

function parseLsrFeature(feature) {
  const properties = feature?.properties || {};
  const coordinates = feature?.geometry?.coordinates || [];
  const type = String(properties.typetext || "").toUpperCase();
  const kind = type === "FLASH FLOOD" ? "flash_flood"
    : type === "FLOOD" ? "flood"
      : ["RAIN", "HEAVY RAIN"].includes(type) ? "rain" : null;
  const lon = Number(coordinates[0]);
  const lat = Number(coordinates[1]);
  if (!kind || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const rawAmount = properties.magf ?? properties.magnitude;
  const numericAmount = rawAmount === null || rawAmount === "" ? NaN : Number(rawAmount);
  const unit = String(properties.unit || "").toLowerCase();
  const amount = Number.isFinite(numericAmount) && unit.includes("mm") ? numericAmount / 25.4 : numericAmount;
  return {
    kind,
    type,
    lat,
    lon,
    amount: Number.isFinite(amount) ? amount : null,
    valid: properties.valid || "",
    city: properties.city || "",
    county: properties.county || "",
    state: properties.state || properties.st || "",
    source: properties.source || "",
    remark: properties.remark || "",
  };
}

async function fetchLsrs(date, scheduleRefresh = false) {
  if (!selectedCaseSupportsLiveLayers(date)) return;
  const request = ++state.lsrRequest;
  const window = forecastWindow(date);
  const start = window.start.toISOString().slice(0, 16) + "Z";
  const params = new URLSearchParams({
    west: "-105.1", east: "-80.4", south: "30", north: "50.1",
    sts: start, ets: window.end.toISOString().slice(0, 16) + "Z",
  });
  const status = document.getElementById("lsr-status");
  state.lsrReports = [];
  state.lsrAvailability = "loading";
  renderLsrs();
  status.textContent = "Loading NWS local storm reports via Iowa Environmental Mesonet…";
  try {
    const response = await fetch(`https://mesonet.agron.iastate.edu/geojson/lsr.geojson?${params}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`IEM LSR request failed (${response.status})`);
    const data = await response.json();
    if (request !== state.lsrRequest) return;
    state.lsrReports = (data.features || []).map(parseLsrFeature).filter((report) => {
      if (!report) return false;
      if (report.kind !== "rain") return true;
      const valid = Date.parse(report.valid);
      return Number.isFinite(valid) && valid >= window.start.getTime() && valid < window.end.getTime();
    });
    state.lsrAvailability = "available";
    renderLsrs();
    const counts = Object.fromEntries(Object.keys(LSR_META).map((key) => [key, state.lsrReports.filter((report) => report.kind === key).length]));
    status.textContent = `Preliminary: ${counts.flash_flood} flash flood, ${counts.flood} flood, ${counts.rain} rain reports. Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`;
  } catch (error) {
    if (request !== state.lsrRequest) return;
    state.lsrReports = [];
    state.lsrAvailability = "unavailable";
    renderLsrs();
    status.textContent = "Local storm reports are temporarily unavailable.";
    console.error(error);
  }
  updateLocationBriefing();
  clearTimeout(state.lsrTimer);
  if (scheduleRefresh) state.lsrTimer = setTimeout(() => fetchLsrs(date, true), LSR_REFRESH_MS);
}

function floodAlertKind(event) {
  const name = String(event || "");
  if (/Flood Watch$/i.test(name)) return "watch";
  if (/Flood Warning$/i.test(name)) return "warning";
  return null;
}

function renderFloodAlerts() {
  if (state.floodAlertLayer) map.removeLayer(state.floodAlertLayer);
  state.floodAlertLayer = null;
  if (!selectedCaseSupportsLiveLayers()) return;
  const features = state.floodAlerts.filter((feature) => state.floodAlertTypes.has(feature.properties.kind));
  state.floodAlertLayer = L.geoJSON({ type: "FeatureCollection", features }, {
    pane: "floodAlertPane",
    interactive: false,
    style: (feature) => {
      const warning = feature.properties.kind === "warning";
      return {
        color: warning ? "#ff4f4f" : "#f0df35",
        weight: warning ? 2.4 : 2,
        opacity: 0.95,
        dashArray: warning ? null : "7 4",
        fillColor: warning ? "#ff4f4f" : "#f0df35",
        fillOpacity: warning ? 0.10 : 0.06,
      };
    },
  }).addTo(map);
}

async function fetchFloodZoneGeometry(url) {
  if (!state.floodZoneCache.has(url)) {
    state.floodZoneCache.set(url, fetch(url, {
      cache: "force-cache",
      headers: { Accept: "application/geo+json" },
    }).then((response) => {
      if (!response.ok) throw new Error(`NWS zone request failed (${response.status})`);
      return response.json();
    }).then((zone) => zone.geometry || null).catch(() => null));
  }
  return state.floodZoneCache.get(url);
}

async function alertPolygonFeatures(alert) {
  const properties = alert?.properties || {};
  const kind = floodAlertKind(properties.event);
  if (!kind) return [];
  const shared = {
    kind,
    event: properties.event || "",
    headline: properties.headline || "",
    areaDesc: properties.areaDesc || "",
    expires: properties.expires || "",
  };
  if (alert.geometry) return [{ type: "Feature", geometry: alert.geometry, properties: shared }];
  const zones = await Promise.all((properties.affectedZones || []).map(fetchFloodZoneGeometry));
  return zones.filter(Boolean).map((geometry) => ({ type: "Feature", geometry, properties: shared }));
}

async function fetchFloodAlerts(scheduleRefresh = false) {
  if (!selectedCaseSupportsLiveLayers()) return;
  const request = ++state.floodAlertRequest;
  const status = document.getElementById("flood-alert-status");
  state.floodAlertAvailability = "loading";
  status.textContent = "Loading active NWS flood alerts…";
  try {
    const response = await fetch(NWS_ALERTS_URL, {
      cache: "no-store",
      headers: { Accept: "application/geo+json" },
    });
    if (!response.ok) throw new Error(`NWS alerts request failed (${response.status})`);
    const collection = await response.json();
    const floodAlerts = (collection.features || []).filter((feature) => floodAlertKind(feature.properties?.event));
    const groups = await Promise.all(floodAlerts.map(alertPolygonFeatures));
    if (request !== state.floodAlertRequest) return;
    state.floodAlerts = groups.flat();
    state.floodAlertAvailability = "available";
    renderFloodAlerts();
    const watchCount = floodAlerts.filter((feature) => floodAlertKind(feature.properties?.event) === "watch").length;
    const warningCount = floodAlerts.length - watchCount;
    const missing = groups.filter((features) => features.length === 0).length;
    status.textContent = `${watchCount} active flood watch${watchCount === 1 ? "" : "es"}, ${warningCount} warning${warningCount === 1 ? "" : "s"}.${missing > 0 ? ` ${missing} alert${missing === 1 ? "" : "s"} had no polygon.` : ""}`;
  } catch (error) {
    if (request !== state.floodAlertRequest) return;
    state.floodAlerts = [];
    state.floodAlertAvailability = "unavailable";
    renderFloodAlerts();
    status.textContent = "NWS flood alerts are temporarily unavailable.";
    console.error(error);
  }
  updateLocationBriefing();
  clearTimeout(state.floodAlertTimer);
  if (scheduleRefresh) state.floodAlertTimer = setTimeout(() => fetchFloodAlerts(true), FLOOD_ALERT_REFRESH_MS);
}

function showProductInfo(key) {
  const meta = PRODUCT_META[key];
  if (!meta) return;
  document.getElementById("product-dialog-title").textContent = meta.title;
  document.getElementById("product-dialog-content").innerHTML = `<p class="lead">${meta.note}</p><p>${meta.detail}</p>`;
  document.getElementById("product-dialog").showModal();
}

function buildLayerControls() {
  const productContainer = document.getElementById("product-options");
  const contourContainer = document.getElementById("contour-options");
  const predictorContainer = document.getElementById("predictor-options");
  productContainer.replaceChildren();
  contourContainer.replaceChildren();
  predictorContainer.replaceChildren();

  for (const key of PRODUCT_ORDER) {
    const available = Boolean(state.data?.layers?.[key]);
    const meta = PRODUCT_META[key];

    const productRow = document.createElement("div");
    productRow.className = "product-row";
    const productButton = document.createElement("button");
    productButton.type = "button";
    productButton.className = `product-choice${state.selected === key ? " active" : ""}`;
    productButton.textContent = meta.short;
    productButton.disabled = !available;
    productButton.addEventListener("click", () => {
      state.selected = key;
      buildLayerControls();
      renderFilledLayer();
      renderLocationBriefing();
    });
    const infoButton = document.createElement("button");
    infoButton.type = "button";
    infoButton.className = "info-mini";
    infoButton.textContent = "i";
    infoButton.setAttribute("aria-label", `About ${meta.title}`);
    infoButton.addEventListener("click", () => showProductInfo(key));
    productRow.append(productButton, infoButton);
    productContainer.append(productRow);

    const contourRow = document.createElement("div");
    contourRow.className = "contour-row";
    const contourLabel = document.createElement("label");
    contourLabel.className = "contour-choice";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.contours.has(key);
    checkbox.disabled = !available;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.contours.add(key);
      else state.contours.delete(key);
      renderContours();
    });
    contourLabel.append(checkbox, document.createTextNode(meta.short));
    const contourInfo = infoButton.cloneNode(true);
    contourInfo.addEventListener("click", () => showProductInfo(key));
    contourRow.append(contourLabel, contourInfo);
    contourContainer.append(contourRow);
  }

  const predictors = Object.entries(state.data?.predictors?.[`r${state.selectedPredictorRadius}`] || {})
    .sort(([, left], [, right]) => left.rank - right.rank);
  const predictorStatus = document.getElementById("predictor-status");
  if (!predictors.length) {
    state.selectedPredictor = null;
    predictorStatus.textContent = "Top-predictor fields are unavailable for this archived date.";
  } else {
    const offLabel = document.createElement("label");
    offLabel.className = "predictor-choice";
    const offRadio = document.createElement("input");
    offRadio.type = "radio";
    offRadio.name = "predictor-layer";
    offRadio.checked = !state.selectedPredictor;
    offRadio.addEventListener("change", () => {
      state.selectedPredictor = null;
      renderPredictorLayer();
    });
    const offText = document.createElement("span");
    offText.textContent = "No predictor overlay";
    offLabel.append(offRadio, offText);
    predictorContainer.append(offLabel);

    for (const [key, predictor] of predictors) {
      const label = document.createElement("label");
      label.className = "predictor-choice";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "predictor-layer";
      radio.checked = state.selectedPredictor === key;
      radio.addEventListener("change", () => {
        state.selectedPredictor = key;
        if (state.viewMode !== "2d") setViewMode("2d");
        renderPredictorLayer();
      });
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = `#${predictor.rank} ${predictor.label}`;
      const details = document.createElement("small");
      details.textContent = `Mean |SHAP| ${predictor.mean_abs_shap.toFixed(3)} · ${predictor.relative_importance_percent}% of #1. ${predictor.direction}`;
      text.append(title, details);
      label.append(radio, text);
      predictorContainer.append(label);
    }
    predictorStatus.textContent = `Global importance and direction are from 50,000 sampled 2024–2025 test-grid points for the v33 ${state.selectedPredictorRadius}-km model.`;
  }

  const observationSection = document.getElementById("observation-section");
  const observationContainer = document.getElementById("observation-options");
  observationContainer.replaceChildren();
  const availableObservations = Object.entries(state.data?.observations || {}).filter(([, source]) => source?.points?.length);
  observationSection.hidden = availableObservations.length === 0;
  for (const [key, source] of availableObservations) {
    const meta = OBSERVATION_META[key] || { label: source.label || key, color: "#fff" };
    const label = document.createElement("label");
    label.className = "observation-choice";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.observations.has(key);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.observations.add(key);
      else state.observations.delete(key);
      renderObservations();
    });
    const swatch = document.createElement("i");
    swatch.style.setProperty("--observation-color", meta.color);
    label.append(checkbox, swatch, document.createTextNode(`${meta.label} (${source.points.length})`));
    observationContainer.append(label);
  }

  const hasVerification = Boolean(state.data?.layers?.pp);
  document.getElementById("verification-availability").textContent = hasVerification
    ? "Practically Perfect verification is available for this valid period."
    : "Practically Perfect verification typically arrives around 11:10 AM CT the following day.";
}

function updateDateUI(entry) {
  document.getElementById("valid-period").textContent = `Valid ${state.data.valid_period_label}`;
  const pngLink = document.getElementById("current-png-link");
  if (entry?.plot_available === false) {
    pngLink.hidden = true;
    pngLink.removeAttribute("href");
  } else {
    const staticHref = entry?.plot_href || `${horizonRoot()}archive/${state.data.date}/latest.png`;
    pngLink.href = `${staticHref}?v=${encodeURIComponent(entry?.site_updated_utc || state.data.generated_utc)}`;
    pngLink.hidden = false;
  }
  const verificationLink = document.getElementById("current-verification-link");
  if (entry?.verification_available && entry.verification_plot_href) {
    verificationLink.href = `${entry.verification_plot_href}?v=${encodeURIComponent(entry.verification_updated_utc || entry.site_updated_utc || state.data.generated_utc)}`;
    verificationLink.textContent = entry.verification_embedded_in_forecast ? "Combined PNG" : "Verification PNG";
    verificationLink.hidden = false;
  } else {
    verificationLink.hidden = true;
    verificationLink.removeAttribute("href");
  }
  document.getElementById("date-select").value = state.data.date;
}

async function loadDate(date, fit = false) {
  const entry = state.archive.find((item) => String(item.date) === String(date));
  if (entry?.mcs_eligible === false) {
    document.getElementById("product-message").textContent =
      `${date} is excluded from maps and verification: ${entry.mcs_classification_label || "Non-MCS-associated precipitation"}.`;
    return;
  }
  state.comparisonRequest += 1;
  state.previousDay2Data = null;
  state.previousDay2Entry = null;
  state.comparisonBlend = 100;
  state.comparisonStatus = state.forecastDay === 1 ? "checking" : "unavailable";
  updateForecastComparisonUI();
  showLoading(`Loading ${date}…`);
  try {
    const mapVersion = `${entry?.map_updated_utc || entry?.site_updated_utc || Date.now()}-${MAP_DATA_VERSION}`;
    const response = await fetch(`${mapPath(entry || { date })}?v=${encodeURIComponent(mapVersion)}`);
    if (!response.ok) throw new Error(`Map data unavailable (${response.status})`);
    state.data = await response.json();
    state.surface3dCache.clear();
    if (!state.data.layers[state.selected]) {
      state.selected = state.data.layers.ml_r60
        ? "ml_r60"
        : Object.keys(state.data.layers)[0];
    }
    state.contours = new Set([...state.contours].filter((key) => state.data.layers[key]));
    state.observations = new Set([...state.observations].filter((key) => state.data.observations?.[key]));
    if (state.selectedPredictor && !state.data.predictors?.[`r${state.selectedPredictorRadius}`]?.[state.selectedPredictor]) {
      state.selectedPredictor = null;
    }
    await loadPreviousDay2Forecast();
    buildLayerControls();
    renderFilledLayer();
    renderPredictorLayer();
    renderContours();
    renderObservations();
    renderForecastDomain();
    updateDateUI(entry);
    const liveLayersAvailable = updateTemporalLayerAvailability();
    if (liveLayersAvailable) {
      clearTimeout(state.lsrTimer);
      fetchLsrs(state.data.date, true);
      fetchFloodAlerts(true);
    }
    if (fit) map.fitBounds([[30, -105], [50, -80.5]], { padding: [15, 15] });
    updateLocationBriefing();
    updateUrl();
  } catch (error) {
    document.getElementById("product-message").textContent = `Interactive data are unavailable for ${date}. Use the PNG link or archive.`;
    console.error(error);
  } finally {
    hideLoading();
  }
}

function populateDates() {
  const select = document.getElementById("date-select");
  select.replaceChildren();
  for (const entry of state.archive) {
    const option = document.createElement("option");
    option.value = entry.date;
    const forecastDate = String(state.forecastDay === 2 && entry.issue_date ? entry.issue_date : entry.date).replace(/-/g, "");
    const dateLabel = `${forecastDate.slice(0, 4)}-${forecastDate.slice(4, 6)}-${forecastDate.slice(6, 8)}`;
    option.textContent = entry.mcs_eligible === false
      ? `${dateLabel} — Non-MCS-associated precipitation`
      : dateLabel;
    option.disabled = entry.map_available === false || entry.mcs_eligible === false;
    select.append(option);
  }
  select.onchange = () => loadDate(select.value);
}

function populateArchive() {
  const rows = document.getElementById("archive-rows");
  rows.replaceChildren();
  for (const entry of state.archive) {
    const row = document.createElement("tr");
    const excludedNonMcs = entry.mcs_eligible === false;
    if (excludedNonMcs) row.className = "non-mcs-archive-row";
    const dateCell = document.createElement("td");
    const validCell = document.createElement("td");
    const mapCell = document.createElement("td");
    const staticCell = document.createElement("td");
    const verificationCell = document.createElement("td");
    dateCell.textContent = entry.date;
    if (excludedNonMcs) {
      const label = document.createElement("span");
      label.className = "non-mcs-label";
      label.textContent = entry.mcs_classification_label || "Non-MCS-associated precipitation";
      dateCell.append(label);
    }
    validCell.textContent = entry.valid_period_label || "—";

    const loadButton = document.createElement("button");
    loadButton.type = "button";
    loadButton.className = "archive-load";
    loadButton.textContent = excludedNonMcs
      ? "Excluded"
      : entry.map_available === false ? "Unavailable" : "Load map";
    loadButton.disabled = entry.map_available === false || excludedNonMcs;
    loadButton.addEventListener("click", () => {
      document.getElementById("archive-dialog").close();
      loadDate(entry.date);
    });
    mapCell.append(loadButton);

    if (entry.plot_href && !excludedNonMcs) {
      const link = document.createElement("a");
      link.href = entry.plot_href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Open PNG";
      staticCell.append(link);
    } else {
      staticCell.textContent = "—";
    }

    if (entry.verification_available && entry.verification_plot_href && !excludedNonMcs) {
      const link = document.createElement("a");
      link.href = entry.verification_plot_href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = entry.verification_embedded_in_forecast ? "Combined PNG" : "Open PNG";
      link.title = entry.verification_embedded_in_forecast
        ? "Forecast and Practically Perfect verification in one image"
        : "Open Practically Perfect verification image";
      verificationCell.append(link);
    } else if (entry.verification_embedded_in_map && !excludedNonMcs) {
      verificationCell.textContent = "On interactive map";
    } else {
      verificationCell.textContent = excludedNonMcs ? "Excluded from skill" : "Pending";
      verificationCell.className = "pending-cell";
    }
    row.append(dateCell, validCell, mapCell, staticCell, verificationCell);
    rows.append(row);
  }
}

function metricValueText(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value))
    ? Number(value).toFixed(3)
    : "Not available";
}

function bestRowsForMetric(rows, metric) {
  const meta = METRIC_META[metric] || { optimum: "max" };
  const candidates = rows.map((row) => {
    const rawValue = row.values?.[metric] ?? row[metric];
    if (rawValue === null || rawValue === undefined || rawValue === "") return null;
    const value = Number(rawValue);
    return Number.isFinite(value) ? { row, value } : null;
  }).filter(Boolean);
  if (!candidates.length) return [];
  const score = (candidate) => {
    if (meta.optimum === "min") return -candidate.value;
    if (meta.optimum === "one") return -Math.abs(candidate.value - 1);
    return candidate.value;
  };
  const bestScore = Math.max(...candidates.map(score));
  return candidates
    .filter((candidate) => Math.abs(score(candidate) - bestScore) < 1e-10)
    .map((candidate) => candidate.row);
}

function renderSkillOccurrence() {
  const chart = document.getElementById("skill-occurrence-chart");
  const table = document.getElementById("skill-occurrence-table");
  const answer = document.getElementById("skill-occurrence-answer");
  chart.replaceChildren();
  table.replaceChildren();
  const threshold = document.getElementById("skill-occurrence-threshold").value;
  const metric = document.getElementById("skill-occurrence-metric").value;
  const products = state.riskOccurrence?.products || {};
  const rows = Object.entries(products)
    .map(([label, values]) => ({ label, ...(values[threshold] || {}) }))
    .filter((row) => Number.isInteger(row.hit_day_count)
      && Number.isInteger(row.miss_day_count)
      && Number.isInteger(row.false_alarm_day_count)
      && Number.isInteger(row.correct_negative_day_count));
  if (!rows.length) {
    chart.textContent = "Final day-level risk-occurrence counts are not available.";
    answer.textContent = "";
    return;
  }
  const bestRows = bestRowsForMetric(rows, metric);
  const maximum = Math.max(
    ...rows.flatMap((row) => [
      row.hit_day_count,
      row.miss_day_count,
      row.false_alarm_day_count,
      row.correct_negative_day_count,
    ]),
    1,
  );
  for (const row of rows) {
    const isBest = bestRows.includes(row);
    const group = document.createElement("div");
    group.className = `bar-group${isBest ? " best-performing" : ""}`;
    const label = document.createElement("strong");
    label.textContent = isBest
      ? `${row.label} · Best ${metric.toUpperCase()}`
      : row.label;
    const hits = document.createElement("div");
    hits.className = "bar-row hits";
    hits.innerHTML = `<span>Hits</span><i style="--bar-width:${row.hit_day_count / maximum * 100}%"></i><b>${row.hit_day_count}</b>`;
    const misses = document.createElement("div");
    misses.className = "bar-row misses";
    misses.innerHTML = `<span>Misses</span><i style="--bar-width:${row.miss_day_count / maximum * 100}%"></i><b>${row.miss_day_count}</b>`;
    const falseAlarms = document.createElement("div");
    falseAlarms.className = "bar-row false-alarms";
    falseAlarms.innerHTML = `<span>False alarms</span><i style="--bar-width:${row.false_alarm_day_count / maximum * 100}%"></i><b>${row.false_alarm_day_count}</b>`;
    const correctNegatives = document.createElement("div");
    correctNegatives.className = "bar-row correct-negatives";
    correctNegatives.innerHTML = `<span>Correct negatives</span><i style="--bar-width:${row.correct_negative_day_count / maximum * 100}%"></i><b>${row.correct_negative_day_count}</b>`;
    group.append(label, hits, misses, falseAlarms, correctNegatives);
    chart.append(group);
    const tr = document.createElement("tr");
    if (isBest) tr.className = "best-performing";
    for (const cellValue of [
      isBest ? `${row.label} · Best ${metric.toUpperCase()}` : row.label,
      row.hit_day_count,
      row.miss_day_count,
      row.false_alarm_day_count,
      row.correct_negative_day_count,
      metricValueText(row.csi),
      metricValueText(row.ets),
      `${row.forecast_risk_day_count}/${row.verified_day_count}`,
      `${row.pp_risk_day_count}/${row.verified_day_count}`,
    ]) {
      const td = document.createElement("td");
      td.textContent = cellValue;
      tr.append(td);
    }
    table.append(tr);
  }
  const bestValue = bestRows.length ? metricValueText(bestRows[0][metric]) : "Not available";
  const bestLabels = bestRows.map((row) => row.label).join(", ");
  answer.textContent = bestRows.length
    ? `${bestLabels} ${bestRows.length === 1 ? "has" : "tie for"} the highest day-level ${metric.toUpperCase()} (${bestValue}) for ${THRESHOLD_LABELS_CLIENT[threshold]} occurrence across the 45 independent test days. This ranking evaluates whether a risk existed anywhere in the forecast domain on each day; it does not measure the risk area's pixel-by-pixel placement.`
    : `Day-level ${metric.toUpperCase()} is unavailable for this threshold.`;
}

const THRESHOLD_LABELS_CLIENT = {
  5: "Marginal-or-greater",
  15: "Slight-or-greater",
  40: "Moderate-or-greater",
  70: "High",
};

function renderSkillDashboard() {
  const status = document.getElementById("skill-status");
  const container = document.getElementById("skill-figures");
  container.replaceChildren();
  const figures = state.skillManifest?.figures || [];
  if (!figures.length) {
    status.textContent = "Finalized model-skill figures are not available.";
    return;
  }
  status.textContent = `${figures.length} finalized 2024–2025 test-set figures · formal test set only`;
  for (const figure of figures) {
    const card = document.createElement("figure");
    card.className = "dashboard-figure";
    const image = document.createElement("img");
    image.src = horizonAsset(figure.path);
    image.alt = figure.title;
    image.loading = "lazy";
    image.decoding = "async";
    const imageLink = document.createElement("a");
    imageLink.href = horizonAsset(figure.path);
    imageLink.target = "_blank";
    imageLink.rel = "noopener";
    imageLink.className = "full-resolution-image";
    imageLink.title = `Open ${figure.title} at full resolution`;
    imageLink.append(image);
    const caption = document.createElement("figcaption");
    const title = document.createElement("strong");
    title.textContent = figure.title;
    const detail = document.createElement("span");
    const direction = figure.metric === "Brier Score" ? "Lower is better."
      : figure.metric === "ETS" ? "Higher is better."
        : "Frequency and spatial coverage are descriptive, not standalone skill.";
    detail.textContent = `${figure.target} · ${figure.test_period} · ${figure.test_case_count || "Unknown"} test days. ${direction}`;
    const fullResolution = document.createElement("a");
    fullResolution.href = horizonAsset(figure.path);
    fullResolution.target = "_blank";
    fullResolution.rel = "noopener";
    fullResolution.textContent = "Open full-resolution figure";
    caption.append(title, detail, fullResolution);
    card.append(imageLink, caption);
    container.append(card);
  }
  renderSkillOccurrence();
}

async function loadSkillDashboard() {
  if (state.skillManifest && state.riskOccurrence) {
    renderSkillDashboard();
    return;
  }
  try {
    const [manifestResponse, occurrenceResponse] = await Promise.all([
      fetch(`${horizonRoot()}model-skill/manifest.json`),
      fetch(`${horizonRoot()}model-skill/risk-occurrence.json`),
    ]);
    if (!manifestResponse.ok || !occurrenceResponse.ok) throw new Error("Model-skill assets unavailable");
    [state.skillManifest, state.riskOccurrence] = await Promise.all([
      manifestResponse.json(),
      occurrenceResponse.json(),
    ]);
    renderSkillDashboard();
  } catch (error) {
    document.getElementById("skill-status").textContent = "Final model-skill outputs are not available in this build.";
    console.error(error);
  }
}

function renderRunningDashboard() {
  const windowName = document.getElementById("running-window").value;
  const referenceName = document.getElementById("running-reference").value;
  const metric = document.getElementById("running-metric").value;
  const threshold = document.getElementById("running-threshold").value;
  const windowData = state.runningVerification?.windows?.[windowName];
  const status = document.getElementById("running-status");
  const summary = document.getElementById("running-summary");
  const caseChart = document.getElementById("running-risk-cases-chart");
  const chart = document.getElementById("running-chart");
  const table = document.getElementById("running-table");
  const warning = document.getElementById("running-warning");
  const download = document.getElementById("running-json-download");
  chart.replaceChildren();
  caseChart.replaceChildren();
  table.replaceChildren();
  summary.replaceChildren();
  download.href = `${horizonRoot()}verification/rolling/${windowName}.json`;
  if (!windowData) {
    status.textContent = "No completed issued-forecast verification is available for this window.";
    warning.hidden = true;
    return;
  }
  const referenceData = windowData.references?.[referenceName]
    || windowData.references?.[windowData.default_reference]
    || windowData;
  const referenceLabel = referenceData.label || windowData.verification_target;
  status.textContent = `${windowData.definition} · Reference: ${referenceLabel}`;
  document.getElementById("running-truth-days-heading").textContent = "Reference risk days";
  for (const [label, value] of [
    ["Verified forecasts", windowData.verified_forecast_count],
    ["Date range", `${windowData.start_date}–${windowData.end_date}`],
    ["Missing days", windowData.missing_day_count],
    ["Completeness", `${windowData.completeness_percent}%`],
  ]) {
    const item = document.createElement("div");
    item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    summary.append(item);
  }
  warning.hidden = windowData.verified_forecast_count >= 10;
  warning.textContent = `Only ${windowData.verified_forecast_count} verified forecasts are available in this window. Interpret this running statistic cautiously.`;
  const rows = Object.entries(referenceData.products || {}).map(([key, thresholds]) => ({
    key,
    label: PRODUCT_META[key]?.short || key,
    values: thresholds[threshold],
  })).filter((row) => row.values);
  const bestRows = bestRowsForMetric(rows, metric);
  const maximumCases = Math.max(
    ...rows.map((row) => Number(row.values.verified_forecast_count) || 0),
    1,
  );
  const caseHeading = document.createElement("div");
  caseHeading.className = "chart-heading";
  caseHeading.textContent = `Cases issuing ${THRESHOLD_LABELS_CLIENT[threshold]}`;
  caseChart.append(caseHeading);
  for (const row of rows) {
    const isBest = bestRows.includes(row);
    const riskCases = Number(row.values.risk_case_count) || 0;
    const verifiedCases = Number(row.values.verified_forecast_count) || 0;
    const bar = document.createElement("div");
    bar.className = `metric-bar-row risk-case-row${isBest ? " best-performing" : ""}`;
    bar.innerHTML = `<span>${row.label}</span><i style="--bar-width:${Math.min(100, riskCases / maximumCases * 100)}%"></i><b>${riskCases}/${verifiedCases}</b>`;
    caseChart.append(bar);
  }
  const finiteValues = rows
    .map((row) => row.values[metric])
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter(Number.isFinite);
  const maximum = Math.max(...finiteValues.map(Math.abs), metric === "frequency_bias" ? 1 : 0.0001);
  const direction = METRIC_META[metric];
  const signedScale = SIGNED_METRICS.has(metric);
  const heading = document.createElement("div");
  heading.className = "chart-heading";
  heading.textContent = `${direction.label} · ${THRESHOLD_LABELS_CLIENT[threshold]} · ${direction.direction}${signedScale ? " · Zero centered" : ""}`;
  chart.append(heading);
  for (const row of rows) {
    const isBest = bestRows.includes(row);
    const rawValue = row.values[metric];
    const value = rawValue === null || rawValue === undefined || rawValue === ""
      ? Number.NaN
      : Number(rawValue);
    const bar = document.createElement("div");
    bar.className = `metric-bar-row${isBest ? " best-performing" : ""}`;
    const width = Number.isFinite(value) ? Math.min(100, Math.abs(value) / maximum * 100) : 0;
    const trackClass = signedScale
      ? `signed-metric ${value < 0 ? "signed-negative" : "signed-positive"}`
      : "";
    const trackStyle = signedScale
      ? `--bar-half-width:${width / 2}%`
      : `--bar-width:${width}%`;
    const bestSuffix = isBest ? ` · Best ${direction.label}` : "";
    bar.innerHTML = `<span>${row.label}${bestSuffix}</span><i class="${trackClass}" style="${trackStyle}"></i><b>${metricValueText(value)}</b>`;
    chart.append(bar);
    const tr = document.createElement("tr");
    if (isBest) tr.className = "best-performing";
    for (const cellValue of [
      isBest ? `${row.label} · Best ${direction.label}` : row.label,
      metricValueText(value),
      row.values.risk_case_count,
      row.values.reference_risk_case_count ?? row.values.truth_risk_case_count,
      row.values.verified_forecast_count,
      row.values.risk_occurrence_hits,
      row.values.risk_occurrence_misses,
      row.values.risk_occurrence_false_alarms,
      row.values.risk_occurrence_correct_negatives,
      metricValueText(row.values.risk_occurrence_csi),
      metricValueText(row.values.risk_occurrence_ets),
    ]) {
      const td = document.createElement("td");
      td.textContent = cellValue ?? "—";
      tr.append(td);
    }
    table.append(tr);
  }
}

async function loadRunningDashboard() {
  if (state.runningVerification) {
    renderRunningDashboard();
    return;
  }
  try {
    const response = await fetch(
      `${horizonRoot()}verification/rolling/latest.json?v=${Date.now()}`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error("Running verification unavailable");
    state.runningVerification = await response.json();
    renderRunningDashboard();
  } catch (error) {
    document.getElementById("running-status").textContent = "Running verification is not available yet; formal test-set cases have not been substituted.";
    console.error(error);
  }
}

function renderExplainabilityDashboard() {
  const model = document.getElementById("shap-model").value;
  const kind = document.getElementById("shap-kind").value;
  const figures = (state.explainabilityManifest?.figures || [])
    .filter((figure) => figure.model === model && figure.kind === kind);
  const status = document.getElementById("explainability-status");
  const figure = document.getElementById("shap-figure");
  if (!figures.length) {
    figure.hidden = true;
    status.textContent = kind === "dependence"
      ? `Final pre-rendered dependence plots are not available for ${model}; r100 dependence panels are available.`
      : `Final ${kind} output is not available for ${model}.`;
    return;
  }
  const selected = figures[0];
  const image = document.getElementById("shap-image");
  image.src = horizonAsset(selected.path);
  image.alt = selected.title;
  document.getElementById("shap-caption").textContent = `${selected.title} · independent ${selected.test_period} test set`;
  figure.hidden = false;
  status.textContent = figures.length > 1
    ? `${figures.length} finalized ${kind} figures are indexed; displaying ${selected.title}.`
    : `Finalized ${kind} figure · ${selected.test_period} test set`;
}

async function loadExplainabilityDashboard() {
  if (state.explainabilityManifest) {
    renderExplainabilityDashboard();
    return;
  }
  try {
    const response = await fetch(`${horizonRoot()}explainability/manifest.json`);
    if (!response.ok) throw new Error("Explainability manifest unavailable");
    state.explainabilityManifest = await response.json();
    renderExplainabilityDashboard();
  } catch (error) {
    document.getElementById("explainability-status").textContent = "Finalized SHAP figures are not available in this build.";
    console.error(error);
  }
}

function updateProductNavHighlight() {
  const nav = document.querySelector(".product-nav");
  const active = nav?.querySelector('a[aria-current="page"]');
  if (!nav || !active) return;
  const navBounds = nav.getBoundingClientRect();
  const activeBounds = active.getBoundingClientRect();
  const activeLeft = activeBounds.left - navBounds.left + nav.scrollLeft;
  nav.style.setProperty("--tab-x", `${activeLeft}px`);
  nav.style.setProperty("--tab-width", `${activeBounds.width}px`);
  nav.classList.add("indicator-ready");
  const leftEdge = activeLeft;
  const rightEdge = leftEdge + activeBounds.width;
  if (leftEdge < nav.scrollLeft) {
    nav.scrollTo({ left: leftEdge - 8, behavior: "auto" });
  } else if (rightEdge > nav.scrollLeft + nav.clientWidth) {
    nav.scrollTo({ left: rightEdge - nav.clientWidth + 8, behavior: "auto" });
  }
}

function siteViewDirection(previous, next) {
  const previousIndex = Math.max(0, SITE_VIEW_ORDER.indexOf(previous));
  const nextIndex = Math.max(0, SITE_VIEW_ORDER.indexOf(next));
  return nextIndex < previousIndex ? "backward" : "forward";
}

function applySiteView(normalized, updateHistory) {
  state.siteView = normalized;
  const forecast = normalized === "forecast";
  document.body.classList.toggle("dashboard-active", !forecast);
  document.getElementById("dashboard").hidden = forecast;
  document.getElementById("location-briefing").hidden = !forecast || !state.selectedLocation;
  document.querySelectorAll("[data-dashboard-view]").forEach((section) => {
    section.hidden = section.dataset.dashboardView !== normalized;
  });
  document.querySelectorAll("[data-site-view]").forEach((link) => {
    const isPrimaryCreatorParent = normalized === "creator"
      && link.dataset.siteView === "about"
      && link.closest(".product-nav");
    if (link.dataset.siteView === normalized || isPrimaryCreatorParent) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (forecast) {
    if (state.viewMode === "2d") {
      document.getElementById("map").hidden = false;
      map.invalidateSize();
    } else {
      document.getElementById("map-3d").hidden = false;
      state.map3d?.resize();
    }
  } else if (normalized === "skill") {
    loadSkillDashboard();
  } else if (normalized === "running") {
    loadRunningDashboard();
  } else if (normalized === "explainability") {
    loadExplainabilityDashboard();
  }
  requestAnimationFrame(updateProductNavHighlight);
  if (updateHistory) updateUrl("push");
}

function setSiteView(view, updateHistory = true) {
  const normalized = SITE_VIEWS.has(view) ? view : "forecast";
  const previous = state.siteView;
  if (previous === normalized) {
    applySiteView(normalized, updateHistory);
    return;
  }
  const direction = siteViewDirection(previous, normalized);
  const apply = () => applySiteView(normalized, updateHistory);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (document.startViewTransition && !reduceMotion) {
    state.viewTransition?.skipTransition?.();
    document.documentElement.dataset.tabDirection = direction;
    state.viewTransition = document.startViewTransition(apply);
    state.viewTransition.finished.finally(() => {
      state.viewTransition = null;
      delete document.documentElement.dataset.tabDirection;
    });
    return;
  }
  apply();
  const className = `tab-transition-${direction}`;
  document.body.classList.remove("tab-transition-forward", "tab-transition-backward");
  void document.body.offsetWidth;
  document.body.classList.add(className);
  clearTimeout(state.tabTransitionTimer);
  state.tabTransitionTimer = setTimeout(() => document.body.classList.remove(className), 360);
}

function setupDialogs() {
  document.getElementById("about-button").addEventListener("click", () => document.getElementById("about-dialog").showModal());
  document.getElementById("archive-button").addEventListener("click", () => document.getElementById("archive-dialog").showModal());
  document.querySelectorAll(".dialog-close").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  try {
    if (!localStorage.getItem("ml-flood-map-intro-seen")) {
      document.getElementById("about-dialog").showModal();
      localStorage.setItem("ml-flood-map-intro-seen", "1");
    }
  } catch (_) {
    document.getElementById("about-dialog").showModal();
  }
}

function setupResponsiveControls() {
  const mobile = window.matchMedia("(max-width: 900px)");
  const layerContent = document.getElementById("layer-panel-content");
  const layerToggle = document.getElementById("collapse-layers");
  const actions = document.querySelector(".top-actions");
  const actionsToggle = document.getElementById("mobile-actions-toggle");

  if (mobile.matches) {
    layerContent.hidden = true;
    layerToggle.textContent = "+";
    layerToggle.setAttribute("aria-label", "Expand layer controls");
  }
  actionsToggle.addEventListener("click", () => {
    const open = actions.classList.toggle("mobile-open");
    actionsToggle.setAttribute("aria-expanded", String(open));
    actionsToggle.textContent = open ? "Close" : "Menu";
    actionsToggle.setAttribute("aria-label", open ? "Close map actions" : "Open map actions");
  });
  actions.querySelectorAll("a, button:not(#mobile-actions-toggle)").forEach((control) => {
    control.addEventListener("click", () => {
      if (!mobile.matches) return;
      actions.classList.remove("mobile-open");
      actionsToggle.setAttribute("aria-expanded", "false");
      actionsToggle.textContent = "Menu";
    });
  });
}

document.querySelectorAll("[data-site-view]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    setSiteView(link.dataset.siteView);
  });
});
window.addEventListener("resize", () => requestAnimationFrame(updateProductNavHighlight));
document.fonts?.ready?.then(() => requestAnimationFrame(updateProductNavHighlight));
document.getElementById("clear-location").addEventListener("click", clearBriefingLocation);
document.getElementById("copy-briefing").addEventListener("click", async () => {
  const status = document.getElementById("copy-briefing-status");
  if (!state.briefingText) {
    status.textContent = "Select a valid map location first.";
    return;
  }
  try {
    await navigator.clipboard.writeText(state.briefingText);
    status.textContent = "Briefing copied.";
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = state.briefingText;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    status.textContent = "Briefing copied.";
  }
});
for (const id of ["skill-occurrence-threshold", "skill-occurrence-metric"]) {
  document.getElementById(id).addEventListener("change", renderSkillOccurrence);
}
for (const id of ["running-window", "running-reference", "running-metric", "running-threshold"]) {
  document.getElementById(id).addEventListener("change", renderRunningDashboard);
}
document.getElementById("shap-model").addEventListener("change", renderExplainabilityDashboard);
document.getElementById("shap-kind").addEventListener("change", renderExplainabilityDashboard);

document.getElementById("collapse-layers").addEventListener("click", (event) => {
  const content = document.getElementById("layer-panel-content");
  content.hidden = !content.hidden;
  if (!content.hidden) setProductMessageExpanded(false);
  event.currentTarget.textContent = content.hidden ? "+" : "−";
  event.currentTarget.setAttribute("aria-label", content.hidden ? "Expand layer controls" : "Collapse layer controls");
});

document.getElementById("product-message-toggle").addEventListener("click", (event) => {
  setProductMessageExpanded(event.currentTarget.getAttribute("aria-expanded") !== "true");
});

document.getElementById("view-2d").addEventListener("click", () => setViewMode("2d"));
document.getElementById("view-3d").addEventListener("click", () => setViewMode("3d"));
document.getElementById("point-gap-toggle").addEventListener("change", (event) => {
  state.separated3dPoints = event.currentTarget.checked;
  schedule3dRender();
});
document.getElementById("expansion-ring-toggle").addEventListener("change", (event) => {
  state.showExpansionRings = event.currentTarget.checked;
  if (state.viewMode === "3d") schedule3dRender();
  else {
    renderObservations();
    renderLsrs();
  }
});
document.getElementById("radar-loop-toggle").addEventListener("change", (event) => {
  if (event.currentTarget.checked) {
    document.getElementById("single-radar-toggle").checked = false;
    setSingleRadarEnabled(false);
  }
  setRadarEnabled(event.currentTarget.checked);
});
document.getElementById("single-radar-toggle").addEventListener("change", (event) => {
  if (event.currentTarget.checked) {
    document.getElementById("radar-loop-toggle").checked = false;
    setRadarEnabled(false);
  }
  setSingleRadarEnabled(event.currentTarget.checked);
});
document.getElementById("radar-station-select").addEventListener("change", (event) => {
  state.selectedSingleRadar = event.currentTarget.value;
  state.singleRadarPlaying = true;
  renderRadarStationMarkers();
  if (state.singleRadarEnabled) fetchSingleRadarFrames(true);
});
document.getElementById("single-radar-play-toggle").addEventListener("click", () => {
  setSingleRadarPlaying(!state.singleRadarPlaying);
});
document.getElementById("predictor-radius").addEventListener("change", (event) => {
  state.selectedPredictorRadius = Number(event.currentTarget.value);
  state.selectedPredictor = null;
  buildLayerControls();
  renderPredictorLayer();
  renderLocationBriefing();
});
document.querySelectorAll(".flood-alert-options input").forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.floodAlertTypes.add(checkbox.value);
    else state.floodAlertTypes.delete(checkbox.value);
    renderFloodAlerts();
  });
});

const opacityInput = document.getElementById("fill-opacity");
const opacityOutput = document.getElementById("fill-opacity-value");
opacityInput.addEventListener("input", () => {
  state.fillOpacity = Number(opacityInput.value) / 100;
  opacityOutput.value = `${opacityInput.value}%`;
  if (state.viewMode === "3d") schedule3dRender();
  else applyForecastFillOpacity();
});

document.getElementById("forecast-comparison-slider").addEventListener("input", (event) => {
  state.comparisonBlend = Number(event.currentTarget.value);
  applyForecastFillOpacity();
  updateForecastComparisonUI();
  setMessage(state.selected);
});

document.getElementById("continuous-probability-toggle").addEventListener("change", (event) => {
  state.continuousProbabilities = event.currentTarget.checked;
  renderFilledLayer();
  renderLocationBriefing();
});

document.querySelectorAll(".lsr-options input").forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.lsrTypes.add(checkbox.value);
    else state.lsrTypes.delete(checkbox.value);
    renderLsrs();
  });
});
document.getElementById("rain-threshold").addEventListener("change", renderLsrs);

map.on("zoomend", () => {
  renderFilledLayer();
  renderPredictorLayer();
  renderContours();
  renderObservations();
  renderLsrs();
});
map.on("click", (event) => selectBriefingLocation(event.latlng.lat, event.latlng.lng));
window.addEventListener("popstate", () => {
  const parameters = new URLSearchParams(location.search);
  const requestedDay = Number(parameters.get("day")) === 2 ? 2 : 1;
  if (requestedDay !== state.forecastDay) {
    loadForecastHorizon(requestedDay, parameters.get("date"), false);
  }
  const requestedView = parameters.get("view");
  setSiteView(requestedView === "3d" ? "forecast" : requestedView, false);
  const requestedMap = parameters.get("map") || (requestedView === "3d" ? "3d" : "2d");
  if (requestedMap !== state.viewMode) setViewMode(requestedMap);
});

async function loadForecastHorizon(day, requestedDate = null, updateHistory = true) {
  const nextDay = Number(day) === 2 ? 2 : 1;
  state.forecastDay = nextDay;
  state.comparisonRequest += 1;
  state.previousDay2Data = null;
  state.previousDay2Entry = null;
  state.comparisonBlend = 100;
  state.comparisonStatus = nextDay === 1 ? "checking" : "unavailable";
  document.getElementById("forecast-day-select").value = String(nextDay);
  updateForecastComparisonUI();
  state.skillManifest = null;
  state.riskOccurrence = null;
  state.runningVerification = null;
  state.explainabilityManifest = null;
  if (nextDay === 2) document.getElementById("shap-kind").value = "importance";

  showLoading(`Loading Day ${nextDay} archive…`);
  const response = await fetch(`${horizonRoot()}archive/index.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Day ${nextDay} archive index unavailable`);
  const archive = await response.json();
  state.archive = Array.isArray(archive) ? archive : archive.entries || [];
  if (nextDay === 2) state.day2Archive = state.archive;
  populateDates();
  populateArchive();
  const initial = state.archive.find((entry) => String(entry.date) === String(requestedDate) && entry.map_available !== false && entry.mcs_eligible !== false)
    || state.archive.find((entry) => entry.map_available !== false && entry.mcs_eligible !== false)
    || state.archive[0];
  if (initial) {
    await loadDate(initial.date, true);
  } else {
    state.data = null;
    renderFilledLayer();
    renderContours();
    document.getElementById("valid-period").textContent = `No archived Day ${nextDay} forecast yet`;
    document.getElementById("product-message").textContent = `No Day ${nextDay} real-time forecast has passed the MCS issuance gate yet. Test-set verification and feature importance remain available.`;
    hideLoading();
  }
  if (state.siteView === "skill") await loadSkillDashboard();
  else if (state.siteView === "running") await loadRunningDashboard();
  else if (state.siteView === "explainability") await loadExplainabilityDashboard();
  if (updateHistory) updateUrl("push");
}

async function init() {
  setupDialogs();
  setupResponsiveControls();
  const initialParameters = new URLSearchParams(location.search);
  try {
    const parameters = new URLSearchParams(location.search);
    const requestedDay = Number(parameters.get("day")) === 2 ? 2 : 1;
    await loadForecastHorizon(requestedDay, parameters.get("date"), false);
    const requestedView = parameters.get("view");
    const requestedMap = parameters.get("map") || (requestedView === "3d" ? "3d" : "2d");
    if (requestedMap === "3d") setViewMode("3d");
    setSiteView(requestedView === "3d" ? "forecast" : requestedView, false);
    updateUrl();
    setRadarEnabled(document.getElementById("radar-loop-toggle").checked);
    setSingleRadarEnabled(document.getElementById("single-radar-toggle").checked);
    fetchRadarStations();
  } catch (error) {
    document.getElementById("product-message").textContent = "Forecast data could not be loaded. Please try again shortly.";
    hideLoading();
    console.error(error);
  }
}

document.getElementById("forecast-day-select").addEventListener("change", async (event) => {
  try {
    await loadForecastHorizon(Number(event.currentTarget.value), null, true);
  } catch (error) {
    document.getElementById("product-message").textContent = `Day ${event.currentTarget.value} forecast data are not available yet.`;
    hideLoading();
    console.error(error);
  }
});

init();
