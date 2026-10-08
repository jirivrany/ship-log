// Passage planner: drop waypoints on the map, read courses and distances.
//
// The arithmetic mirrors app/passage.py deliberately. Plans are ephemeral and
// update live while a marker is dragged, so round-tripping to the server for
// every mouse move would make the drag feel laggy for no gain. The Python
// module remains the reference implementation and is the one under test for
// the awkward cases; tests/test_passage.py pins the values both must produce.
(function () {
  const map = ShipLogMap.create('passage-map');
  if (!map) return;

  const EARTH_RADIUS_NM = 3440.065;
  const MIN_LEG_NM = 1e-6;

  // Magnetic variation. The World Magnetic Model is a server-side concern, so
  // the page asks for a value per leg midpoint and caches it: variation moves
  // slowly enough that a 0.1° grid is far finer than a compass can be read.
  const variationCache = new Map();

  function variationKey(lat, lon) {
    return Math.round(lat * 10) + ',' + Math.round(lon * 10);
  }

  async function variationFor(lat, lon) {
    const key = variationKey(lat, lon);
    if (variationCache.has(key)) return variationCache.get(key);
    try {
      const r = await fetch(`/passage/variation?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`);
      if (!r.ok) throw new Error('lookup failed');
      const data = await r.json();
      variationCache.set(key, data.variation);
      return data.variation;
    } catch (e) {
      // True courses are still useful on their own, so a failed lookup blanks
      // the magnetic column rather than breaking the plan.
      variationCache.set(key, null);
      return null;
    }
  }

  function rhumbLine(a, b) {
    const lat1 = a.lat * Math.PI / 180, lon1 = a.lng * Math.PI / 180;
    const lat2 = b.lat * Math.PI / 180, lon2 = b.lng * Math.PI / 180;

    const dLat = lat2 - lat1;
    let dLon = lon2 - lon1;

    const dPsi = lat2 !== lat1
      ? Math.log(Math.tan(lat2 / 2 + Math.PI / 4) / Math.tan(lat1 / 2 + Math.PI / 4))
      : 0;
    // q degenerates on an east-west leg, which follows a parallel.
    const q = Math.abs(dPsi) > 1e-12 ? dLat / dPsi : Math.cos(lat1);

    // Take the short way round the antimeridian.
    if (Math.abs(dLon) > Math.PI) dLon -= Math.sign(dLon) * 2 * Math.PI;

    const distance = Math.hypot(dLat, q * dLon) * EARTH_RADIUS_NM;
    if (distance < MIN_LEG_NM) return { distance: 0, course: 0 };

    const course = (Math.atan2(dLon, dPsi) * 180 / Math.PI + 360) % 360;
    return { distance: distance, course: course };
  }

  function courseChange(previous, current) {
    return ((current - previous + 540) % 360) - 180;
  }

  function formatHours(hours) {
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    // Rounding 59.7 minutes up must roll the hour, not print "7h 60m".
    return m === 60 ? `${h + 1}h 00m` : `${h}h ${String(m).padStart(2, '0')}m`;
  }

  // --- state -----------------------------------------------------------
  const waypoints = [];   // L.Marker, in click order
  let routeLine = null;

  const rowsBody = document.getElementById('passage-rows');
  const totalCell = document.getElementById('passage-total');
  const resultPanel = document.getElementById('passage-result');
  const emptyPanel = document.getElementById('passage-empty');
  const variationNote = document.getElementById('variation-note');
  const undoButton = document.getElementById('undo-waypoint');
  const clearButton = document.getElementById('clear-route');

  function latLngs() {
    return waypoints.map(m => m.getLatLng());
  }

  // A waypoint's name is what the helm reads, so it travels with the marker
  // and falls back to its position in the route.
  function labelOf(marker, index) {
    return marker.wpName || String(index + 1);
  }

  function redrawLine() {
    const points = latLngs();
    if (routeLine) routeLine.setLatLngs(points);
    else if (points.length > 1) {
      routeLine = L.polyline(points, { color: '#dc2626', weight: 3, opacity: 0.85 }).addTo(map);
    }
    // Keep the line under the markers so they stay clickable.
    if (routeLine) routeLine.bringToBack();
  }

  function renumber() {
    waypoints.forEach((marker, i) => {
      marker.setTooltipContent(labelOf(marker, i));
    });
  }

  async function render() {
    const points = latLngs();
    undoButton.disabled = points.length === 0;
    clearButton.disabled = points.length === 0;
    refreshPlanButtons();

    const legs = [];
    let cumulative = 0;
    let previousCourse = null;

    for (let i = 0; i < points.length - 1; i++) {
      const { distance, course } = rhumbLine(points[i], points[i + 1]);
      if (distance < MIN_LEG_NM) continue;   // a double click in one place
      cumulative += distance;
      legs.push({
        from: i + 1,
        to: i + 2,
        fromName: labelOf(waypoints[i], i),
        toName: labelOf(waypoints[i + 1], i + 1),
        distance: distance,
        course: course,
        cumulative: cumulative,
        alter: previousCourse === null ? null : courseChange(previousCourse, course),
        midLat: (points[i].lat + points[i + 1].lat) / 2,
        midLon: (points[i].lng + points[i + 1].lng) / 2,
      });
      previousCourse = course;
    }

    if (legs.length === 0) {
      resultPanel.classList.add('d-none');
      emptyPanel.classList.remove('d-none');
      return;
    }
    emptyPanel.classList.add('d-none');
    resultPanel.classList.remove('d-none');

    // Paint true courses immediately, then fill the magnetic column when the
    // lookups land — dragging stays responsive either way.
    rowsBody.innerHTML = legs.map(leg => `
      <tr>
        <td>${leg.fromName} → ${leg.toName}</td>
        <td class="text-end">${leg.course.toFixed(0)}°</td>
        <td class="text-end text-muted" data-magnetic="${leg.from}">…</td>
        <td class="text-end">${leg.distance.toFixed(1)} NM</td>
        <td class="text-end">${leg.cumulative.toFixed(1)} NM</td>
        <td>${leg.alter === null ? '' :
            `${Math.abs(leg.alter).toFixed(0)}° to ${leg.alter > 0 ? 'starboard' : 'port'}`}</td>
      </tr>`).join('');

    totalCell.textContent = cumulative.toFixed(1) + ' NM';

    // Match on the rendered attribute, not on the number: Jinja writes "5.0"
    // where JavaScript stringifies 5.0 as "5", so a templated selector misses.
    document.querySelectorAll('[data-eta-speed]').forEach(cell => {
      const speed = parseFloat(cell.dataset.etaSpeed);
      if (speed > 0) cell.textContent = formatHours(cumulative / speed);
    });

    const variations = [];
    for (const leg of legs) {
      const variation = await variationFor(leg.midLat, leg.midLon);
      const cell = rowsBody.querySelector(`[data-magnetic="${leg.from}"]`);
      if (!cell) continue;   // the route changed while the lookup was in flight
      if (variation === null) {
        cell.textContent = '—';
      } else {
        variations.push(variation);
        cell.textContent = ((leg.course - variation + 360) % 360).toFixed(0) + '°';
        cell.classList.remove('text-muted');
      }
    }
    showVariationNote(variations);
  }

  // Spell out the correction behind the magnetic column, so the number can be
  // checked against the chart rather than taken on trust.
  function showVariationNote(variations) {
    if (!variationNote) return;

    if (variations.length === 0) {
      variationNote.textContent = 'Magnetic variation unavailable — courses are true.';
      return;
    }

    const low = Math.min(...variations);
    const high = Math.max(...variations);
    const easterly = high >= 0;
    // The range is quoted by magnitude, so order it that way: a westerly
    // -13.2 to -12.8 reads 12.8°-13.2°W, not 13.2°-12.8°W.
    const from = Math.min(Math.abs(low), Math.abs(high));
    const to = Math.max(Math.abs(low), Math.abs(high));
    // Variation drifts along a passage; quote the span only when the ends
    // differ by enough to show at the precision used here.
    const span = (from.toFixed(1) === to.toFixed(1))
      ? `${to.toFixed(1)}°${easterly ? 'E' : 'W'}`
      : `${from.toFixed(1)}°–${to.toFixed(1)}°${easterly ? 'E' : 'W'}`;
    const sign = easterly ? 'subtracted from' : 'added to';

    variationNote.textContent =
      `Magnetic = true − variation. Variation here is ${span}, ` +
      `${sign} the true course.`;
  }

  function addWaypoint(latlng, name) {
    const marker = L.marker(latlng, { draggable: true })
      .bindTooltip('', { permanent: true, direction: 'top', className: 'passage-wp' })
      .addTo(map);

    marker.on('drag', () => { redrawLine(); });
    marker.on('dragend', () => { redrawLine(); render(); });
    // A click opens the waypoint's own popup rather than acting at once.
    // Naming and removing cannot share a single click — a double-click to
    // rename would delete the marker on its first half — and the number is a
    // tooltip that passes clicks through to the map underneath, so it cannot
    // be the handle either.
    marker.on('click', (ev) => {
      L.DomEvent.stopPropagation(ev);   // do not also drop a new point here
      marker.bindPopup(waypointPopup(marker)).openPopup();
      const input = marker.getPopup().getContent().querySelector('input');
      if (input) { input.focus(); input.select(); }
    });
    marker.on('contextmenu', (ev) => {
      L.DomEvent.preventDefault(ev);
      marker.bindPopup(waypointPopup(marker)).openPopup();
    });

    if (name) marker.wpName = name;
    waypoints.push(marker);
    renumber();
    redrawLine();
    render();
  }

  // Waypoints behave like the log-entry markers on the leg map: a click opens
  // the marker's own popup and every action is a button inside it. Nothing
  // acts on the click itself — naming and removing cannot share one, and the
  // number is a tooltip that passes clicks through to the map underneath.
  function waypointPopup(marker) {
    const index = waypoints.indexOf(marker);
    const point = marker.getLatLng();

    const container = L.DomUtil.create('div', 'passage-wp-popup');
    container.innerHTML =
      `<strong>Waypoint ${index + 1}</strong>` +
      `<span class="popup-source">${point.lat.toFixed(4)}, ` +
      `${point.lng.toFixed(4)}</span><br>` +
      `<input type="text" class="popup-name-input" ` +
      `value="${(marker.wpName || '').replace(/"/g, '&quot;')}" ` +
      `placeholder="Name (optional)">` +
      `<button type="button" class="popup-name-btn">Save name</button>` +
      `<button type="button" class="popup-delete-btn">Delete waypoint</button>`;

    const input = container.querySelector('input');

    function commit() {
      marker.wpName = input.value.trim() || null;
      renumber();
      render();
      marker.closePopup();
    }

    container.querySelector('.popup-name-btn')
      .addEventListener('click', commit);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { L.DomEvent.stop(ev); commit(); }
    });
    container.querySelector('.popup-delete-btn').addEventListener('click', () => {
      marker.closePopup();
      removeWaypoint(marker);
    });

    return container;
  }

  function removeWaypoint(marker) {
    const i = waypoints.indexOf(marker);
    if (i === -1) return;
    map.removeLayer(marker);
    waypoints.splice(i, 1);
    renumber();
    redrawLine();
    render();
  }

  function clearRoute() {
    waypoints.forEach(m => map.removeLayer(m));
    waypoints.length = 0;
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    render();
  }

  // --- 20 NM offshore limit ---------------------------------------------
  // Each computation covers the current viewport and is added to a layer
  // group rather than replacing what is already drawn, so the coverage can be
  // built up area by area into a whole sea. The lines are plain polylines, so
  // they survive zooming and panning without recomputation, and the server
  // caches each area — a second look at the same waters is instant.
  const limitButton = document.getElementById('show-limit');
  const limitSpinner = document.getElementById('limit-spinner');
  const limitLabel = document.getElementById('limit-label');
  const limitStatus = document.getElementById('limit-status');
  const limitLegend = document.getElementById('limit-legend');
  const clearLimitButton = document.getElementById('clear-limit');
  const limitToggle = document.getElementById('toggle-limit');
  const limitToggleLabel = document.getElementById('toggle-limit-label');

  const limitLayers = L.layerGroup();
  // Viewports already computed, as [s, w, n, e]. A new request whose area is
  // already covered is skipped: the answer would be identical and it would
  // cost another Overpass fetch.
  const computedAreas = [];

  function isAlreadyCovered(bounds) {
    return computedAreas.some(([s, w, n, e]) =>
      bounds.getSouth() >= s && bounds.getNorth() <= n &&
      bounds.getWest() >= w && bounds.getEast() <= e);
  }

  function setLimitBusy(busy) {
    limitSpinner.classList.toggle('d-none', !busy);
    limitLabel.textContent = busy ? 'Computing…' : limitButtonLabel();
    limitButton.disabled = busy;
    clearLimitButton.disabled = busy || computedAreas.length === 0;
    limitToggle.disabled = busy || computedAreas.length === 0;
  }

  // Showing and hiding is pure display: the contours stay in memory, so this
  // never recomputes and never asks the server for anything.
  function applyLimitVisibility() {
    const shown = limitToggle.checked && computedAreas.length > 0;
    if (shown && !map.hasLayer(limitLayers)) limitLayers.addTo(map);
    if (!shown && map.hasLayer(limitLayers)) map.removeLayer(limitLayers);
    limitToggleLabel.textContent = limitToggle.checked ? 'Limit shown'
                                                       : 'Limit hidden';
    if (limitLegend) {
      limitLegend.classList.toggle('d-none', !shown);
    }
  }

  function limitButtonLabel() {
    // The button always computes; it never merely reveals something already
    // there. Saying "show" invited a click on an area that was already drawn.
    return computedAreas.length ? 'Extend limit to this view'
                                : 'Compute 20 NM limit';
  }

  // Orange, not purple: OpenSeaMap draws traffic separation schemes in a
  // near-identical violet, and the two are easy to confuse on the chart.
  // Red is already the measured route.
  function drawLimitLines(area) {
    L.polyline(area.lines, {
      color: '#ea580c', weight: 3, opacity: 0.95, dashArray: '8 5',
    }).bindTooltip(`${area.limit_nm} NM from shore`, { sticky: true })
      .addTo(limitLayers);
  }

  function showLimitError(message) {
    limitStatus.textContent = message;
    limitStatus.classList.remove('d-none');
  }

  async function clearLimit() {
    limitLayers.clearLayers();
    computedAreas.length = 0;
    try {
      await fetch('/passage/offshore-limit/areas', { method: 'DELETE' });
    } catch (e) {
      // The lines are gone from the map either way; stored coverage will
      // simply reappear on the next reload.
    }
    limitStatus.classList.add('d-none');
    setLimitBusy(false);
    applyLimitVisibility();
  }

  async function addLimitForView() {
    limitStatus.classList.add('d-none');

    const bounds = map.getBounds();
    if (isAlreadyCovered(bounds)) {
      showLimitError('This view is already covered — pan or zoom out to '
                     + 'waters beyond the lines to extend the limit.');
      return;
    }

    setLimitBusy(true);
    const params = new URLSearchParams({
      south: bounds.getSouth().toFixed(4),
      west: bounds.getWest().toFixed(4),
      north: bounds.getNorth().toFixed(4),
      east: bounds.getEast().toFixed(4),
    });

    try {
      const response = await fetch(`/passage/offshore-limit?${params}`);
      const data = await response.json();
      if (!response.ok) {
        showLimitError(data.detail || 'Could not compute the limit.');
        setLimitBusy(false);
        return;
      }

      // Remember the area even when it holds no line: "everywhere here is
      // within the limit" is a real answer, and recomputing it would just
      // spend another fetch to learn the same thing.
      computedAreas.push([bounds.getSouth(), bounds.getWest(),
                          bounds.getNorth(), bounds.getEast()]);

      if (!data.lines.length) {
        showLimitError(
          'No limit line in this view — the whole area is within '
          + `${data.limit_nm} NM of shore.`);
        setLimitBusy(false);
        return;
      }

      drawLimitLines(data);
      limitToggle.checked = true;
      applyLimitVisibility();
      limitLayers.eachLayer(layer => layer.bringToBack());
    } catch (e) {
      showLimitError('Could not reach the server to compute the limit.');
    }
    setLimitBusy(false);
  }

  limitButton.addEventListener('click', addLimitForView);
  clearLimitButton.addEventListener('click', clearLimit);
  limitToggle.addEventListener('change', applyLimitVisibility);

  // Coverage computed in earlier sessions is kept server-side, so the page
  // redraws it on load rather than making the skipper recompute waters the
  // app has already measured.
  async function restoreStoredAreas() {
    try {
      const response = await fetch('/passage/offshore-limit/areas');
      if (!response.ok) return;
      const data = await response.json();
      data.areas.forEach(area => {
        computedAreas.push([area.south, area.west, area.north, area.east]);
        if (area.lines.length) drawLimitLines(area);
      });
      applyLimitVisibility();
    } catch (e) {
      // No stored coverage is not an error; the button still works.
    }
    setLimitBusy(false);
  }


  // --- saving and loading plans ----------------------------------------
  const planName = document.getElementById('plan-name');
  const savePlanButton = document.getElementById('save-plan');
  const savePlanAsButton = document.getElementById('save-plan-as');
  const loadPlanSelect = document.getElementById('load-plan');
  const deletePlanButton = document.getElementById('delete-plan');
  const planStatus = document.getElementById('plan-status');
  let currentPlanId = LOADED_PLAN_ID;

  function showPlanStatus(message, ok) {
    planStatus.textContent = message;
    planStatus.classList.remove('d-none', 'alert-success', 'alert-warning');
    planStatus.classList.add(ok ? 'alert-success' : 'alert-warning');
  }

  function refreshPlanButtons() {
    const enough = waypoints.length >= 2;
    savePlanButton.disabled = !enough;
    savePlanButton.textContent = currentPlanId ? 'Save changes' : 'Save plan';
    // "Save as new" only means something once a plan is loaded: it is how an
    // edit avoids changing every leg already attached to the original.
    savePlanAsButton.classList.toggle('d-none', !currentPlanId || !enough);
    if (deletePlanButton) deletePlanButton.disabled = !currentPlanId;
  }

  function currentWaypoints() {
    return waypoints.map((marker, i) => {
      const point = marker.getLatLng();
      return {lat: point.lat, lon: point.lng,
              name: marker.wpName || null};
    });
  }

  async function savePlan(asNew) {
    const name = (planName.value || '').trim();
    if (!name) {
      showPlanStatus('Give the plan a name first.', false);
      planName.focus();
      return;
    }
    try {
      const response = await fetch('/passage/plans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          id: asNew ? null : currentPlanId,
          name: name,
          waypoints: currentWaypoints(),
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        showPlanStatus(data.detail || 'Could not save the plan.', false);
        return;
      }
      currentPlanId = data.id;
      refreshPlanButtons();
      showPlanStatus(
        RETURN_LEG_ID
          ? `Saved "${data.name}". Attach it on the leg page.`
          : `Saved "${data.name}".`, true);
    } catch (e) {
      showPlanStatus('Could not reach the server to save the plan.', false);
    }
  }

  savePlanButton.addEventListener('click', () => savePlan(false));
  savePlanAsButton.addEventListener('click', () => savePlan(true));

  if (loadPlanSelect) {
    loadPlanSelect.addEventListener('change', () => {
      const id = loadPlanSelect.value;
      // Loading replaces the drawn route: the planner shows one plan at a
      // time, and the previous one is already saved or deliberately not.
      if (!id) return;
      window.location.href = `/passage?plan=${id}`
        + (RETURN_LEG_ID ? `&leg=${RETURN_LEG_ID}` : '');
    });
  }

  if (deletePlanButton) {
    deletePlanButton.addEventListener('click', async () => {
      if (!currentPlanId) return;
      try {
        const response = await fetch(`/passage/plans/${currentPlanId}`,
                                     {method: 'DELETE'});
        if (!response.ok) {
          showPlanStatus('Could not delete the plan.', false);
          return;
        }
        const data = await response.json();
        const detached = data.legs_detached
          ? ` ${data.legs_detached} leg(s) detached.` : '';
        window.location.href = '/passage?deleted=1';
      } catch (e) {
        showPlanStatus('Could not reach the server to delete the plan.', false);
      }
    });
  }

  map.on('click', (ev) => addWaypoint(ev.latlng));
  undoButton.addEventListener('click', () => {
    if (waypoints.length) removeWaypoint(waypoints[waypoints.length - 1]);
  });
  clearButton.addEventListener('click', clearRoute);

  // Open on the Adriatic, this app's usual cruising ground, matching the leg
  // map's fallback view. This must happen before anything is added to the
  // map: Leaflet needs a centre and zoom before it can place a layer, and
  // adding one first throws inside DomUtil on an undefined container.
  map.setView([43.5, 16.0], 8);

  // A plan opened from its own URL is drawn straight away, now that the map
  // has a view to draw it on.
  LOADED_WAYPOINTS.forEach(wp => addWaypoint(L.latLng(wp.lat, wp.lon), wp.name));
  if (LOADED_WAYPOINTS.length > 1) {
    ShipLogMap.fitToLatLons(map, LOADED_WAYPOINTS.map(wp => [wp.lat, wp.lon]));
  }

  // Stored limit coverage is drawn last, for the same reason: it adds layers,
  // so it must not run before the map has a view. It only happened to work
  // before because its fetch gave setView time to land first.
  restoreStoredAreas();

  refreshPlanButtons();
  render();
})();
