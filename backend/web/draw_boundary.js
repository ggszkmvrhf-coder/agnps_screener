/* Draw Field Boundary — simple Leaflet + Leaflet.draw page for sales reps.
 *
 * URL parameters:
 *   lead_id      (required) the Lead key to save against
 *   lat, lng     (optional) GPS point to center on + drop a marker
 *   backend_url  (optional) backend origin; defaults to where this page is served
 *   key          (optional) API key, sent as X-API-Key if the backend requires it
 */
(function () {
  "use strict";

  var SQM_PER_ACRE = 4046.8564224;

  function param(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  var leadId = param("lead_id") || "";
  var lat = parseFloat(param("lat"));
  var lng = parseFloat(param("lng"));
  var backendUrl = (param("backend_url") || window.location.origin).replace(/\/$/, "");
  var apiKey = param("key");

  document.getElementById("lead-id").textContent = leadId || "(missing)";

  var hasPoint = !isNaN(lat) && !isNaN(lng);
  var center = hasPoint ? [lat, lng] : [42.9, -75.5]; // fallback: central NY
  var map = L.map("map").setView(center, hasPoint ? 17 : 7);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  if (hasPoint) {
    L.marker(center).addTo(map).bindPopup("Reported problem location").openPopup();
  }

  // Layer that holds the drawn polygon.
  var drawn = new L.FeatureGroup();
  map.addLayer(drawn);

  var drawControl = new L.Control.Draw({
    edit: { featureGroup: drawn, remove: true },
    draw: {
      polygon: { allowIntersection: false, showArea: true },
      polyline: false, rectangle: false, circle: false, marker: false, circlemarker: false,
    },
  });
  map.addControl(drawControl);

  var saveBtn = document.getElementById("save");
  var clearBtn = document.getElementById("clear");
  var areaEl = document.getElementById("area");
  var statusEl = document.getElementById("status");

  function acresOfLayer(layer) {
    var latlngs = layer.getLatLngs()[0];
    var m2 = L.GeometryUtil.geodesicArea(latlngs);
    return m2 / SQM_PER_ACRE;
  }

  function refreshArea() {
    var total = 0;
    drawn.eachLayer(function (l) { total += acresOfLayer(l); });
    var has = drawn.getLayers().length > 0;
    areaEl.innerHTML = "Area: <b>" + (has ? total.toFixed(2) : "—") + " acres</b>";
    saveBtn.disabled = !has;
    clearBtn.disabled = !has;
  }

  // Keep a single polygon: clear previous when a new one is drawn.
  map.on(L.Draw.Event.CREATED, function (e) {
    drawn.clearLayers();
    drawn.addLayer(e.layer);
    refreshArea();
  });
  map.on(L.Draw.Event.EDITED, refreshArea);
  map.on(L.Draw.Event.DELETED, refreshArea);

  clearBtn.addEventListener("click", function () {
    drawn.clearLayers();
    refreshArea();
    showStatus("", "hidden");
  });

  function showStatus(msg, cls) {
    statusEl.className = cls;
    statusEl.textContent = msg;
  }

  function firstLayerGeoJSON() {
    var layers = drawn.getLayers();
    if (!layers.length) return null;
    return layers[0].toGeoJSON(); // a GeoJSON Feature with Polygon geometry
  }

  saveBtn.addEventListener("click", function () {
    if (!leadId) { showStatus("Missing lead_id in the link.", "error"); return; }
    var feature = firstLayerGeoJSON();
    if (!feature) { showStatus("Draw a polygon first.", "error"); return; }

    saveBtn.disabled = true;
    showStatus("Saving…", "info");

    var headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;

    fetch(backendUrl + "/save-boundary", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({
        LeadID: leadId,
        BoundarySource: "Sales drawn boundary",
        BoundaryGeoJSON: feature,
      }),
    })
      .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
      .then(function (res) {
        if (res.ok && res.body && res.body.success) {
          areaEl.innerHTML = "Area: <b>" + res.body.BoundaryAreaAcres + " acres</b>";
          showStatus(res.body.message || "Saved.", "success");
        } else {
          var m = (res.body && (res.body.message || res.body.detail)) || "Save failed.";
          showStatus("Could not save: " + m, "error");
          saveBtn.disabled = false;
        }
      })
      .catch(function (err) {
        showStatus("Network error: " + err.message, "error");
        saveBtn.disabled = false;
      });
  });

  refreshArea();
})();
