/* Draw Field Boundary — Leaflet + Leaflet.draw page for sales reps.
 *
 * The POLYGON is the field/problem boundary — it's the only thing sent to the
 * backend and the only thing that feeds the screening score.
 * The colored LINES / MARKERS are annotations ("points of interest"): they are
 * saved separately from the boundary so they can appear in KML, but never feed
 * any calculation.
 *
 * URL params: lead_id (required), lat, lng, backend_url (optional), key (optional)
 */
(function () {
  "use strict";

  var SQM_PER_ACRE = 4046.8564224;
  function param(name) { return new URLSearchParams(window.location.search).get(name); }

  var leadId = param("lead_id") || "";
  var lat = parseFloat(param("lat"));
  var lng = parseFloat(param("lng"));
  var backendUrl = (param("backend_url") || window.location.origin).replace(/\/$/, "");
  // AGENT-H1: API key is sourced from the URL query string (?key=).
  // This means the key appears in browser history, Render logs, and HTTP referrer headers.
  // Changing this mechanism requires updating the AppSheet Draw Boundary URL formula — out of scope here.
  // Operators should rotate API_KEY periodically. SHARE_LINK_SECRET is now separate from this key.
  var apiKey = param("key");
  document.getElementById("lead-id").textContent = leadId || "(missing)";

  var hasPoint = !isNaN(lat) && !isNaN(lng);
  var center = hasPoint ? [lat, lng] : [42.9, -75.5];
  var map = L.map("map").setView(center, hasPoint ? 17 : 7);

  // Base layers: satellite (best for tracing fields) + streets, with a toggle.
  var satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" }
  );
  var streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  });
  satellite.addTo(map);
  L.control.layers({ "Satellite": satellite, "Streets": streets }, null, { collapsed: false }).addTo(map);

  if (hasPoint) {
    L.marker(center).addTo(map).bindPopup("Reported problem location").openPopup();
  }

  var drawn = new L.FeatureGroup();        // the boundary polygon (feeds the score)
  var annotations = new L.FeatureGroup();  // colored lines/markers (export only)
  map.addLayer(drawn);
  map.addLayer(annotations);

  var currentColor = "#e6194b";
  var freehandActive = false;
  var sketching = false;
  var sketchLine = null;
  var sketchPoints = [];
  var selectedAnnotation = null;

  var drawControl = new L.Control.Draw({
    edit: { featureGroup: drawn, remove: true },
    draw: {
      polygon: { allowIntersection: false, showArea: true, shapeOptions: { color: "#1f78ff" } },
      polyline: false,
      marker: true,
      rectangle: false, circle: false, circlemarker: false,
    },
  });
  map.addControl(drawControl);

  var saveBtn = document.getElementById("save");
  var downloadBtn = document.getElementById("download");
  var clearBtn = document.getElementById("clear");
  var freehandBtn = document.getElementById("freehand");
  var deleteNoteBtn = document.getElementById("delete-note");
  var areaEl = document.getElementById("area");
  var statusEl = document.getElementById("status");

  // Color swatches set the color of the NEXT line you draw.
  Array.prototype.forEach.call(document.querySelectorAll("#colors .swatch"), function (sw) {
    sw.addEventListener("click", function () {
      currentColor = sw.getAttribute("data-color");
      Array.prototype.forEach.call(document.querySelectorAll("#colors .swatch"), function (s) {
        s.classList.remove("sel");
      });
      sw.classList.add("sel");
    });
  });

  function showStatus(msg, cls) { statusEl.className = cls; statusEl.textContent = msg; }

  function acresOfLayer(layer) { return L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]) / SQM_PER_ACRE; }

  function setMapDragEnabled(enabled) {
    if (enabled) {
      map.dragging.enable();
      map.doubleClickZoom.enable();
      if (map.tap) map.tap.enable();
    } else {
      map.dragging.disable();
      map.doubleClickZoom.disable();
      if (map.tap) map.tap.disable();
    }
  }

  function setFreehandActive(active) {
    freehandActive = active;
    freehandBtn.classList.toggle("active", active);
    freehandBtn.textContent = active ? "Drawing note..." : "Draw note";
    map.getContainer().classList.toggle("freehand-active", active);
    setMapDragEnabled(!active);
    if (active) {
      clearAnnotationSelection();
      showStatus("Drag on the map to draw a colored note. Tap Draw note again to pan.", "info");
    } else if (!sketching) {
      showStatus("", "hidden");
    }
  }

  function setAnnotationSelected(layer, selected) {
    if (layer && layer.setStyle) {
      layer.setStyle({
        weight: selected ? (layer._noteWeight || 5) + 3 : (layer._noteWeight || 5),
        opacity: selected ? 1 : 0.95,
      });
    }
  }

  function clearAnnotationSelection() {
    if (selectedAnnotation) setAnnotationSelected(selectedAnnotation, false);
    selectedAnnotation = null;
    deleteNoteBtn.disabled = true;
  }

  function selectAnnotation(layer) {
    clearAnnotationSelection();
    selectedAnnotation = layer;
    setAnnotationSelected(layer, true);
    deleteNoteBtn.disabled = false;
    showStatus("Note selected. Tap Delete selected note to remove it.", "info");
  }

  function attachAnnotationBehavior(layer, weight) {
    layer._noteWeight = weight || layer._noteWeight || 5;
    layer.on("click", function (e) {
      if (freehandActive) return;
      if (e.originalEvent) L.DomEvent.stopPropagation(e.originalEvent);
      selectAnnotation(layer);
    });
  }

  function refreshUI() {
    var total = 0;
    drawn.eachLayer(function (l) { total += acresOfLayer(l); });
    var hasBoundary = drawn.getLayers().length > 0;
    var hasAnything = hasBoundary || annotations.getLayers().length > 0;
    areaEl.innerHTML = "Area: <b>" + (hasBoundary ? total.toFixed(2) : "—") + " acres</b>";
    saveBtn.disabled = !hasBoundary;       // only a polygon can be saved
    downloadBtn.disabled = !hasAnything;   // KML can include just annotations too
    clearBtn.disabled = !hasAnything;
    deleteNoteBtn.disabled = !selectedAnnotation || !annotations.hasLayer(selectedAnnotation);
  }

  function shouldAddSketchPoint(latlng) {
    if (!sketchPoints.length) return true;
    var prev = map.latLngToLayerPoint(sketchPoints[sketchPoints.length - 1]);
    var next = map.latLngToLayerPoint(latlng);
    return prev.distanceTo(next) >= 4;
  }

  function startSketch(e) {
    if (!freehandActive || sketching) return;
    if (e.originalEvent && e.originalEvent.button && e.originalEvent.button !== 0) return;
    if (e.originalEvent) L.DomEvent.preventDefault(e.originalEvent);
    sketching = true;
    sketchPoints = [e.latlng];
    sketchLine = L.polyline(sketchPoints, {
      color: currentColor,
      weight: 5,
      opacity: 0.95,
      smoothFactor: 1,
    }).addTo(map);
  }

  function moveSketch(e) {
    if (!sketching || !sketchLine || !e.latlng) return;
    if (e.originalEvent) L.DomEvent.preventDefault(e.originalEvent);
    if (!shouldAddSketchPoint(e.latlng)) return;
    sketchPoints.push(e.latlng);
    sketchLine.addLatLng(e.latlng);
  }

  function finishSketch() {
    if (!sketching) return;
    sketching = false;
    var added = false;
    if (sketchLine && sketchPoints.length > 1) {
      annotations.addLayer(sketchLine);
      attachAnnotationBehavior(sketchLine, 5);
      added = true;
    } else if (sketchLine) {
      map.removeLayer(sketchLine);
    }
    sketchLine = null;
    sketchPoints = [];
    setFreehandActive(false);
    if (added) showStatus("Note added. Tap it to select/delete, or tap Draw note to add another.", "success");
    refreshUI();
  }

  freehandBtn.addEventListener("click", function () {
    setFreehandActive(!freehandActive);
  });

  deleteNoteBtn.addEventListener("click", function () {
    if (!selectedAnnotation) return;
    annotations.removeLayer(selectedAnnotation);
    map.removeLayer(selectedAnnotation);
    selectedAnnotation = null;
    refreshUI();
    showStatus("Selected note deleted.", "success");
  });

  map.on("mousedown touchstart", startSketch);
  map.on("mousemove touchmove", moveSketch);
  map.on("mouseup touchend", finishSketch);
  map.on(L.Draw.Event.DRAWSTART, function () {
    if (freehandActive) setFreehandActive(false);
  });

  map.on(L.Draw.Event.CREATED, function (e) {
    if (e.layerType === "polygon") {
      drawn.clearLayers();          // keep a single boundary
      drawn.addLayer(e.layer);
    } else if (e.layerType === "polyline") {
      e.layer.setStyle({ color: currentColor, weight: 5, opacity: 0.95 });
      annotations.addLayer(e.layer);
      attachAnnotationBehavior(e.layer, 5);
    } else {
      annotations.addLayer(e.layer); // marker / point of interest
      attachAnnotationBehavior(e.layer, 5);
    }
    refreshUI();
  });
  map.on(L.Draw.Event.EDITED, refreshUI);
  map.on(L.Draw.Event.DELETED, refreshUI);

  clearBtn.addEventListener("click", function () {
    drawn.clearLayers();
    annotations.clearLayers();
    clearAnnotationSelection();
    refreshUI();
    showStatus("", "hidden");
  });

  /* ---------------- KML (boundary polygon + colored annotations) ---------------- */
  function hexToKmlColor(hex) {            // #rrggbb -> KML aabbggrr
    var h = (hex || "#e6194b").replace("#", "");
    return "ff" + h.substr(4, 2) + h.substr(2, 2) + h.substr(0, 2);
  }
  function escapeXml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");
  }
  function safeFileName(value) {
    var safe = String(value || "boundary").replace(/[^A-Za-z0-9._-]/g, "_").replace(/^[._]+|[._]+$/g, "");
    return (safe || "boundary").slice(0, 80);
  }
  function coordStr(latlngs, close) {
    var c = latlngs.map(function (p) { return p.lng + "," + p.lat + ",0"; });
    if (close && latlngs.length) c.push(latlngs[0].lng + "," + latlngs[0].lat + ",0");
    return c.join(" ");
  }
  function buildKml() {
    var styles = "", placemarks = "";
    var boundary = drawn.getLayers()[0];
    if (boundary) {
      placemarks += "<Placemark><name>Field boundary</name><Polygon><outerBoundaryIs>" +
        "<LinearRing><coordinates>" + coordStr(boundary.getLatLngs()[0], true) +
        "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>";
    }
    var i = 0;
    annotations.eachLayer(function (layer) {
      i++;
      if (layer.getLatLngs) {            // polyline
        var col = (layer.options && layer.options.color) || currentColor;
        var sid = "ln" + i;
        styles += '<Style id="' + sid + '"><LineStyle><color>' + hexToKmlColor(col) +
          "</color><width>4</width></LineStyle></Style>";
        placemarks += '<Placemark><name>Note ' + i + '</name><styleUrl>#' + sid +
          "</styleUrl><LineString><coordinates>" + coordStr(layer.getLatLngs(), false) +
          "</coordinates></LineString></Placemark>";
      } else if (layer.getLatLng) {      // marker
        var ll = layer.getLatLng();
        placemarks += "<Placemark><name>Point " + i + "</name><Point><coordinates>" +
          ll.lng + "," + ll.lat + ",0</coordinates></Point></Placemark>";
      }
    });
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>' + escapeXml(leadId || "boundary") +
      "</name>" + styles + placemarks + "</Document></kml>";
  }

  downloadBtn.addEventListener("click", function () {
    if (!drawn.getLayers().length && !annotations.getLayers().length) {
      showStatus("Draw a boundary or a note first.", "error");
      return;
    }
    var blob = new Blob([buildKml()], { type: "application/vnd.google-earth.kml+xml" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = safeFileName(leadId || "boundary") + ".kml";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showStatus("KML downloaded (boundary + notes).", "success");
  });

  function annotationsToGeoJSON() {
    var features = [];
    annotations.eachLayer(function (layer) {
      if (!layer.toGeoJSON) return;
      var feature = layer.toGeoJSON();
      feature.properties = feature.properties || {};
      if (layer.options && layer.options.color) {
        feature.properties.color = layer.options.color;
      }
      features.push(feature);
    });
    return { type: "FeatureCollection", features: features };
  }

  /* ---------------- Save (boundary polygon + separate notes) ---------------- */
  saveBtn.addEventListener("click", function () {
    if (!leadId) { showStatus("Missing lead_id in the link.", "error"); return; }
    var layers = drawn.getLayers();
    if (!layers.length) { showStatus("Draw the field boundary (polygon) first.", "error"); return; }

    saveBtn.disabled = true;
    showStatus("Saving…", "info");

    var headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;
    var noteGeoJSON = annotationsToGeoJSON();

    fetch(backendUrl + "/save-boundary", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({
        LeadID: leadId,
        BoundarySource: "Sales drawn boundary",
        BoundaryGeoJSON: layers[0].toGeoJSON(),
        BoundaryAnnotationsGeoJSON: noteGeoJSON.features.length ? noteGeoJSON : null,
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

  refreshUI();
})();
