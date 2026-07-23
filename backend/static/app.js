// Small vanilla JS frontend, no build tools needed. Fetches everything
// directly from the FastAPI app on the same origin (see backend/main.py).

if (window.Chart) {
  Chart.defaults.color = "#97a3b5";
  Chart.defaults.borderColor = "rgba(255,255,255,0.08)";
}

// Path the app is served under: "" at the domain root, or e.g. "/pogo" when the
// backend runs with URL_BASE set behind a reverse proxy. Derived from this
// script's own URL (loaded as <script src="app.js"> relative to the page), so
// the frontend needs no server-side templating and works at any mount point.
const APP_BASE = (function () {
  try {
    const src = document.currentScript && document.currentScript.src;
    if (src) {
      return new URL(src, window.location.href).pathname.replace(/\/app\.js(?:\?.*)?$/, "");
    }
  } catch (e) {}
  return "";
})();

function spriteUrl(pokemonId, shiny) {
  return APP_BASE + "/sprites/" + pokemonId + ".png" + (shiny ? "?shiny=true" : "");
}

function ivPercent(atk, def, sta) {
  return Math.round(((atk + def + sta) / 45) * 100);
}

function updateClock() {
  const now = new Date();
  document.getElementById("clock-time").textContent = now.toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: userTimezone,
  });
  document.getElementById("clock-date").textContent = now.toLocaleDateString("en-US", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: userTimezone,
  });
  updateLocationTime();
}

let lastLocationMap = null;
let lastLocationMarker = null;
// Tracks the coordinates currently shown, so a refresh with the same last
// catch doesn't yank the view back to it if the user has panned/zoomed away.
let lastLocationShown = null;
// The IANA timezone of the last catch's coordinates (resolved server-side
// via timezonefinder), so updateClock() can also show what time it
// currently is *there* - separate from and in addition to the user's own
// clock/timezone in Settings. Null when there's no location data yet, or
// the coordinates couldn't be resolved to a timezone.
let lastLocationTimezone = null;

function updateLocationTime() {
  const el = document.getElementById("last-location-time");
  if (!el) return;
  if (!lastLocationTimezone) {
    el.textContent = "";
    return;
  }
  try {
    const timeStr = new Date().toLocaleTimeString("en-US", {
      hour: "2-digit", minute: "2-digit", hour12: false, timeZone: lastLocationTimezone,
    });
    el.textContent = "Local time there: " + timeStr + " (" + lastLocationTimezone.replace(/_/g, " ") + ")";
  } catch (e) {
    // Shouldn't happen (the backend only ever sends valid IANA names or
    // null), but never let a bad timezone string break the clock.
    el.textContent = "";
  }
}

function lastLocationPinIcon() {
  return L.divIcon({
    className: "map-pin-icon",
    html: '<div class="map-pin-dot"></div>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

async function loadLastLocation() {
  const res = await fetch(APP_BASE + "/api/last-location");
  const data = await res.json();
  const mapEl = document.getElementById("last-location-map");
  const empty = document.getElementById("map-empty");
  const caption = document.getElementById("last-location-caption");

  if (!data || data.lat == null || data.lon == null) {
    mapEl.style.display = "none";
    empty.style.display = "flex";
    caption.textContent = "";
    lastLocationTimezone = null;
    updateLocationTime();
    return;
  }

  mapEl.style.display = "block";
  empty.style.display = "none";

  lastLocationTimezone = data.timezone || null;
  updateLocationTime();

  const locationChanged = !lastLocationShown ||
    lastLocationShown.lat !== data.lat || lastLocationShown.lon !== data.lon;

  if (!lastLocationMap) {
    lastLocationMap = L.map(mapEl, { zoomControl: false, attributionControl: true })
      .setView([data.lat, data.lon], 15);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    }).addTo(lastLocationMap);
    lastLocationMarker = L.marker([data.lat, data.lon], { icon: lastLocationPinIcon() }).addTo(lastLocationMap);
    lastLocationShown = { lat: data.lat, lon: data.lon };
    setTimeout(() => lastLocationMap.invalidateSize(), 100);
  } else if (locationChanged) {
    // Only recenter when there's actually a new catch - keep the zoom level
    // the user currently has instead of forcing it back to the default.
    lastLocationMap.setView([data.lat, data.lon], lastLocationMap.getZoom());
    lastLocationMarker.setLatLng([data.lat, data.lon]);
    lastLocationShown = { lat: data.lat, lon: data.lon };
  }
  // If the location hasn't changed, leave the map exactly as the user left it.

  const kindLabel = data.event_type === "raid" ? "Raid catch" : data.event_type === "flee" ? "Flee" : "Catch";
  const targetTab = data.event_type === "raid" ? "raids" : "history";
  caption.innerHTML =
    "<a href='javascript:void(0)' class='map-caption-link' onclick=\"showTab('" + targetTab + "')\">" +
    kindLabel + ": <span class='map-caption-name'>" + data.pokemon_name + "</span>" +
    (data.ts ? " · " + formatHistoryTimestamp(data.ts) : "") +
    "</a>";
}

let heatMap = null;
let heatLayer = null;
// Tracks the last set of points rendered, so an unchanged refresh is a
// no-op (no layer rebuild, no view reset) and a changed one updates the
// heat data in place without touching the user's current pan/zoom.
let lastHeatmapData = null;

// How many days back the heatmap looks - "all" means unbounded. Kept
// separate from chartDays (below): without a limit here the heatmap
// accumulates every catch ever recorded and after a few weeks/months just
// turns into a blob around wherever you're usually active, so this
// defaults to a much shorter window than the "All Time" it used to be.
let heatmapDays = localStorage.getItem("pogostats_heatmap_days") || "30";

async function loadHeatmap(forceRefit) {
  const daysParam = heatmapDays === "all" ? "" : "&days=" + heatmapDays;
  const res = await fetch(APP_BASE + "/api/locations?tz=" + encodeURIComponent(userTimezone) + daysParam);
  const points = await res.json();
  const el = document.getElementById("heatmap");
  const empty = document.getElementById("heatmap-empty");

  if (!points.length) {
    el.style.display = "none";
    empty.style.display = "flex";
    return;
  }

  el.style.display = "block";
  empty.style.display = "none";

  const serialized = JSON.stringify(points);
  const unchanged = serialized === lastHeatmapData && heatMap;
  if (unchanged && !forceRefit) {
    // Nothing new, and nobody explicitly asked for a re-fit - don't touch
    // the layer or the view at all.
    return;
  }
  const isFirstLoad = !heatMap;
  lastHeatmapData = serialized;

  const heatPoints = points.map((p) => [p.lat, p.lon, 0.5]);

  if (!heatMap) {
    heatMap = L.map(el, { zoomControl: false, attributionControl: true });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    }).addTo(heatMap);
  }

  if (heatLayer) {
    // Update the existing layer's data in place instead of removing and
    // re-adding it - avoids a flicker and never touches the map view.
    heatLayer.setLatLngs(heatPoints);
  } else {
    heatLayer = L.heatLayer(heatPoints, { radius: 20, blur: 25, maxZoom: 17 }).addTo(heatMap);
  }

  if (isFirstLoad || forceRefit) {
    // Fit the view to the data on the very first load, and whenever the
    // user explicitly changes the time range (forceRefit) since the extent
    // of the data likely changed a lot - but never on a routine periodic
    // refresh, which must not reset wherever the user has panned/zoomed to.
    const bounds = L.latLngBounds(heatPoints.map((p) => [p[0], p[1]]));
    heatMap.fitBounds(bounds, { padding: [20, 20], maxZoom: 15 });
    if (isFirstLoad) setTimeout(() => heatMap.invalidateSize(), 100);
  }
}

