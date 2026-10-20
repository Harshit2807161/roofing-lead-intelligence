// Leaflet maps for the lead list and the detail page. The server renders the data (a JSON script
// tag or data attributes); this file only draws it, and redraws the list map after HTMX swaps.
(function () {
  "use strict";

  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  let listMap = null;
  let listLayer = null;

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function baseMap(el, center, zoom) {
    const map = L.map(el, { scrollWheelZoom: false }).setView(center, zoom);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(map);
    return map;
  }

  function originMarker(center) {
    return L.circleMarker(center, {
      radius: 6, color: token("--fg"), weight: 2, fillColor: token("--bg-surface"), fillOpacity: 1,
    }).bindTooltip("ZIP 10013 (search centre)");
  }

  function scoreColour(score) {
    if (score == null) return token("--fg-subtle");
    if (score >= 70) return token("--accent-deep");
    if (score >= 50) return token("--accent");
    return token("--accent-glow");
  }

  function markerRadius(p) {
    // Bigger dot = better lead. Unscored leads fall back to review volume so the map still reads.
    if (p.score != null) return 4 + (p.score / 100) * 9;
    return 4 + Math.min(1, Math.log10((p.review_count || 0) + 1) / 3) * 5;
  }

  function popupHtml(p) {
    const score = p.score == null ? "Not scored" : `Score ${Math.round(p.score)}`;
    const rating = p.rating ? ` · ${p.rating}★ (${p.review_count})` : "";
    return (
      `<span class="map-pop-name">${escapeHtml(p.name)}</span>` +
      `<span>#${p.rank} · ${escapeHtml(score)}${rating}</span><br>` +
      `<span>${escapeHtml(p.city)}, ${escapeHtml(p.state)} · ${p.distance_mi} mi</span><br>` +
      (p.play ? `<span>${escapeHtml(p.play)}</span><br>` : "") +
      `<a class="map-pop-link" href="/leads/${encodeURIComponent(p.gaf_id)}">Open lead &rarr;</a>`
    );
  }

  function readPoints() {
    const el = document.getElementById("map-points");
    try {
      return el ? JSON.parse(el.textContent) : [];
    } catch (_) {
      return [];
    }
  }

  function drawListMarkers() {
    if (!listMap) return;
    listLayer.clearLayers();
    const points = readPoints();
    // Draw low scores first so the strongest leads sit on top.
    points
      .slice()
      .sort((a, b) => (a.score ?? -1) - (b.score ?? -1))
      .forEach((p) => {
        L.circleMarker([p.lat, p.lng], {
          radius: markerRadius(p), color: token("--fg"), weight: 1,
          fillColor: scoreColour(p.score), fillOpacity: 0.88,
        }).bindPopup(popupHtml(p)).addTo(listLayer);
      });
  }

  function openListMap() {
    const el = document.getElementById("lead-map");
    if (!el || typeof L === "undefined") return;
    if (!listMap) {
      const center = JSON.parse(el.dataset.center);
      listMap = baseMap(el, center, 10);
      listLayer = L.layerGroup().addTo(listMap);
      originMarker(center).addTo(listMap);
      drawListMarkers();
    }
    listMap.invalidateSize();
  }

  function initToggle() {
    const button = document.querySelector("[data-map-toggle]");
    const panel = document.getElementById("map-panel");
    if (!button || !panel) return;
    button.addEventListener("click", () => {
      const show = panel.hidden;
      panel.hidden = !show;
      button.setAttribute("aria-pressed", String(show));
      button.textContent = show ? "Hide map" : "Show map";
      if (show) openListMap();
    });
  }

  function initDetailMap() {
    const el = document.getElementById("detail-map");
    if (!el || typeof L === "undefined") return;
    const here = [parseFloat(el.dataset.lat), parseFloat(el.dataset.lng)];
    const center = JSON.parse(el.dataset.center);
    const map = baseMap(el, here, 11);
    L.circleMarker(here, {
      radius: 8, color: token("--fg"), weight: 1, fillColor: token("--accent"), fillOpacity: 0.95,
    }).bindTooltip(escapeHtml(el.dataset.name)).addTo(map);
    originMarker(center).addTo(map);
    map.fitBounds(L.latLngBounds([here, center]).pad(0.25), { maxZoom: 12 });
  }

  function init() {
    initToggle();
    initDetailMap();
    document.body.addEventListener("htmx:afterSettle", drawListMarkers);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
