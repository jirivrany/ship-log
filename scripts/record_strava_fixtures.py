"""Record real Strava API responses as test fixtures.

Run once after the OAuth flow works (inside the app container, where the
tokens file lives):

    docker compose exec app python scripts/record_strava_fixtures.py

Fetches the most recent sailing activity and writes:
    tests/fixtures/strava_activities.json   (one page of /athlete/activities)
    tests/fixtures/strava_bundle.json       (activity + streams + laps)

The Strava-related tests skip themselves until these files exist.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import strava_api  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tests", "fixtures")


def main() -> int:
    activities = strava_api._api_get("/athlete/activities", {"per_page": 50})
    sail = [a for a in activities if a.get("sport_type") == "Sail"]
    if not sail:
        print("No sailing activities found in the last 50 — nothing recorded.")
        return 1

    os.makedirs(FIXTURES_DIR, exist_ok=True)
    with open(os.path.join(FIXTURES_DIR, "strava_activities.json"), "w") as f:
        json.dump(activities, f, indent=2)

    newest = sail[0]
    print(f"Recording bundle for: {newest['name']} ({newest['start_date']})")
    bundle = strava_api.fetch_activity_bundle(newest["id"])
    with open(os.path.join(FIXTURES_DIR, "strava_bundle.json"), "w") as f:
        json.dump(bundle, f, indent=2)

    print(f"Wrote fixtures to {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
