# Product Requirements Document (PRD)

## Project Title

**Historical Sailing Conditions Analyzer for the Croatian Adriatic**

---

## Purpose

Develop a Python-based data analysis tool that evaluates historical sailing conditions in the Croatian Adriatic during a specified date range and geographical area.

The primary use case is to help sailing skippers and charter crews assess the historical probability of favorable, challenging, or unsuitable sailing conditions for a planned voyage.

Initial focus:

* Region: Zadar Archipelago, Kornati Islands, Šibenik area
* Optional extension: Split, Brač, Hvar
* Date range: October 24–30
* Historical period: last 10–20 years

The system shall collect historical weather data, classify sailing conditions, generate statistical summaries, and produce outputs suitable for further interpretation by humans or AI assistants.

---

# Goals

The system shall answer the following questions:

### Wind Conditions

* How often do light-wind conditions occur?
* How often can crews expect good sailing wind?
* How often do strong or potentially dangerous wind conditions occur?

### Wind Direction

* What are the dominant wind directions?
* How frequently do Bora-like (NE) conditions occur?
* How frequently do Jugo-like (SE) conditions occur?

### Rainfall

* How often does significant rainfall occur?
* What is the probability of one or more rainy days during the selected week?
* How frequently do multi-day rainfall events occur?

### Sailing Suitability

* How many days are typically suitable for normal charter sailing?
* How many days are considered sporty or demanding?
* How many days are likely to cause itinerary disruption or require staying in a marina?

---

# Scope

## Included

* Historical weather data acquisition
* Data normalization
* Wind speed analysis
* Wind direction analysis
* Rainfall analysis
* Sailing condition classification
* Statistical summaries
* CSV export
* JSON export
* Graph generation

## Excluded

* Weather forecasting
* Route optimization
* Marina recommendations
* Real-time weather monitoring
* Vessel-specific performance calculations

---

# Data Sources

## Preferred Source

ERA5 Reanalysis Dataset

Required variables:

* 10 m U wind component
* 10 m V wind component
* Total precipitation
* Mean sea level pressure (optional)

Suggested provider:

Copernicus Climate Data Store (CDS)

---

## Alternative Source

Open-Meteo Historical API

Required variables:

* Wind speed
* Wind direction
* Precipitation

The implementation should allow future replacement of the weather data provider without major changes to downstream processing.

---

# Geographic Coverage

The system shall support analysis of multiple predefined locations.

Initial configuration:

```python
LOCATIONS = {
    "Murter": (43.82, 15.60),
    "Kornati": (43.78, 15.30),
    "Dugi_Otok": (43.90, 15.05),
    "Zadar_Channel": (44.10, 15.25),
    "Sibenik_Channel": (43.70, 15.75),
}
```

The location list must be configurable.

---

# Functional Requirements

## FR-1 Data Collection

The system shall download historical weather observations for:

* configurable locations
* configurable date range
* configurable historical year range

Example:

* Locations: Kornati, Murter
* Date range: October 24–30
* Years: 2010–2025

---

## FR-2 Wind Speed Calculation

The system shall calculate wind speed from U/V components when required.

Output unit:

* knots (kn)

---

## FR-3 Wind Direction Calculation

The system shall calculate wind direction in degrees.

Additionally, directions shall be grouped into sectors:

* N
* NE
* E
* SE
* S
* SW
* W
* NW

---

## FR-4 Daily Aggregation

Hourly observations shall be aggregated into daily summaries.

Daily metrics:

* average wind speed
* maximum wind speed
* maximum gust (if available)
* dominant wind direction
* total precipitation
* sailing condition category

---

## FR-5 Sailing Condition Classification

Each day shall be classified into one of three categories.

### Green

Suitable for normal charter sailing.

Suggested criteria:

* wind speed between 5 and 20 kn
* daily precipitation less than 5 mm

### Yellow

Demanding or sporty sailing conditions.

Suggested criteria:

* wind speed between 20 and 30 kn

OR

* precipitation greater than 5 mm

### Red

Conditions likely to disrupt normal charter operations.

Suggested criteria:

* wind speed greater than 30 kn

OR

* wind gusts greater than 40 kn

OR

* precipitation greater than 30 mm

Classification thresholds must be configurable.

---

## FR-6 Lost Day Detection

The system shall identify days likely to cause significant itinerary disruption.

Example criteria:

```python
lost_day = (
    max_wind_kn > 30
    or daily_precipitation_mm > 20
)
```

The system shall calculate:

* number of lost days per analyzed season
* average lost days per selected week
* probability of at least one lost day

---

## FR-7 Statistical Analysis

The system shall calculate:

### Wind Distribution

Bins:

* <10 kn
* 10–15 kn
* 15–20 kn
* 20–25 kn
* 25–30 kn
* > 30 kn

### Direction Distribution

Percentage occurrence of:

* N
* NE
* E
* SE
* S
* SW
* W
* NW

### Rainfall Statistics

* rainy days
* heavy-rain days
* consecutive rainy-day events

### Sailing Categories

Distribution of:

* Green
* Yellow
* Red

---

# Output Requirements

## CSV Export

### Hourly Dataset

Columns:

* timestamp
* location
* wind_speed_kn
* wind_direction_deg
* precipitation_mm
* pressure_hpa

---

### Daily Summary Dataset

Columns:

* date
* location
* avg_wind_kn
* max_wind_kn
* max_gust_kn
* rain_total_mm
* dominant_direction
* sailing_rating

---

## JSON Export

Statistical summary structure:

```json
{
  "wind_bins": {},
  "direction_distribution": {},
  "rainfall_statistics": {},
  "sailing_categories": {},
  "lost_day_statistics": {}
}
```

---

# Visualization Requirements

The system shall generate:

## Wind Rose

Visual representation of wind direction frequency.

## Wind Speed Histogram

Distribution of wind speeds.

## Rainfall Histogram

Distribution of precipitation totals.

## Sailing Category Chart

Green / Yellow / Red day distribution.

## Lost Day Summary

Probability and frequency of disrupted sailing days.

---

# Non-Functional Requirements

## Performance

The system should process:

* 20 years of data
* 5 locations
* hourly resolution

within a reasonable execution time on a standard workstation.

---

## Extensibility

The architecture shall support:

* additional locations
* alternative weather providers
* new sailing condition models
* different seasonal analyses

without major redesign.

---

# Expected Deliverables

1. Python application
2. Configuration file
3. Data acquisition module
4. Statistical analysis module
5. Visualization module
6. CSV exports
7. JSON summary exports
8. Documentation and usage examples

---

# Success Criteria

The project is successful if a skipper can answer the following question from the generated outputs:

> "Based on the last 10–20 years of historical weather data, what is the probability that a sailing trip in the Kornati and Zadar region between October 24 and October 30 will experience favorable, sporty, or disruptive sailing conditions?"
