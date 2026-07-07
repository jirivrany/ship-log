"""Curated boat library backing the voyage form's "Fill from library" button.

Data lives in boat_library.json next to this module: known hulls under
"boats", model-level specs under "models". Matching is diacritics- and
case-insensitive so "eirene" finds "Eirené". Extend the JSON with the
/add-boat skill (.claude/skills/add-boat).
"""
import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

LIBRARY_PATH = Path(__file__).parent / "boat_library.json"

# Voyage spec columns a model entry may prefill (must match models.Voyage)
SPEC_FIELDS = (
    "length_m", "beam_m", "draft_m", "air_draft_m", "engine_type",
    "engine_power_hp", "displacement_t", "max_persons", "sail_area_m2",
    "mainsail_m2", "genoa_m2", "water_tank_l", "fuel_tank_l",
)


def _norm(text: Optional[str]) -> str:
    """Casefold, strip diacritics and collapse whitespace for matching."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


@lru_cache(maxsize=1)
def _library() -> dict:
    with open(LIBRARY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _find_boat(name: Optional[str]) -> Optional[dict]:
    key = _norm(name)
    if not key:
        return None
    return next((b for b in _library()["boats"] if _norm(b["name"]) == key), None)


def _find_model(maker: Optional[str], model: Optional[str]) -> Optional[dict]:
    maker_key, model_key = _norm(maker), _norm(model)
    if not maker_key or not model_key:
        return None
    return next(
        (m for m in _library()["models"]
         if _norm(m["maker"]) == maker_key and _norm(m["model"]) == model_key),
        None,
    )


def lookup(name: Optional[str] = None, maker: Optional[str] = None,
           model: Optional[str] = None) -> Optional[dict]:
    """Match a known boat by name first, else a model by maker+model.

    Returns {"label", "fields", "note"} where "fields" maps voyage form
    field names to library values, or None when nothing matches.
    """
    boat = _find_boat(name)
    if boat:
        maker, model = boat["maker"], boat["model"]
    model_entry = _find_model(maker, model)
    if not boat and not model_entry:
        return None

    fields: dict = {}
    if boat:
        fields["boat_maker"] = boat["maker"]
        fields["boat_model"] = boat["model"]
        if boat.get("year_built"):
            fields["year_built"] = boat["year_built"]
    if model_entry:
        for field in SPEC_FIELDS:
            if model_entry.get(field) is not None:
                fields[field] = model_entry[field]

    label = f"{maker} {model}" if model_entry else f"{boat['maker']} {boat['model']}"
    if boat:
        label = f"{boat['name']} — {label}"
        if boat.get("year_built"):
            label += f" ({boat['year_built']})"
    note = "; ".join(
        n for n in ((boat or {}).get("note"), (model_entry or {}).get("notes")) if n
    )
    return {"label": label, "fields": fields, "note": note}
