// Small vanilla JS frontend, no build tools needed. Fetches everything
// directly from the FastAPI app on the same origin (see backend/main.py).

if (window.Chart) {
  Chart.defaults.color = "#97a3b5";
  Chart.defaults.borderColor = "rgba(255,255,255,0.08)";
}

function spriteUrl(pokemonId, shiny) {
  return "/sprites/" + pokemonId + ".png" + (shiny ? "?shiny=true" : "");
}

function ivPercent(atk, def, sta) {
  return Math.round(((atk + def + sta) / 45) * 100);
}

function updateClock() {
  const now = new Date();
  document.getElementById("clock-time").textContent = now.toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  document.getElementById("clock-date").textContent = now.toLocaleDateString("en-US", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

let lastLocationMap = null;
let lastLocationMarker = null;
// Tracks the coordinates currently shown, so a refresh with the same last
// catch doesn't yank the view back to it if the user has panned/zoomed away.
let lastLocationShown = null;

function lastLocationPinIcon() {
  return L.divIcon({
    className: "map-pin-icon",
    html: '<div class="map-pin-dot"></div>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

async function loadLastLocation() {
  const res = await fetch("/api/last-location");
  const data = await res.json();
  const mapEl = document.getElementById("last-location-map");
  const empty = document.getElementById("map-empty");
  const caption = document.getElementById("last-location-caption");

  if (!data || data.lat == null || data.lon == null) {
    mapEl.style.display = "none";
    empty.style.display = "flex";
    caption.textContent = "";
    return;
  }

  mapEl.style.display = "block";
  empty.style.display = "none";

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

async function loadHeatmap() {
  const res = await fetch("/api/locations");
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
  if (serialized === lastHeatmapData && heatMap) {
    // Nothing new - don't touch the layer or the view at all.
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

  if (isFirstLoad) {
    // Only fit the view to the data on the very first load - subsequent
    // refreshes must never reset wherever the user has panned/zoomed to.
    const bounds = L.latLngBounds(heatPoints.map((p) => [p[0], p[1]]));
    heatMap.fitBounds(bounds, { padding: [20, 20], maxZoom: 15 });
    setTimeout(() => heatMap.invalidateSize(), 100);
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

// Notification preferences - all off by default until the user opts in.
let notifyShiny = localStorage.getItem("pogostats_notify_shiny") === "true";
let notifyIv100 = localStorage.getItem("pogostats_notify_iv100") === "true";
let notifyShinyIv100 = localStorage.getItem("pogostats_notify_shiny_iv100") === "true";
let lastNotifiedTs = localStorage.getItem("pogostats_last_notified_ts") || null;

let historyOffset = 0;
const historyLimit = 50;
let historyFilter = "all";
let historyIncludeRaids = false;
let historyTotal = 0;

function showTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab-content").forEach((el) => el.classList.remove("visible"));
  document.querySelectorAll(".tab-btn").forEach((el) => el.classList.remove("active"));
  document.getElementById("tab-" + tab).classList.add("visible");
  document.getElementById("btn-" + tab).classList.add("active");
  if (tab === "dashboard") {
    if (lastLocationMap) setTimeout(() => lastLocationMap.invalidateSize(), 50);
    if (heatMap) setTimeout(() => heatMap.invalidateSize(), 50);
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
  } else if (tab === "calendar") {
    loadCalendar(currentYear, currentMonth);
  } else if (tab === "history") {
    loadHistoryPage(true);
  } else if (tab === "raids") {
    loadRaidSummary();
    loadRaidTopSpecies();
    loadRaidHistoryPage(true);
  } else if (tab === "settings") {
    document.getElementById("setting-hide-trainer").checked = hideTrainerName;
    document.getElementById("setting-chart-days").value = String(chartDays);
    document.getElementById("setting-notify-shiny").checked = notifyShiny;
    document.getElementById("setting-notify-iv100").checked = notifyIv100;
    document.getElementById("setting-notify-shiny-iv100").checked = notifyShinyIv100;
    updateNotificationStatusText();
  }
}

function updateExportLink() {
  document.getElementById("export-csv-link").href = "/api/export/csv?hide_trainer=" + hideTrainerName;
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

function updateChartTitles() {
  document.getElementById("line-chart-title").textContent = "Catches - Last " + chartDays + " Days";
  document.getElementById("bar-chart-title").textContent = "Top Pokemon (" + chartDays + " Days)";
  document.getElementById("raid-bar-chart-title").textContent = "Most Common Raid Bosses (" + chartDays + " Days)";
}

function onChartDaysChange() {
  chartDays = parseInt(document.getElementById("setting-chart-days").value, 10);
  localStorage.setItem("pogostats_chart_days", String(chartDays));
  updateChartTitles();
  loadTimeseries();
  loadTopSpecies();
  loadRaidTopSpecies();
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

async function initNotificationBaseline() {
  // The first time notifications are turned on, don't retroactively notify
  // for existing history - just record the newest entry as the starting
  // point and only notify for anything newer than that from now on.
  if (lastNotifiedTs) return;
  try {
    const res = await fetch("/api/history?limit=1&include_raids=true");
    const data = await res.json();
    lastNotifiedTs = data.entries.length ? data.entries[0].ts : "1970-01-01T00:00:00";
  } catch (e) {
    lastNotifiedTs = "1970-01-01T00:00:00";
  }
  localStorage.setItem("pogostats_last_notified_ts", lastNotifiedTs);
}

function fireCatchNotification(title, entry) {
  const body = entry.pokemon_name + (entry.trainer ? " · " + entry.trainer : "");
  let notif;
  try {
    notif = new Notification(title, { body: body, icon: "/favicon.svg" });
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

  const res = await fetch("/api/history?limit=20&include_raids=true");
  const data = await res.json();
  const newEntries = data.entries
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

async function loadSummary() {
  const res = await fetch("/api/summary");
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

async function loadTimeseries() {
  const res = await fetch("/api/timeseries?days=" + chartDays);
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
        borderWidth: 2
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });
}

async function loadTopSpecies() {
  const res = await fetch("/api/top-species?days=" + chartDays + "&limit=8");
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
  const res = await fetch("/api/calendar/" + year + "/" + month);
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

  const res = await fetch("/api/day/" + dateStr);
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
    '<p class="history-name">' + entry.pokemon_name + "</p>" +
    '<p class="history-meta">' + formatHistoryTimestamp(entry.ts) +
    (!hideTrainerName && entry.trainer ? " · " + entry.trainer : "") + "</p>" +
    statsLine +
    "</div>" +
    '<div class="history-badges">' + badges + "</div>" +
    mapLink +
    "</div>"
  );
}

async function loadHistoryPage(reset) {
  if (reset) {
    historyOffset = 0;
    document.getElementById("history-list").innerHTML = "";
  }

  const typeParam = historyFilter === "all" ? "" : "&type=" + historyFilter;
  const raidsParam = historyIncludeRaids ? "&include_raids=true" : "";
  const res = await fetch("/api/history?limit=" + historyLimit + "&offset=" + historyOffset + typeParam + raidsParam);
  const data = await res.json();
  historyTotal = data.total;

  const list = document.getElementById("history-list");
  if (reset && data.entries.length === 0) {
    list.innerHTML = '<p class="top-species">No entries yet.</p>';
  } else {
    list.insertAdjacentHTML("beforeend", data.entries.map(renderHistoryEntry).join(""));
  }

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
  historyIncludeRaids = document.getElementById("history-include-raids").checked;
  loadHistoryPage(true);
}

let raidBarChart;
let raidHistoryOffset = 0;
const raidHistoryLimit = 50;
let raidHistoryTotal = 0;

async function loadRaidSummary() {
  const res = await fetch("/api/raids/summary");
  const data = await res.json();
  document.getElementById("raid-stat-today").textContent = data.today;
  document.getElementById("raid-stat-week").textContent = data.week;
  document.getElementById("raid-stat-all").textContent = data.all_time;
  document.getElementById("raid-stat-shiny").textContent = data.shiny_today;
  document.getElementById("raid-stat-iv100").textContent = data.iv100_today;
}

async function loadRaidTopSpecies() {
  const res = await fetch("/api/raids/top-species?days=" + chartDays + "&limit=8");
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

  const res = await fetch("/api/raids/history?limit=" + raidHistoryLimit + "&offset=" + raidHistoryOffset);
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
  document.getElementById("setting-notify-shiny").checked = notifyShiny;
  document.getElementById("setting-notify-iv100").checked = notifyIv100;
  document.getElementById("setting-notify-shiny-iv100").checked = notifyShinyIv100;
  updateNotificationStatusText();
  updateChartTitles();
  updateExportLink();

  updateClock();
  setInterval(updateClock, 1000);
  loadLastLocation();
  setInterval(loadLastLocation, 60000);
  loadHeatmap();

  loadSummary();
  loadTimeseries();
  loadTopSpecies();
  loadCalendar(currentYear, currentMonth);
  loadHistoryPage(true);
  loadRaidSummary();
  loadRaidTopSpecies();
  loadRaidHistoryPage(true);

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
