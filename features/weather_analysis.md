# Historical Weather Analysis

## 1. Purpose

Add a standalone historical-weather analysis tool to Ship Log. It helps a
skipper choose where to sail by comparing the likelihood of favourable,
demanding, and disruptive conditions across candidate destinations in a date
window.

The feature is independent of voyages, legs, and log entries. It shares the
application's FastAPI, Jinja, Bootstrap, SQLite, and weather-unit foundations,
but neither reads from nor modifies voyage data.

The MVP answers:

> Based on selected historical years, which candidate destination is most
> likely to provide suitable sailing conditions in late October?

## 2. Scope

### In scope

- A top-level **Weather Analysis** navigation item and standalone page.
- User-managed locations: add, edit, and remove.
- User selection of one or more locations, a month/day window, and a historical
  year range.
- Direct comparison of selected locations using the same date window, years,
  and classification thresholds.
- Hourly historical weather acquisition from Open-Meteo.
- Daily sailing-condition classifications and aggregate statistics.
- Tables and in-page charts for wind, direction, rain, suitability, and lost
  days.
- CSV exports for normalized hourly data and daily summaries.
- JSON export for the statistical summary.
- SQLite caching of fetched immutable historical data and analysis results.

### Out of scope

- Forecasting, real-time weather, routing, marina recommendations, and boat
  performance calculations.
- Adding historical analysis data to a voyage, leg, printed logbook, or log
  entry.
- Direct Copernicus data acquisition in the MVP.
- Treating the result as an observed weather record at a berth or marina.

## 3. Users and assumptions

The application is a personal, non-commercial Ship Log installation. Open-Meteo
may therefore be used under its free non-commercial terms. The page must
attribute Open-Meteo and ERA5 and state that results are reanalysis estimates,
not observations made at the selected location.

The analyzer supports any recurring date window, from a single day to a full
charter week or longer seasonal period. A Saturday-to-Saturday charter week is
the primary use case. Late October is the first example scenario, not a product
limitation.

Analysis is initially optimized for a one-week to two-week window across 10--20
historical years and up to twelve locations.

## 4. Data provider

### MVP: Open-Meteo Historical Weather API

Use `https://archive-api.open-meteo.com/v1/archive` through the existing
`httpx`-based application integration. Do not add an Open-Meteo Python client:
the existing codebase already has a tested HTTP client and the API response is
small and straightforward.

Every analysis request must include:

```text
hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,pressure_msl
wind_speed_unit=kn
timezone=<location IANA timezone>
cell_selection=sea
models=era5
```

`models=era5` is mandatory. It prevents a change in source model or resolution
between older and newer years, making year-to-year statistics comparable.
`cell_selection=sea` is mandatory for coastal and island locations so the
resolved cell represents sea conditions where possible.

The provider adapter must expose normalized hourly observations, not raw
Open-Meteo response shapes. Its public interface must permit a later direct
Copernicus ERA5 or CERRA implementation without changing aggregation,
classification, storage, exports, or templates.

### Future provider: Copernicus CDS

Direct Copernicus ERA5 is deferred. It requires a user account and API
credentials and adds NetCDF/GRIB acquisition and processing complexity without
improving the MVP's core result over Open-Meteo's ERA5 series. CERRA is a future
optional high-resolution European provider, particularly for coastal analysis.

Credentials for any future provider must be environment variables and must
never be persisted in SQLite or committed to the repository.

## 5. Locations

Locations are user-managed records, each with a region, display name, latitude,
longitude, and IANA timezone. The initial configuration deliberately samples
distinct sailing areas rather than closely overlapping points:

| Region | Key | Display name | Latitude | Longitude | Timezone |
| --- | --- | --- | ---: | ---: | --- |
| Northern Adriatic | `pula` | Pula | 44.85 | 13.85 | Europe/Zagreb |
| Northern Adriatic | `cres` | Cres | 44.95 | 14.40 | Europe/Zagreb |
| Northern Adriatic | `krk` | Krk | 45.10 | 14.65 | Europe/Zagreb |
| Middle Adriatic | `murtersko_more` | Murtersko More | 43.82 | 15.60 | Europe/Zagreb |
| Middle Adriatic | `kornati` | Kornati | 43.78 | 15.30 | Europe/Zagreb |
| Southern Adriatic | `split` | Split | 43.45 | 16.35 | Europe/Zagreb |
| Southern Adriatic | `hvar` | Hvar | 43.15 | 16.45 | Europe/Zagreb |
| Southern Adriatic | `dubrovnik` | Dubrovnik | 42.60 | 18.05 | Europe/Zagreb |
| Balearic Islands | `mallorca` | Mallorca | 39.70 | 3.00 | Europe/Madrid |
| South Baltic | `rugen` | Rugen | 54.42 | 13.45 | Europe/Berlin |
| South Baltic | `falster` | Falster | 54.70 | 12.00 | Europe/Copenhagen |