let lineChart, barChart;
let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let currentTab = "dashboard";

// Persisted in the browser only - the trainer name is still stored in the
// database either way, this just controls what's shown in this browser.
let hideTrainerName = localStorage.getItem("pogostats_hide_trainer") === "true";

// How many days back the dashboard/raids charts look, configurable in Settings.
let chartDays = parseInt(localStorage.getItem("pogostats_chart_days") || "30", 10);

// Which timezone "today"/the calendar/the clock are based on. Defaults to
// whatever the browser reports (so it's correct out of the box even if the
// server itself runs in UTC, which is the common case), but can be
// overridden in Settings - e.g. to check stats while traveling.
function detectBrowserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch (e) {
    return "UTC";
  }
}
let userTimezone = localStorage.getItem("pogostats_timezone") || detectBrowserTimezone();

// Notification preferences - all off by default until the user opts in.
let notifyShiny = localStorage.getItem("pogostats_notify_shiny") === "true";
let notifyIv100 = localStorage.getItem("pogostats_notify_iv100") === "true";
let notifyShinyIv100 = localStorage.getItem("pogostats_notify_shiny_iv100") === "true";
let lastNotifiedTs = localStorage.getItem("pogostats_last_notified_ts") || null;

let historyOffset = 0;
const historyLimit = 50;
let historyFilter = "all";
let historyTotal = 0;
// "catches" | "raids" - a sub-tab *within* History, separate from the
// top-level Raids tab (which has its own summary/chart/history). Lets you
// browse raid catches in the same filterable/paginated list as regular
// catches, without mixing the two together.
let historySubTab = "catches";
let historyShinyOnly = false;
let historyIv100Only = false;
// Persisted since it's a display preference, not a filter (matches
// chartDays/userTimezone/hideTrainerName - things you set once and expect
// to stick, vs. filters that reasonably reset per session).
let historyDisplayMode = localStorage.getItem("pogostats_history_display") || "list";
// Every entry loaded so far across "Load More" pages, kept around so
// switching List/Grid re-renders instantly without refetching.
let lastHistoryEntries = [];
// Multi-account filter + Pokemon-name search for the History tab. Not
// persisted - these are per-session filters (like the shiny/100%/type
// filters above), not sticky preferences. Both apply to the Catches and the
// Raids sub-tab of History alike.
let historyTrainer = "";
let historySearch = "";
let historySearchDebounce = null;

function showTab(tab) {
  // Any navigation should land on the tab's normal content, never leave you
  // stuck in a previously-opened species view - this covers the dashboard's
  // last-catch link and notification clicks as well as the tab buttons.
  // showSpeciesDetail re-opens the view immediately after, so it stays in
  // control of its own case.
  if (speciesDetailId != null) resetSpeciesView(false);
  currentTab = tab;
  document.querySelectorAll(".tab-content").forEach((el) => el.classList.remove("visible"));
  document.querySelectorAll(".tab-btn").forEach((el) => el.classList.remove("active"));
  document.getElementById("tab-" + tab).classList.add("visible");
  document.getElementById("btn-" + tab).classList.add("active");
  if (tab === "dashboard") {
    if (lastLocationMap) setTimeout(() => lastLocationMap.invalidateSize(), 50);
    if (heatMap) setTimeout(() => heatMap.invalidateSize(), 50);
  }
  if (tab === "history") {
    // Refresh the account dropdown when the tab is opened (once here, not on
    // the 15s auto-refresh) so newly-seen trainers appear without a reload.
    loadTrainerFilterOptions();
  }
  // Refresh this tab's data every time it's switched to, so newly arrived
  // catches/raids show up without needing a full page reload.
  refreshTab(tab);
}

function refreshTab(tab) {
  if (tab === "dashboard") {
    loadSummary();
    loadTimeseries();
    loadTopSpecies();
    loadLastLocation();
    loadHeatmap();
    loadLastSynced();
  } else if (tab === "calendar") {
    loadCalendar(currentYear, currentMonth);
  } else if (tab === "history") {
    // Never yank the user out of the species view on the periodic refresh -
    // update the species numbers in place instead of rebuilding the list.
    if (speciesDetailId != null) {
      loadSpeciesDetail();
    } else {
      loadHistoryPage(true);
    }
  } else if (tab === "raids") {
    loadRaidSummary();
    loadRaidTopSpecies();
    loadRaidHistoryPage(true);
  } else if (tab === "rolling") {
    loadRollingSummary();
  } else if (tab === "settings") {
    document.getElementById("setting-hide-trainer").checked = hideTrainerName;
    document.getElementById("setting-chart-days").value = String(chartDays);
    document.getElementById("setting-heatmap-days").value = heatmapDays;
    document.getElementById("setting-timezone").value = userTimezone;
    document.getElementById("setting-notify-shiny").checked = notifyShiny;
    document.getElementById("setting-notify-iv100").checked = notifyIv100;
    document.getElementById("setting-notify-shiny-iv100").checked = notifyShinyIv100;
    updateNotificationStatusText();
  }
}

