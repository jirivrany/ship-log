"""Weather derivations and Open-Meteo client (PRD: weather enrichment)."""
from app.weather import (
    cloud_pct_to_oktas,
    degrees_to_sector,
    knots_to_beaufort,
    wave_height_to_douglas,
)


def test_moderate_breeze_is_4_bft():
    # WMO: 11-16 kn = force 4
    assert knots_to_beaufort(14.2) == 4


def test_beaufort_wmo_boundaries():
    # WMO ranges in knots: 0:<1, 1:1-3, 2:4-6, 3:7-10, 4:11-16, 5:17-21,
    # 6:22-27, 7:28-33, 8:34-40, 9:41-47, 10:48-55, 11:56-63, 12:>=64.
    assert knots_to_beaufort(0.0) == 0
    assert knots_to_beaufort(0.9) == 0
    assert knots_to_beaufort(1.0) == 1
    assert knots_to_beaufort(3.0) == 1
    assert knots_to_beaufort(4.0) == 2
    assert knots_to_beaufort(16.0) == 4    # top of force 4 stays 4
    assert knots_to_beaufort(16.4) == 4    # rounds to 16
    assert knots_to_beaufort(17.0) == 5
    assert knots_to_beaufort(63.0) == 11
    assert knots_to_beaufort(64.0) == 12
    assert knots_to_beaufort(120.0) == 12  # hurricane caps at 12


def test_cardinal_sectors_16_point():
    assert degrees_to_sector(0) == "N"
    assert degrees_to_sector(225) == "SW"
    assert degrees_to_sector(202.5) == "SSW"
    assert degrees_to_sector(45) == "NE"


def test_sector_boundaries_and_wrap():
    # Each sector is 22.5 deg wide, centred on its heading: N covers [348.75, 11.25).
    assert degrees_to_sector(11.24) == "N"
    assert degrees_to_sector(11.25) == "NNE"
    assert degrees_to_sector(348.75) == "N"
    assert degrees_to_sector(348.74) == "NNW"
    assert degrees_to_sector(360) == "N"


def test_cloud_pct_to_oktas():
    assert cloud_pct_to_oktas(0) == 0
    assert cloud_pct_to_oktas(100) == 8
    assert cloud_pct_to_oktas(50) == 4
    assert cloud_pct_to_oktas(15) == 1
    assert cloud_pct_to_oktas(19) == 2   # 1.52 rounds up


def test_wave_height_to_douglas():
    # Douglas scale by significant wave height (m), lower bound inclusive:
    # 0: 0, 1: (0-0.1], 2: (0.1-0.5], 3: (0.5-1.25], 4: (1.25-2.5],
    # 5: (2.5-4], 6: (4-6], 7: (6-9], 8: (9-14], 9: >14
    assert wave_height_to_douglas(0.0) == 0
    assert wave_height_to_douglas(0.05) == 1
    assert wave_height_to_douglas(0.3) == 2
    assert wave_height_to_douglas(0.5) == 2
    assert wave_height_to_douglas(1.0) == 3
    assert wave_height_to_douglas(2.5) == 4
    assert wave_height_to_douglas(3.0) == 5
    assert wave_height_to_douglas(5.0) == 6
    assert wave_height_to_douglas(8.0) == 7
    assert wave_height_to_douglas(10.0) == 8
    assert wave_height_to_douglas(15.0) == 9