The UI must provide a location-management view or modal where a user can:

- Add a location with region, display name, latitude, longitude, and timezone.
- Edit any of those fields.
- Remove a location from future selection.

New locations must validate latitude, longitude, a non-empty display name, and
a valid IANA timezone. The location picker must group locations by region and
display configured and provider-resolved coordinates, if supplied. Removal must
not delete prior analysis runs, their snapshots, or cached observations; it only
removes the location from future selection.

The coordinates are representative sea-analysis points, not marina positions.
They must be reviewed against the Open-Meteo sea-cell resolution before release;
the final configured point must resolve to a meaningful sea cell with
`cell_selection=sea`.

## 6. Input and validation

The analysis form contains:

- One or more configured locations.
- Start month/day and end month/day for a recurring annual window.
- Inclusive first and last historical year.
- Optional thresholds for classification, initially filled with defaults.

The MVP supports any valid recurring annual window, including one that crosses
New Year. When the end month/day precedes the start month/day, the end belongs
to the following calendar year; the selected analysis year identifies the year
containing the start date. The first year must be no later than the last year.
Each historical date must be complete and valid. February 29 is excluded unless
every selected analysis year supports it.

The first offered scenario is October 24--30 for the last 15 complete calendar
years. The user may choose any other period, including a Saturday-to-Saturday
charter week. The current incomplete analysis year must not be preselected.

The requested window, selected years, provider/model, provider-resolved
coordinates, timezone, and thresholds are part of an analysis run's metadata.

## 7. Data model and cache

Store analysis data separately from `Voyage`, `Leg`, and `LogEntry`. At minimum,
persist:

### Analysis location

- Unique identifier, region, display name, latitude, longitude, and IANA
  timezone.
- Creation and update timestamps.
- An active flag or equivalent soft-removal state so historical runs retain
  their location snapshots after removal.

### Analysis run

- Unique identifier and creation time.
- Requested locations and date/year range.
- A snapshot of each selected location's display name, region, coordinates, and
  timezone.
- Provider (`open-meteo`) and model (`era5`).
- Location timezone, requested and resolved coordinates, and retrieval timestamp.
- Exact classification thresholds used.

### Hourly observation

- Analysis run identifier.
- UTC timestamp and local date/time.
- Location key and resolved latitude/longitude.
- Wind speed in knots and direction in degrees.
- Wind gust in knots.
- Precipitation in millimetres.
- Mean sea-level pressure in hPa.

### Daily summary

- Analysis run identifier, location, and local date.
- Average and maximum wind in knots.
- Maximum gust in knots, if available.
- Dominant eight-point direction.
- Total precipitation in millimetres.
- Sailing rating and lost-day flag.

Cache normalized hourly data by provider, model, location, and requested date.
Historical ERA5 values are immutable, so a repeated request must reuse valid
cached observations instead of calling the provider again. A failed provider
request must be reported to the user and must not create a successful-looking
empty analysis.

## 8. Calculations

### Wind and direction

- Open-Meteo wind values are already requested in knots.
- Direction is grouped into eight sectors: N, NE, E, SE, S, SW, W, and NW.
- The daily dominant direction is the most frequent valid hourly sector.
- Wind-speed distribution bins are `<10`, `10--15`, `15--20`, `20--25`,
  `25--30`, and `>30 kn`.

### Daily aggregation

Hourly values are aggregated by each location's configured local calendar day:

- `avg_wind_kn`: arithmetic mean of valid hourly wind speeds.
- `max_wind_kn`: maximum valid hourly wind speed.
- `max_gust_kn`: maximum valid hourly gust.
- `rain_total_mm`: sum of valid hourly precipitation.
- `dominant_direction`: most frequent eight-point wind sector.

The selected autumn date window can cross a daylight-saving transition. Store
timestamps in UTC, convert to the location's IANA timezone for daily grouping
and display, and retain both repeated local hours.

### Ratings and lost days

Defaults are configurable per run:

| Rating | Rule |
| --- | --- |
| Green | Maximum wind is below 20 kn and daily rain is below 5 mm. |
| Yellow | Maximum wind is 20--30 kn, or daily rain is 5--30 mm. |
| Red | Maximum wind exceeds 30 kn, maximum gust exceeds 40 kn, or daily rain exceeds 30 mm. |

Red rules take precedence over Yellow, and Yellow rules take precedence over
Green. Boundaries must be implemented unambiguously: 20 kn and 5 mm are Yellow;
30 kn and 30 mm remain Yellow; a value greater than either Red threshold is Red.

The default lost-day rule is:

```python
max_wind_kn > 30 or rain_total_mm > 20
```

For each location and all selected years, calculate:

- Count and percentage of Green, Yellow, and Red days.
- Count and percentage of light, good-sailing, strong, and dangerous wind days.
- Direction distribution.
- Rainy days, heavy-rain days, and consecutive rainy-day events.
- Lost days per historical window.
- Mean lost days per selected window.
- Probability that an analyzed window contains at least one lost day.

## 9. User interface

The standalone page is available at `/weather-analysis`, linked from the shared
navigation as **Weather Analysis**. It has no voyage selector and no voyage
links.

### Form

The page presents the location picker, date-window controls, year range, and
an expandable thresholds section. It explains that the source is ERA5
reanalysis via Open-Meteo and includes the required attribution.

### Result

After an analysis run, show:

1. A concise comparison statement identifying the most and least suitable
   selected locations for the requested window, without presenting the result
   as a forecast or guarantee.
2. Green/Yellow/Red distribution and lost-day probability.
3. A sortable per-location comparison table, with Green-day percentage,
   Red-day percentage, lost-day probability, median maximum wind, and median
   daily rain.
4. Wind-speed distribution and direction distribution.
5. Rainfall and consecutive-rain event statistics.
6. Daily summaries, filterable by location and historical year.
7. Wind rose, wind-speed histogram, rainfall histogram, and sailing-rating
   chart.
8. CSV and JSON download controls.

Charts must remain readable without JavaScript by retaining the equivalent
summary table. A provider or cache failure must be visible as an error state
with no misleading partial success result.

## 10. Exports

### Hourly CSV

```text
timestamp_utc,local_timestamp,location,requested_lat,requested_lon,
resolved_lat,resolved_lon,wind_speed_kn,wind_direction_deg,wind_gust_kn,
precipitation_mm,pressure_msl_hpa,provider,model
```

### Daily CSV

```text
date,location,avg_wind_kn,max_wind_kn,max_gust_kn,rain_total_mm,
dominant_direction,sailing_rating,lost_day,provider,model
```

### Summary JSON

The JSON export includes run metadata and:

```json
{
  "wind_bins": {},
  "direction_distribution": {},
  "rainfall_statistics": {},
  "sailing_categories": {},
  "lost_day_statistics": {}
}
```

All exports must include the source/model and the selected thresholds.

## 11. Non-functional requirements

- A twelve-location, seven-day, 20-year request must finish in a reasonable time
  on a standard workstation; cached repeats should not contact the provider.
- Network calls use explicit timeouts and actionable errors.
- The application must not make live network calls in tests. Use `httpx`
  `MockTransport` fixtures, following existing weather tests.
- Unit conversions, aggregation, thresholds, date validation, cache behavior,
  and export formats require focused tests.
- Existing voyage, leg, profile, weather-enrichment, and forecast behaviour
  must remain unchanged.
- The source attribution and reanalysis disclaimer must appear in the page and
  exports.

## 12. Acceptance criteria

1. The shared navigation exposes a Weather Analysis page independent of voyages.
2. A user can add, edit, and remove locations without changing or deleting
   historical analysis results.
3. A user can analyze the first October 24--30 scenario or any valid recurring
   date window, including a Saturday-to-Saturday charter week, for at least one
   configured location in any initial region.
4. The Open-Meteo request selects `models=era5`, `cell_selection=sea`, the
   selected location's IANA timezone, knots, and all required hourly fields.
5. The result contains daily metrics, ratings, lost-day probability, wind and
   direction distributions, and rainfall statistics.
6. When multiple locations are selected, the result provides a sortable,
   like-for-like destination comparison and a qualified suitability summary.
7. A repeated equivalent request reads cached historical data without an
   additional provider call.
8. CSV and JSON exports contain normalized values, analysis metadata, source,
   model, and thresholds.
9. Results display the Open-Meteo/ERA5 attribution and the reanalysis
   disclaimer.
10. The full existing test suite and new deterministic feature tests pass without
   accessing the network.