function updateExportLink() {
  document.getElementById("export-csv-link").href = APP_BASE + "/api/export/csv?hide_trainer=" + hideTrainerName;
}

function onHideTrainerChange() {
  hideTrainerName = document.getElementById("setting-hide-trainer").checked;
  localStorage.setItem("pogostats_hide_trainer", String(hideTrainerName));
  updateExportLink();
  // Re-render the already-loaded lists immediately so the change is visible
  // without waiting for the next refresh.
  loadHistoryPage(true);
  loadRaidHistoryPage(true);
}

// "Last 24 Hours" for the 1-day range (matches the heatmap's wording),
// otherwise "Last N Days". Keeps "1 Days" out of the chart titles.
function chartRangeLabel() {
  return chartDays === 1 ? "24 Hours" : chartDays + " Days";
}

function heatmapRangeLabel() {
  if (heatmapDays === "all") return "All Time";
  if (heatmapDays === "1") return "Last 24 Hours";
  return "Last " + heatmapDays + " Days";
}

function updateChartTitles() {
  document.getElementById("line-chart-title").textContent = "Catches - Last " + chartRangeLabel();
  document.getElementById("bar-chart-title").textContent = "Top Pokemon (" + chartRangeLabel() + ")";
  document.getElementById("raid-bar-chart-title").textContent = "Most Common Raid Bosses (" + chartRangeLabel() + ")";
  document.getElementById("heatmap-title").textContent = "Catch Density Heatmap (" + heatmapRangeLabel() + ")";
}

function onChartDaysChange() {
  chartDays = parseInt(document.getElementById("setting-chart-days").value, 10);
  localStorage.setItem("pogostats_chart_days", String(chartDays));
  updateChartTitles();
  loadTimeseries();
  loadTopSpecies();
  loadRaidTopSpecies();
}

function onHeatmapDaysChange() {
  heatmapDays = document.getElementById("setting-heatmap-days").value;
  localStorage.setItem("pogostats_heatmap_days", heatmapDays);
  updateChartTitles();
  // forceRefit=true: the data extent likely changed a lot (e.g. going from
  // 30 days to All Time), so re-fit the view instead of leaving it where it
  // was for the old, narrower range.
  loadHeatmap(true);
}

// A short, curated fallback list for browsers that don't support
// Intl.supportedValuesOf (older Safari/WebKit) - covers the common cases.
// Modern Chrome/Firefox/Edge get the full IANA list instead.
const FALLBACK_TIMEZONES = [
  "UTC", "Europe/Berlin", "Europe/London", "Europe/Paris", "Europe/Madrid",
  "Europe/Rome", "Europe/Amsterdam", "Europe/Warsaw", "Europe/Moscow",
  "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Sao_Paulo", "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata",
  "Asia/Singapore", "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland",
];

function populateTimezoneOptions() {
  const select = document.getElementById("setting-timezone");
  let zones;
  try {
    zones = Intl.supportedValuesOf("timeZone");
  } catch (e) {
    zones = FALLBACK_TIMEZONES;
  }
  // Make sure the currently active timezone is always selectable, even if
  // it's not in the fallback list (e.g. browser-detected but list is short).
  if (!zones.includes(userTimezone)) {
    zones = [userTimezone, ...zones];
  }
  select.innerHTML = zones
    .map((z) => '<option value="' + z + '">' + z.replace(/_/g, " ") + "</option>")
    .join("");
  select.value = userTimezone;
}

function onTimezoneChange() {
  userTimezone = document.getElementById("setting-timezone").value;
  localStorage.setItem("pogostats_timezone", userTimezone);
  updateClock();
  // Timezone affects every day-boundary calculation, so refresh everything
  // that depends on one - not just the currently visible tab.
  loadSummary();
  loadTimeseries();
  loadTopSpecies();
  loadCalendar(currentYear, currentMonth);
  loadRaidSummary();
  loadRaidTopSpecies();
  loadHeatmap(true);
}

async function ensureNotificationPermission() {
  if (!("Notification" in window)) {
    return false;
  }
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
}

function persistNotifySettings() {
  localStorage.setItem("pogostats_notify_shiny", String(notifyShiny));
  localStorage.setItem("pogostats_notify_iv100", String(notifyIv100));
  localStorage.setItem("pogostats_notify_shiny_iv100", String(notifyShinyIv100));
}

function updateNotificationStatusText(customMessage) {
  const el = document.getElementById("notification-status");
  if (!el) return;
  if (customMessage) {
    el.textContent = customMessage;
    return;
  }
  if (!("Notification" in window)) {
    el.textContent = "Your browser doesn't support notifications.";
    return;
  }
  const anyOn = notifyShiny || notifyIv100 || notifyShinyIv100;
  el.textContent = anyOn ? "Notifications are on." : "";
}

async function onNotifySettingChange() {
  const shinyBox = document.getElementById("setting-notify-shiny");
  const iv100Box = document.getElementById("setting-notify-iv100");
  const bothBox = document.getElementById("setting-notify-shiny-iv100");
  const anyChecked = shinyBox.checked || iv100Box.checked || bothBox.checked;

  if (anyChecked) {
    const granted = await ensureNotificationPermission();
    if (!granted) {
      shinyBox.checked = false;
      iv100Box.checked = false;
      bothBox.checked = false;
      notifyShiny = false;
      notifyIv100 = false;
      notifyShinyIv100 = false;
      persistNotifySettings();
      updateNotificationStatusText("Notifications are blocked in your browser. Enable them in your browser's site settings to use this.");
      return;
    }
  }

  notifyShiny = shinyBox.checked;
  notifyIv100 = iv100Box.checked;
  notifyShinyIv100 = bothBox.checked;
  persistNotifySettings();
  updateNotificationStatusText();
  await initNotificationBaseline();
}

// /api/history and /api/raids/history are separate endpoints now (used to
// be combinable via an include_raids flag on /api/history) - notifications
// care about both catches and raid catches, so fetch each and merge/sort
// client-side rather than picking just one source.
async function fetchRecentEntries(limit) {
  const [catchesRes, raidsRes] = await Promise.all([
    fetch(APP_BASE + "/api/history?limit=" + limit),
    fetch(APP_BASE + "/api/raids/history?limit=" + limit),
  ]);
  const catchesData = await catchesRes.json();
  const raidsData = await raidsRes.json();
  const merged = catchesData.entries.concat(raidsData.entries);
  merged.sort((a, b) => (a.ts > b.ts ? -1 : 1));
  return merged.slice(0, limit);
}

