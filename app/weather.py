"""Historical weather for log entries: unit derivations + Open-Meteo client."""

# WMO Beaufort scale: exclusive upper wind-speed bound (knots) per force 0-11
# (force 4 is 11-16 kn, i.e. [11, 17)); >= 64 kn is force 12.
_BEAUFORT_UPPER_KN = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]


def knots_to_beaufort(knots: float) -> int:
    for force, upper in enumerate(_BEAUFORT_UPPER_KN):
        if knots < upper:
            return force
    return 12
