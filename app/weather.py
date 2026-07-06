"""Historical weather for log entries: unit derivations + Open-Meteo client."""

# WMO Beaufort scale: inclusive upper wind-speed bound (knots) per force 0-11,
# applied to the speed rounded to whole knots; >= 64 kn is force 12.
_BEAUFORT_MAX_KN = [0, 3, 6, 10, 16, 21, 27, 33, 40, 47, 55, 63]


def knots_to_beaufort(knots: float) -> int:
    kn = round(knots)
    for force, upper in enumerate(_BEAUFORT_MAX_KN):
        if kn <= upper:
            return force
    return 12