async function initNotificationBaseline() {
  // The first time notifications are turned on, don't retroactively notify
  // for existing history - just record the newest entry as the starting
  // point and only notify for anything newer than that from now on.
  if (lastNotifiedTs) return;
  try {
    const entries = await fetchRecentEntries(1);
    lastNotifiedTs = entries.length ? entries[0].ts : "1970-01-01T00:00:00";
  } catch (e) {
    lastNotifiedTs = "1970-01-01T00:00:00";
  }
  localStorage.setItem("pogostats_last_notified_ts", lastNotifiedTs);
}

function fireCatchNotification(title, entry) {
  const body = entry.pokemon_name + (entry.trainer ? " · " + entry.trainer : "");
  let notif;
  try {
    notif = new Notification(title, { body: body, icon: APP_BASE + "/favicon.svg" });
  } catch (e) {
    return;
  }
  notif.onclick = () => {
    window.focus();
    showTab(entry.event_type === "raid" ? "raids" : "history");
  };
}

async function checkForNewCatchNotifications() {
  if (!notifyShiny && !notifyIv100 && !notifyShinyIv100) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!lastNotifiedTs) {
    await initNotificationBaseline();
    return;
  }

  const entries = await fetchRecentEntries(20);
  const newEntries = entries
    .filter((e) => e.ts > lastNotifiedTs)
    .sort((a, b) => (a.ts > b.ts ? 1 : -1));

  for (const entry of newEntries) {
    if (entry.shiny && entry.iv100 && notifyShinyIv100) {
      fireCatchNotification("Shiny + 100% IV catch!", entry);
    } else if (entry.shiny && notifyShiny) {
      fireCatchNotification("Shiny catch!", entry);
    } else if (entry.iv100 && notifyIv100) {
      fireCatchNotification("100% IV catch!", entry);
    }
    lastNotifiedTs = entry.ts;
  }

  if (newEntries.length) {
    localStorage.setItem("pogostats_last_notified_ts", lastNotifiedTs);
  }
}

function formatRelativeTime(ts) {
  // Same "Z"-suffix handling as formatHistoryTimestamp - ts is naive UTC
  // from the backend and only gets a timezone marker appended if it
  // doesn't already have one.
  const hasTz = /[Zz]$|[+-]\d{2}:\d{2}$/.test(ts);
  const d = new Date(hasTz ? ts : ts + "Z");
  if (isNaN(d.getTime())) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return diffMin + (diffMin === 1 ? " minute ago" : " minutes ago");
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return diffHour + (diffHour === 1 ? " hour ago" : " hours ago");
  const diffDay = Math.floor(diffHour / 24);
  return diffDay + (diffDay === 1 ? " day ago" : " days ago");
}

// Shows how long ago the last catch/flee/raid was actually recorded -
// separate from whether the Bot/Backend processes are technically running
// (the tray icon/status window only knows that much). If this stops moving
// forward while you're actively playing, that's a sign the bot silently
// stopped receiving Discord events (expired token, network issue, etc.)
// even though the process itself might still look "up".
async function loadLastSynced() {
  const el = document.getElementById("last-synced");
  if (!el) return;
  const res = await fetch(APP_BASE + "/api/last-synced");
  const data = await res.json();
  el.textContent = data.ts ? "Last catch synced " + formatRelativeTime(data.ts) : "No catches recorded yet.";
}

async function loadSummary() {
  const res = await fetch(APP_BASE + "/api/summary?tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  document.getElementById("stat-today").textContent = data.today;
  document.getElementById("stat-week").textContent = data.week;
  document.getElementById("stat-all").textContent = data.all_time;
  document.getElementById("stat-shiny").textContent = data.shiny_today;
  document.getElementById("stat-iv100").textContent = data.iv100_today;
}

// Cache of the last data used to render each chart, keyed by chart var name,
// so a periodic refresh that returns identical data can skip re-rendering
// entirely instead of causing a visible flash.
let lastTimeseriesData = null;
let lastTopSpeciesData = null;
let lastRaidTopSpeciesData = null;

async function loadRollingSummary() {
  const res = await fetch(APP_BASE + "/api/rolling/summary?hours=24");
  const data = await res.json();
  document.getElementById("rolling-stat-encounters").textContent = data.encounters;
  document.getElementById("rolling-stat-raids").textContent = data.raids;
}

async function loadTimeseries() {
  const res = await fetch(APP_BASE + "/api/timeseries?days=" + chartDays + "&tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  const serialized = JSON.stringify(data);
  if (serialized === lastTimeseriesData && lineChart) return;
  lastTimeseriesData = serialized;

  const labels = data.map((d) => d.day.slice(5));
  const counts = data.map((d) => d.count);

  if (lineChart) {
    // Update the existing chart in place - Chart.js animates the transition
    // smoothly instead of the whole canvas flashing away and back.
    lineChart.data.labels = labels;
    lineChart.data.datasets[0].data = counts;
    lineChart.update();
    return;
  }

  lineChart = new Chart(document.getElementById("lineChart"), {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        data: counts,
        borderColor: "#1D9E75",
        backgroundColor: "rgba(29,158,117,0.12)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: "#1D9E75",
        pointHoverBorderColor: "#0d1117",
        pointHoverBorderWidth: 2,
        borderWidth: 2
      }]
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ctx.parsed.y + (ctx.parsed.y === 1 ? " catch" : " catches"),
          },
        },
      },
      // mode "index" + intersect: false means the tooltip triggers anywhere
      // along a given x position, not just when the cursor is exactly on top
      // of a point - needed since pointRadius is 0 (no visible dot to hit).
      interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });
}

async function loadTopSpecies() {
  const res = await fetch(APP_BASE + "/api/top-species?days=" + chartDays + "&limit=8&tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  const serialized = JSON.stringify(data);
  if (serialized === lastTopSpeciesData && barChart) return;
  lastTopSpeciesData = serialized;

  const labels = data.map((d) => d.name);
  const counts = data.map((d) => d.count);

  if (barChart) {
    barChart.data.labels = labels;
    barChart.data.datasets[0].data = counts;
    barChart.update();
    return;
  }

  barChart = new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{ data: counts, backgroundColor: "#7F77DD", borderRadius: 4 }]
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });
}

