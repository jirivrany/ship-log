"""PDF export smoke tests: real render, assertions on the extracted text layer.

Model objects are built directly (no DB); a leg without a track exercises
the no-map path, so no tile fetching happens anywhere in these tests.
"""
import io
from datetime import datetime

from pypdf import PdfReader

from app.export import export_filename, leg_context, render_leg_pdf, render_voyage_pdf
from app.models import EntrySource, Leg, LogEntry, PropulsionType, Voyage


def _voyage() -> Voyage:
    return Voyage(
        id=1, name="Chorvatsko — Namaste", boat_name="Namaste",
        boat_maker="Bavaria", boat_model="Cruiser 46", year_built=2018,
        registration_number="ZD 1234", home_port="Sukošan",
        skipper="Jiří", crew="Petra, Tomáš",
        length_m=14.27, fuel_tank_l=210,
        start_date="2026-06-20", end_date="2026-06-27",
    )


def _sailed_leg() -> tuple[Leg, list[LogEntry]]:
    leg = Leg(
        id=1, voyage_id=1, from_port="Sukošan", to_port="Ždrelac",
        date="2026-06-20", timezone="Europe/Zagreb",
        forecast="Morning NW 3-4 Bf, afternoon W 4 Bf",
        warnings="none", synoptic_situation="Ridge over the Adriatic",
        sunrise="05:15", sunset="20:45",
    )
    entries = [
        LogEntry(id=1, leg_id=1, timestamp=datetime(2026, 6, 20, 15, 0),
                 lat=44.05, lon=15.30, source=EntrySource.manual,
                 propulsion=PropulsionType.motor, log_value=0.0, speed=4.2),
        LogEntry(id=2, leg_id=1, timestamp=datetime(2026, 6, 20, 16, 0),
                 lat=44.02, lon=15.28, source=EntrySource.turning_point,
                 propulsion=PropulsionType.sail, sails="Main R1 + Genoa",
                 log_value=4.0, speed=5.5, wind_direction="NW", wind_force=4),
        LogEntry(id=3, leg_id=1, timestamp=datetime(2026, 6, 20, 18, 0),
                 lat=43.99, lon=15.25, source=EntrySource.manual,
                 propulsion=PropulsionType.motor, log_value=13.0,
                 notes="anchored in Ždrelac cove"),
    ]
    return leg, entries


def _empty_leg() -> Leg:
    return Leg(id=2, voyage_id=1, from_port="Ždrelac", to_port="Kornati",
               date="2026-06-21", timezone="Europe/Zagreb")


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def test_voyage_pdf_has_cover_day_pages_and_totals():
    voyage = _voyage()
    leg, entries = _sailed_leg()
    contexts = [leg_context(leg, entries), leg_context(_empty_leg(), [])]

    pdf = render_voyage_pdf(voyage, contexts)

    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 3  # cover + two day pages
    text = _text(pdf)
    # cover
    assert "Chorvatsko — Namaste" in text
    # boat label may wrap across lines in the extracted text layer
    assert "Bavaria" in text and "Cruiser 46 (2018)" in text
    assert "ZD 1234" in text
    # pypdf sometimes splits glyph runs ("T omáš") — compare without spaces
    assert "Petra,Tomáš" in text.replace(" ", "")
    # day page: entries in leg-local time (15:00 UTC -> 17:00 CEST)
    assert "Sukošan → Ždrelac" in text
    assert "17:00" in text
    assert "Main R1 + Genoa" in text
    assert "Morning NW 3-4 Bf" in text
    assert "05:15" in text and "20:45" in text
    assert "anchored in Ždrelac cove" in text
    # totals: 4 Nm motor + 9 Nm sail = 13 Nm
    assert "13.0" in text and "9.0" in text and "4.0" in text
    # the empty manual leg still renders its page
    assert "Ždrelac → Kornati" in text
    assert "No log entries" in text


def test_leg_pdf_contains_only_that_leg():
    voyage = _voyage()
    leg, entries = _sailed_leg()

    pdf = render_leg_pdf(voyage, leg_context(leg, entries))

    text = _text(pdf)
    assert "Sukošan → Ždrelac" in text
    assert "Kornati" not in text
    assert "exported" in text


def test_no_track_means_no_map_and_no_network():
    # leg_context must not attempt any tile fetch for a track-less leg —
    # passing no client would otherwise hit the network
    context = leg_context(_empty_leg(), [])
    assert context["track_map_uri"] is None
    assert context["synoptic_chart_uri"] is None


def test_synoptic_chart_embeds_as_data_uri(tmp_path):
    chart = tmp_path / "synoptic_2026-06-20.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    leg, entries = _sailed_leg()
    leg.synoptic_chart_path = str(chart)

    context = leg_context(leg, entries)

    assert context["synoptic_chart_uri"].startswith("data:image/png;base64,")


def test_export_filename_is_ascii_slug():
    voyage = _voyage()
    leg, _ = _sailed_leg()
    assert export_filename(voyage, leg) == "chorvatsko-namaste_2026-06-20.pdf"
    # voyage export is stamped with today's date
    assert export_filename(voyage).endswith(".pdf")
    assert export_filename(voyage).startswith("chorvatsko-namaste_")
