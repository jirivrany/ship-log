"""Historical weather for log entries: unit derivations + Open-Meteo client."""

# WMO Beaufort scale: upper wind-speed bound (knots) per force 0-11; >= last is 12.
_BEAUFORT_MAX_KN = [1, 3, 6, 10, 16, 21, 27, 33, 40, 47, 55, 63]


def knots_to_beaufort(knots: float) -> int:
    for force, upper in enumerate(_BEAUFORT_MAX_KN):
        if knots < upper:
            return force
    return 12