async function loadCalendar(year, month) {
  const res = await fetch(APP_BASE + "/api/calendar/" + year + "/" + month + "?tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  renderCalendar(year, month, data);
}

function renderCalendar(year, month, data) {
  const grid = document.getElementById("calendar-grid");
  grid.innerHTML = "";
  document.getElementById("calendar-label").textContent = new Date(year, month - 1, 1)
    .toLocaleDateString("en-US", { month: "long", year: "numeric" });

  ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].forEach((d) => {
    const el = document.createElement("div");
    el.className = "cal-head";
    el.textContent = d;
    grid.appendChild(el);
  });

  const firstDay = new Date(year, month - 1, 1);
  const startOffset = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month, 0).getDate();

  for (let i = 0; i < startOffset; i++) {
    grid.appendChild(document.createElement("div"));
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
    const info = data[dateStr];
    const cell = document.createElement("div");
    cell.className = "cal-cell";
    cell.dataset.date = dateStr;
    let html = day + '<div class="cal-count">' + (info ? info.catches : "-") + "</div>";
    if (info && info.shiny > 0) {
      html += '<span class="cal-shiny">&#10022;</span>';
    }
    cell.innerHTML = html;
    cell.onclick = () => selectDay(dateStr, cell);
    grid.appendChild(cell);
  }
}

