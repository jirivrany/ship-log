// Shared Leaflet setup. Every map in the app is built here so that the base
// layer, the nautical overlay, the attribution and the layer control exist in
// exactly one place — pages differ in what they *draw*, not in how the map is
// made.
//
// The boundary is deliberate: create() returns a plain Leaflet map and knows
// nothing about legs, voyages or log entries. Page-specific behaviour (track
// polylines, entry markers, click-to-add popups) stays with the page.
window.ShipLogMap = (function () {
  // OpenSeaMap is a *transparent seamark overlay*, not a basemap: it renders
  // buoys, lights, beacons and harbours over a normal OSM base. There is no
  // standalone OpenSeaMap basemap to switch to.
  const SEAMARK_URL = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png';

  // Seamark coverage thins out at high zoom and the ceiling varies by area
  // (Hvar renders to z17, Šibenik stops at z15). Without maxNativeZoom the
  // layer would request tiles that come back as empty PNGs and the symbols
  // would silently vanish as you zoom in — which reads as a bug. Capping the
  // native zoom makes Leaflet upscale the last real tile instead: slightly
  // soft, but never blank.
  const SEAMARK_MAX_NATIVE_ZOOM = 17;
  const MAX_ZOOM = 19;

  // Remembering the toggle keeps the choice stable while moving between legs
  // and voyages. Private-mode browsers can throw on access, and the feature is
  // a convenience, so every read and write is guarded.
  const TOGGLE_KEY = 'shiplog.seamarks';

  function seamarksEnabled() {
    try {
      // Default on: this is a sailing logbook, so charted marks are the point.
      return window.localStorage.getItem(TOGGLE_KEY) !== 'off';
    } catch (e) {
      return true;
    }
  }

  function rememberSeamarks(enabled) {
    try {
      window.localStorage.setItem(TOGGLE_KEY, enabled ? 'on' : 'off');
    } catch (e) {
      /* no persistence available; the toggle still works for this page */
    }
  }

  function create(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return null;

    const map = L.map(elementId);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: MAX_ZOOM,
    }).addTo(map);

    // CC-BY-SA requires its own credit alongside the OSM one.
    const seamarks = L.tileLayer(SEAMARK_URL, {
      attribution: '© <a href="https://www.openseamap.org/">OpenSeaMap</a> contributors (CC-BY-SA)',
      maxNativeZoom: SEAMARK_MAX_NATIVE_ZOOM,
      maxZoom: MAX_ZOOM,
    });

    if (seamarksEnabled()) seamarks.addTo(map);

    L.control.layers(null, { 'Sea marks': seamarks }, { collapsed: false }).addTo(map);

    map.on('overlayadd', function (e) {
      if (e.layer === seamarks) rememberSeamarks(true);
    });
    map.on('overlayremove', function (e) {
      if (e.layer === seamarks) rememberSeamarks(false);
    });

    return map;
  }

  // Both existing maps end by framing whatever positioned data they have.
  function fitToLatLons(map, latLons, padding) {
    if (!map || !latLons || latLons.length === 0) return false;
    map.fitBounds(L.latLngBounds(latLons), { padding: padding || [20, 20] });
    return true;
  }

  return { create: create, fitToLatLons: fitToLatLons };
})();
