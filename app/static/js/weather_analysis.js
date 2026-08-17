// Weather-analysis enhancements. All are progressive: the page works without
// this file — tables render server-sorted with every row present, and the
// analysis form is a plain POST.

// Analysis form: show that a run is under way. A cold run fetches one request
// per location and year, so the browser can sit on the POST for tens of
// seconds with nothing to show for it. On success the server redirects to the
// result page, which replaces the document, so the busy state never needs
// clearing — only a failed submit returns here, with a fresh page.
(function () {
  const form = document.getElementById('analysis-form');
  if (!form) return;

  const button = document.getElementById('analyze-button');
  const spinner = document.getElementById('analyze-spinner');
  const label = document.getElementById('analyze-label');
  const status = document.getElementById('analyze-status');

  form.addEventListener('submit', function () {
    // Let the browser's own required-field checks win before going busy.
    if (typeof form.checkValidity === 'function' && !form.checkValidity()) return;

    if (spinner) spinner.classList.remove('d-none');
    if (status) status.classList.remove('d-none');
    if (label) label.textContent = 'Analyzing…';
    if (button) {
      // A disabled submit button is not sent with the form, and disabling it
      // outright would also block the submission in some browsers; aria-disabled
      // plus a pointer-events guard keeps the POST intact.
      button.setAttribute('aria-disabled', 'true');
      button.classList.add('disabled');
    }
  });

  // Coming back via the bfcache (browser Back) restores the busy DOM as it
  // was, which would leave a permanent spinner on a form that is idle again.
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    if (spinner) spinner.classList.add('d-none');
    if (status) status.classList.add('d-none');
    if (label) label.textContent = 'Analyze';
    if (button) {
      button.removeAttribute('aria-disabled');
      button.classList.remove('disabled');
    }
  });
})();

document.querySelectorAll('.sortable-table').forEach(function (table) {
  const headers = table.querySelectorAll('th[data-sort]');
  headers.forEach(function (header, column) {
    header.style.cursor = 'pointer';
    header.addEventListener('click', function () {
      const numeric = header.dataset.sort === 'number';
      const ascending = header.dataset.direction !== 'asc';
      headers.forEach(function (other) { delete other.dataset.direction; });
      header.dataset.direction = ascending ? 'asc' : 'desc';

      const body = table.tBodies[0];
      const rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        const x = a.cells[column].textContent.trim();
        const y = b.cells[column].textContent.trim();
        const result = numeric
          ? (parseFloat(x.replace(/[^0-9.\-]/g, '')) || 0) -
            (parseFloat(y.replace(/[^0-9.\-]/g, '')) || 0)
          : x.localeCompare(y);
        return ascending ? result : -result;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  });
});

(function () {
  const table = document.getElementById('daily-table');
  const byLocation = document.getElementById('filter-location');
  const byYear = document.getElementById('filter-year');
  if (!table || !byLocation || !byYear) return;

  function apply() {
    const location = byLocation.value;
    const year = byYear.value;
    Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
      const matches =
        (!location || row.dataset.location === location) &&
        (!year || row.dataset.year === year);
      row.style.display = matches ? '' : 'none';
    });
  }

  byLocation.addEventListener('change', apply);
  byYear.addEventListener('change', apply);
})();