async function selectDay(dateStr, cellEl) {
  document.querySelectorAll(".cal-cell.selected").forEach((el) => el.classList.remove("selected"));
  if (cellEl) cellEl.classList.add("selected");

  const res = await fetch(APP_BASE + "/api/day/" + dateStr + "?tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  const panel = document.getElementById("day-detail");
  const dateLabel = new Date(dateStr + "T00:00:00")
    .toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

  let speciesHtml = '<p class="top-species">No catches on this day.</p>';
  if (data.top_species.length) {
    speciesHtml = '<ul class="top-species-list">' +
      data.top_species.map((s) =>
        '<li><img src="' + spriteUrl(s.pokemon_id, false) + '" onerror="this.style.visibility=\'hidden\'" alt="">' +
        s.name + " (" + s.count + ")</li>"
      ).join("") +
      "</ul>";
  }

  let raidSpeciesHtml = '<p class="top-species">No raid catches on this day.</p>';
  if (data.raid_top_species.length) {
    raidSpeciesHtml = '<ul class="top-species-list">' +
      data.raid_top_species.map((s) =>
        '<li><img src="' + spriteUrl(s.pokemon_id, false) + '" onerror="this.style.visibility=\'hidden\'" alt="">' +
        s.name + " (" + s.count + ")</li>"
      ).join("") +
      "</ul>";
  }

  panel.innerHTML =
    '<p class="day-title">' + dateLabel + "</p>" +
    '<div class="day-stats">' +
    '<div class="metric-card"><p class="label">Catches</p><p class="value">' + data.catches + "</p></div>" +
    '<div class="metric-card shiny"><p class="label">Shinies</p><p class="value">' + data.shiny + "</p></div>" +
    '<div class="metric-card iv100"><p class="label">100% IV</p><p class="value">' + data.iv100 + "</p></div>" +
    "</div>" +
    speciesHtml +
    '<p class="day-subtitle">Raids</p>' +
    '<div class="day-stats">' +
    '<div class="metric-card"><p class="label">Raids</p><p class="value">' + data.raids + "</p></div>" +
    '<div class="metric-card shiny"><p class="label">Shinies</p><p class="value">' + data.raid_shiny + "</p></div>" +
    '<div class="metric-card iv100"><p class="label">100% IV</p><p class="value">' + data.raid_iv100 + "</p></div>" +
    "</div>" +
    raidSpeciesHtml;
}

function changeMonth(delta) {
  currentMonth += delta;
  if (currentMonth > 12) { currentMonth = 1; currentYear += 1; }
  if (currentMonth < 1) { currentMonth = 12; currentYear -= 1; }
  loadCalendar(currentYear, currentMonth);
}

function formatHistoryTimestamp(ts) {
  // ts is ISO in the format YYYY-MM-DDTHH:MM:SS (UTC, from the Discord
  // timestamp). Only append "Z" if the string doesn't already have a
  // timezone - otherwise e.g. "...+00:00" + "Z" produces an invalid date.
  const hasTz = /[Zz]$|[+-]\d{2}:\d{2}$/.test(ts);
  const d = new Date(hasTz ? ts : ts + "Z");
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-US", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

// The Pokemon name doubles as a link into the per-species stats view. Only
// the numeric id is passed to the handler, so a name containing quotes or an
// apostrophe can't break out of the inline attribute.
function speciesLink(entry) {
  if (entry.pokemon_id == null) return entry.pokemon_name;
  return '<a href="javascript:void(0)" class="species-link" onclick="showSpeciesDetail(' +
    entry.pokemon_id + ')">' + entry.pokemon_name + "</a>";
}

function renderHistoryEntry(entry) {
  let rowClass = "history-row catch";
  if (entry.event_type === "flee") rowClass = "history-row flee";
  else if (entry.event_type === undefined || entry.event_type === "raid") rowClass = "history-row raid";

  let badges = "";
  if (entry.event_type === "flee") {
    badges += '<span class="badge flee">Fled</span>';
  }
  if (entry.shiny && entry.iv100) {
    // Special combined tag for the rare case of a shiny AND perfect IV catch.
    badges += '<span class="badge perfect-shiny">Shiny 100% IV</span>';
  } else {
    if (entry.shiny) {
      badges += '<span class="badge shiny">Shiny</span>';
    }
    if (entry.iv100) {
      badges += '<span class="badge iv100">100% IV</span>';
    }
  }

  let mapLink = '<span class="history-map disabled">No GPS data</span>';
  if (entry.lat != null && entry.lon != null) {
    const url = "https://www.google.com/maps?q=" + entry.lat + "," + entry.lon;
    mapLink = '<a class="history-map" href="' + url + '" target="_blank" rel="noopener">View on Google Maps</a>';
  }

  const icon = spriteUrl(entry.pokemon_id, entry.shiny);

  let statsLine = "";
  if (entry.iv_atk != null) {
    const pct = ivPercent(entry.iv_atk, entry.iv_def, entry.iv_sta);
    const parts = [entry.iv_atk + "/" + entry.iv_def + "/" + entry.iv_sta + " - " + pct + "%"];
    if (entry.cp != null) parts.push("CP " + entry.cp);
    if (entry.level != null) parts.push("Lvl " + entry.level);
    statsLine = '<p class="history-stats">' + parts.join(" · ") + "</p>";
  }

  return (
    '<div class="' + rowClass + '">' +
    '<img class="history-icon" src="' + icon + '" onerror="this.style.visibility=\'hidden\'" alt="">' +
    '<div class="history-main">' +
    '<p class="history-name">' + speciesLink(entry) + "</p>" +
    '<p class="history-meta">' + formatHistoryTimestamp(entry.ts) +
    (!hideTrainerName && entry.trainer ? " · " + entry.trainer : "") + "</p>" +
    statsLine +
    "</div>" +
    '<div class="history-badges">' + badges + "</div>" +
    mapLink +
    "</div>"
  );
}

// Compact card version of the same entry, for Grid view - fewer details
// (no trainer/IV breakdown line) so more entries fit on screen at once.
function renderHistoryCard(entry) {
  let cardClass = "history-card catch";
  if (entry.event_type === "flee") cardClass = "history-card flee";
  else if (entry.event_type === undefined || entry.event_type === "raid") cardClass = "history-card raid";

  let badges = "";
  if (entry.event_type === "flee") {
    badges += '<span class="badge flee">Fled</span>';
  }
  if (entry.shiny && entry.iv100) {
    badges += '<span class="badge perfect-shiny">Shundo</span>';
  } else {
    if (entry.shiny) {
      badges += '<span class="badge shiny">Shiny</span>';
    }
    if (entry.iv100) {
      badges += '<span class="badge iv100">100%</span>';
    }
  }

  const icon = spriteUrl(entry.pokemon_id, entry.shiny);

  // Always render something in the map slot (a real link, or a disabled
  // placeholder) rather than omitting it entirely - keeps every card the
  // same height regardless of whether that particular entry has GPS data
  // (flees, for instance, never do).
  let mapLink = '<span class="history-map disabled">No GPS</span>';
  if (entry.lat != null && entry.lon != null) {
    const url = "https://www.google.com/maps?q=" + entry.lat + "," + entry.lon;
    mapLink = '<a class="history-map" href="' + url + '" target="_blank" rel="noopener">Map</a>';
  }

  return (
    '<div class="' + cardClass + '">' +
    '<img class="history-icon" src="' + icon + '" onerror="this.style.visibility=\'hidden\'" alt="">' +
    '<p class="history-name">' + speciesLink(entry) + "</p>" +
    '<p class="history-meta">' + formatHistoryTimestamp(entry.ts) + "</p>" +
    '<div class="history-badges">' + badges + "</div>" +
    mapLink +
    "</div>"
  );
}

function renderHistoryList(entries) {
  const list = document.getElementById("history-list");
  if (entries.length === 0) {
    list.innerHTML = '<p class="top-species">No entries yet.</p>';
    return;
  }
  const renderFn = historyDisplayMode === "grid" ? renderHistoryCard : renderHistoryEntry;
  list.innerHTML = entries.map(renderFn).join("");
}

async function loadHistoryPage(reset) {
  if (reset) {
    historyOffset = 0;
    lastHistoryEntries = [];
  }

  const shinyParam = historyShinyOnly ? "&shiny=true" : "";
  const iv100Param = historyIv100Only ? "&iv100=true" : "";
  const trainerParam = historyTrainer ? "&trainer=" + encodeURIComponent(historyTrainer) : "";
  const searchParam = historySearch ? "&q=" + encodeURIComponent(historySearch) : "";

  let url;
  if (historySubTab === "raids") {
    // Raids have no "flee" concept, so no type filter here.
    url = APP_BASE + "/api/raids/history?limit=" + historyLimit + "&offset=" + historyOffset + shinyParam + iv100Param + trainerParam + searchParam;
  } else {
    const typeParam = historyFilter === "all" ? "" : "&type=" + historyFilter;
    url = APP_BASE + "/api/history?limit=" + historyLimit + "&offset=" + historyOffset + typeParam + shinyParam + iv100Param + trainerParam + searchParam;
  }

  const res = await fetch(url);
  const data = await res.json();
  historyTotal = data.total;
  lastHistoryEntries = lastHistoryEntries.concat(data.entries);
  renderHistoryList(lastHistoryEntries);

  historyOffset += data.entries.length;

  const btn = document.getElementById("history-load-more");
  btn.disabled = historyOffset >= historyTotal;
  btn.textContent = btn.disabled ? "No more entries" : "Load More";
}

function loadMoreHistory() {
  loadHistoryPage(false);
}

function onHistoryFilterChange() {
  historyFilter = document.getElementById("history-filter").value;
  historyShinyOnly = document.getElementById("history-shiny-only").checked;
  historyIv100Only = document.getElementById("history-iv100-only").checked;
  historyTrainer = document.getElementById("history-trainer").value;
  loadHistoryPage(true);
}

// Debounced so typing a Pokemon name doesn't fire a request per keystroke.
function onHistorySearchInput() {
  clearTimeout(historySearchDebounce);
  historySearchDebounce = setTimeout(() => {
    historySearch = document.getElementById("history-search").value.trim();
    loadHistoryPage(true);
  }, 250);
}

// Fills the account dropdown from the distinct trainers the backend knows
// about, keeping the current selection if it still exists. Called on load and
// whenever the History tab is opened, so newly-seen accounts show up.
async function loadTrainerFilterOptions() {
  const select = document.getElementById("history-trainer");
  if (!select) return;
  let trainers = [];
  try {
    const res = await fetch(APP_BASE + "/api/trainers");
    trainers = await res.json();
  } catch (e) {
    return;
  }
  const previous = historyTrainer;
  select.innerHTML = '<option value="">All accounts</option>' +
    trainers.map((t) => '<option value="' + t.replace(/"/g, "&quot;") + '">' + t + "</option>").join("");
  // Restore the active selection if that trainer is still present.
  select.value = previous && trainers.includes(previous) ? previous : "";
  historyTrainer = select.value;
}

function setHistorySubTab(tab) {
  historySubTab = tab;
  document.getElementById("history-subtab-catches").classList.toggle("active", tab === "catches");
  document.getElementById("history-subtab-raids").classList.toggle("active", tab === "raids");
  // The Catches/Flees type filter only makes sense for the Catches sub-tab.
  // Use visibility (not display) so the element still reserves its layout
  // space when hidden - otherwise the toolbar reflows and the display-mode
  // toggle visibly shifts left/right when switching sub-tabs.
  const showTypeFilter = tab === "catches";
  const filterLabel = document.getElementById("history-filter-label");
  const filterSelect = document.getElementById("history-filter");
  filterLabel.style.visibility = showTypeFilter ? "" : "hidden";
  filterSelect.style.visibility = showTypeFilter ? "" : "hidden";
  filterSelect.style.pointerEvents = showTypeFilter ? "" : "none";
  filterSelect.tabIndex = showTypeFilter ? 0 : -1;
  loadHistoryPage(true);
}

function setHistoryDisplayMode(mode) {
  historyDisplayMode = mode;
  localStorage.setItem("pogostats_history_display", mode);
  document.getElementById("history-display-list").classList.toggle("active", mode === "list");
  document.getElementById("history-display-grid").classList.toggle("active", mode === "grid");
  document.getElementById("history-list").classList.toggle("grid-mode", mode === "grid");
  // Re-render what's already loaded in the new layout - no need to refetch.
  renderHistoryList(lastHistoryEntries);
}

// --- Per-species detail view ---------------------------------------------
// Clicking a Pokemon name in History (or in the Raids tab) swaps the list out
// for a stats view for that species. Non-null while that view is open, which
// also tells the periodic refresh to leave the list alone (see refreshTab).
let speciesDetailId = null;
// Leaflet instance for this view's "last catch location" map, plus the last
// payload rendered. An unchanged periodic refresh is skipped entirely so it
// can never rebuild (and re-centre) the map under the user - the same trick
// the charts use with lastTimeseriesData.
let speciesMap = null;
let lastSpeciesData = null;

const SPECIES_PERIOD_ROWS = [
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["all", "All time"],
];

function setHistoryBrowseVisible(visible) {
  const display = visible ? "" : "none";
  ["history-subtabs", "history-toolbar", "history-list", "history-load-more"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = display;
  });
}

async function showSpeciesDetail(pokemonId) {
  if (pokemonId == null) return;
  // The view lives in the History tab, so a click from the Raids tab brings
  // you here rather than duplicating the whole view over there. Switch first:
  // showTab() clears any open species view, so ours is opened afterwards.
  if (currentTab !== "history") showTab("history");
  speciesDetailId = pokemonId;
  lastSpeciesData = null;  // different species - always render fresh
  setHistoryBrowseVisible(false);
  document.getElementById("species-detail").style.display = "";
  await loadSpeciesDetail();
}

// Tears the view down without reloading the list itself. `clearSearch` is used
// when the user re-enters through the History tab (see showHistoryTab).
function resetSpeciesView(clearSearch) {
  speciesDetailId = null;
  lastSpeciesData = null;
  if (speciesMap) {
    // The container is thrown away with the innerHTML, so drop the Leaflet
    // instance with it rather than leaking it and its listeners.
    speciesMap.remove();
    speciesMap = null;
  }
  const detail = document.getElementById("species-detail");
  if (detail) detail.style.display = "none";
  setHistoryBrowseVisible(true);
  if (clearSearch) {
    historySearch = "";
    const box = document.getElementById("history-search");
    if (box) box.value = "";
  }
}

// Back button: return to the list exactly as it was left, filters and search
// intact - that's what "back" should do.
function closeSpeciesDetail() {
  resetSpeciesView(false);
  loadHistoryPage(true);
}

// The History tab button. Clicking it always lands on the list itself, even
// from inside a species view, and clears the search box so you get the full
// list back instead of whatever was last typed there.
function showHistoryTab() {
  resetSpeciesView(true);
  showTab("history");
}

async function loadSpeciesDetail() {
  if (speciesDetailId == null) return;
  // Follows the History account filter, so the numbers match the list the
  // user clicked from rather than silently going global.
  const trainerParam = historyTrainer ? "?trainer=" + encodeURIComponent(historyTrainer) : "";
  const res = await fetch(APP_BASE + "/api/species/" + speciesDetailId + trainerParam);
  renderSpeciesDetail(await res.json());
}

// Builds the little Leaflet map showing where the species was last caught -
// same dark CARTO basemap and pin as the dashboard's "Last Catch Location".
// Called after the detail HTML is in the DOM, since it needs the container.
function renderSpeciesMap(location) {
  if (speciesMap) {
    speciesMap.remove();
    speciesMap = null;
  }
  if (!location) return;
  const el = document.getElementById("species-map");
  if (!el) return;
  speciesMap = L.map(el, { zoomControl: false, attributionControl: true })
    .setView([location.lat, location.lon], 15);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    subdomains: "abcd",
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  }).addTo(speciesMap);
  L.marker([location.lat, location.lon], { icon: lastLocationPinIcon() }).addTo(speciesMap);
  // The container is only sized once it's visible, so nudge Leaflet after
  // layout - same reason the dashboard map does this.
  setTimeout(() => speciesMap.invalidateSize(), 100);
}

function renderSpeciesDetail(data) {
  // Skip the re-render entirely when nothing changed, so the 15s refresh
  // can't rebuild the map (and throw away the user's pan/zoom) for nothing.
  const serialized = JSON.stringify(data);
  if (serialized === lastSpeciesData) return;
  lastSpeciesData = serialized;

  const el = document.getElementById("species-detail");
  const name = data.name || "#" + data.pokemon_id;
  let scope = "All accounts";
  if (data.trainer) scope = hideTrainerName ? "Selected account" : data.trainer;

  const rows = SPECIES_PERIOD_ROWS.map(function (period) {
    const stats = (data.periods && data.periods[period[0]]) || {};
    const cells = ["caught", "raids", "shiny", "hundo", "shundo", "fled"]
      .map((key) => "<td>" + (stats[key] || 0) + "</td>").join("");
    return "<tr><th>" + period[1] + "</th>" + cells + "</tr>";
  }).join("");

  let lastCaught = '<p class="top-species">No catches recorded for this Pokemon yet.</p>';
  if (data.last_caught) {
    lastCaught = '<p class="top-species">Last caught ' + formatRelativeTime(data.last_caught.ts) +
      " · " + formatHistoryTimestamp(data.last_caught.ts) +
      (data.last_caught.is_raid ? " · raid" : "") +
      (!hideTrainerName && data.last_caught.trainer ? " · " + data.last_caught.trainer : "") +
      "</p>";
  }

  // Separate from last_caught on purpose: the newest catch may have had no
  // GPS data, so this can point at an older entry (or be absent entirely).
  let locationBlock = '<p class="day-subtitle">Last catch location</p>' +
    '<p class="top-species">No GPS data recorded for this Pokemon yet.</p>';
  if (data.last_location) {
    const url = "https://www.google.com/maps?q=" + data.last_location.lat + "," + data.last_location.lon;
    locationBlock =
      '<p class="day-subtitle">Last catch location</p>' +
      '<div class="map-wrap species-map-wrap"><div id="species-map"></div></div>' +
      '<p class="map-caption">' + formatHistoryTimestamp(data.last_location.ts) +
      ' · <a class="map-caption-link" href="' + url + '" target="_blank" rel="noopener">' +
      "Open in Google Maps</a></p>";
  }

  el.innerHTML =
    '<div class="species-header">' +
    '<img class="species-icon" src="' + spriteUrl(data.pokemon_id, false) +
    '" onerror="this.style.visibility=\'hidden\'" alt="">' +
    '<div class="species-heading"><p class="day-title">' + name + "</p>" +
    '<p class="top-species">Account: ' + scope + "</p></div>" +
    '<button class="species-back" onclick="closeSpeciesDetail()">Back</button>' +
    "</div>" +
    '<div class="species-table-wrap"><table class="species-table"><thead><tr>' +
    "<th>Period</th><th>Caught</th><th>Raids</th><th>Shiny</th><th>Hundo</th>" +
    "<th>Shundo</th><th>Fled</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
    lastCaught +
    locationBlock;

  renderSpeciesMap(data.last_location);
}

let raidBarChart;
let raidHistoryOffset = 0;
const raidHistoryLimit = 50;
let raidHistoryTotal = 0;

async function loadRaidSummary() {
  const res = await fetch(APP_BASE + "/api/raids/summary?tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  document.getElementById("raid-stat-today").textContent = data.today;
  document.getElementById("raid-stat-week").textContent = data.week;
  document.getElementById("raid-stat-all").textContent = data.all_time;
  document.getElementById("raid-stat-shiny").textContent = data.shiny_today;
  document.getElementById("raid-stat-iv100").textContent = data.iv100_today;
}

async function loadRaidTopSpecies() {
  const res = await fetch(APP_BASE + "/api/raids/top-species?days=" + chartDays + "&limit=8&tz=" + encodeURIComponent(userTimezone));
  const data = await res.json();
  const serialized = JSON.stringify(data);
  if (serialized === lastRaidTopSpeciesData && raidBarChart) return;
  lastRaidTopSpeciesData = serialized;

  const labels = data.map((d) => d.name);
  const counts = data.map((d) => d.count);

  if (raidBarChart) {
    raidBarChart.data.labels = labels;
    raidBarChart.data.datasets[0].data = counts;
    raidBarChart.update();
    return;
  }

  raidBarChart = new Chart(document.getElementById("raidBarChart"), {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{ data: counts, backgroundColor: "#5b8def", borderRadius: 4 }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

async function loadRaidHistoryPage(reset) {
  if (reset) {
    raidHistoryOffset = 0;
    document.getElementById("raid-history-list").innerHTML = "";
  }

  const res = await fetch(APP_BASE + "/api/raids/history?limit=" + raidHistoryLimit + "&offset=" + raidHistoryOffset);
  const data = await res.json();
  raidHistoryTotal = data.total;

  const list = document.getElementById("raid-history-list");
  if (reset && data.entries.length === 0) {
    list.innerHTML = '<p class="top-species">No raid catches yet.</p>';
  } else {
    list.insertAdjacentHTML("beforeend", data.entries.map(renderHistoryEntry).join(""));
  }

  raidHistoryOffset += data.entries.length;

  const btn = document.getElementById("raid-history-load-more");
  btn.disabled = raidHistoryOffset >= raidHistoryTotal;
  btn.textContent = btn.disabled ? "No more entries" : "Load More";
}

function loadMoreRaidHistory() {
  loadRaidHistoryPage(false);
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("setting-hide-trainer").checked = hideTrainerName;
  document.getElementById("setting-chart-days").value = String(chartDays);
  document.getElementById("setting-heatmap-days").value = heatmapDays;
  populateTimezoneOptions();
  document.getElementById("setting-notify-shiny").checked = notifyShiny;
  document.getElementById("setting-notify-iv100").checked = notifyIv100;
  document.getElementById("setting-notify-shiny-iv100").checked = notifyShinyIv100;
  updateNotificationStatusText();
  updateChartTitles();
  updateExportLink();

  document.getElementById("history-display-list").classList.toggle("active", historyDisplayMode === "list");
  document.getElementById("history-display-grid").classList.toggle("active", historyDisplayMode === "grid");
  document.getElementById("history-list").classList.toggle("grid-mode", historyDisplayMode === "grid");

  updateClock();
  setInterval(updateClock, 1000);
  loadLastLocation();
  setInterval(loadLastLocation, 60000);
  loadHeatmap();
  loadLastSynced();
  // Refreshed on its own faster cadence than the general per-tab refresh
  // below, since the whole point is a relative-time display ("5 minutes
  // ago") that should keep advancing even while just sitting on the
  // Dashboard tab.
  setInterval(loadLastSynced, 30000);

  loadSummary();
  loadTimeseries();
  loadTopSpecies();
  loadCalendar(currentYear, currentMonth);
  loadTrainerFilterOptions();
  loadHistoryPage(true);
  loadRaidSummary();
  loadRaidTopSpecies();
  loadRaidHistoryPage(true);
  loadRollingSummary();

  // Periodically refresh whichever tab is currently visible, so new
  // catches/raids show up automatically without a manual page reload.
  setInterval(() => refreshTab(currentTab), 15000);

  // Independent of which tab is open - checks for new shiny/100%/both
  // catches so notifications work even if you're not looking at History.
  if (notifyShiny || notifyIv100 || notifyShinyIv100) {
    initNotificationBaseline();
  }
  setInterval(checkForNewCatchNotifications, 20000);
});
