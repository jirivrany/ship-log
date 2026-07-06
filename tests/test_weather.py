"""Weather derivations and Open-Meteo client (PRD: weather enrichment)."""
from app.weather import knots_to_beaufort


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
