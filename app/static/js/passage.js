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
      marker.setTooltipContent(String(i + 1));
    });
  }

  async function render() {
    const points = latLngs();
    undoButton.disabled = points.length === 0;
    clearButton.disabled = points.length === 0;

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
        <td>${leg.from} → ${leg.to}</td>
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

  function addWaypoint(latlng) {
    const marker = L.marker(latlng, { draggable: true })
      .bindTooltip('', { permanent: true, direction: 'top', className: 'passage-wp' })
      .addTo(map);

    marker.on('drag', () => { redrawLine(); });
    marker.on('dragend', () => { redrawLine(); render(); });
    marker.on('click', (ev) => {
      L.DomEvent.stopPropagation(ev);   // do not also drop a new point here
      removeWaypoint(marker);
    });

    waypoints.push(marker);
    renumber();
    redrawLine();
    render();
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

  map.on('click', (ev) => addWaypoint(ev.latlng));
  undoButton.addEventListener('click', () => {
    if (waypoints.length) removeWaypoint(waypoints[waypoints.length - 1]);
  });
  clearButton.addEventListener('click', clearRoute);

  // Open on the Adriatic, this app's usual cruising ground, matching the leg
  // map's fallback view.
  map.setView([43.5, 16.0], 8);
  render();
})();
