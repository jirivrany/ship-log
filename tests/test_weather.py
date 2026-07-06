"""Weather derivations and Open-Meteo client (PRD: weather enrichment)."""
from app.weather import knots_to_beaufort


def test_moderate_breeze_is_4_bft():
    # WMO: 11-16 kn = force 4
    assert knots_to_beaufort(14.2) == 4
