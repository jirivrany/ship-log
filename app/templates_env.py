from datetime import datetime
from fastapi.templating import Jinja2Templates

from app.models import AREA_LABELS, AREA_ORDER

templates = Jinja2Templates(directory="app/templates")

# Navigation-area lookups used by the shared voyage form partial.
templates.env.globals["area_order"] = AREA_ORDER
templates.env.globals["area_labels"] = AREA_LABELS


def _datetimeformat(value: str, fmt: str = "%A") -> str:
    """Format a YYYY-MM-DD date string with strftime."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime(fmt)
    except (ValueError, TypeError):
        return value


templates.env.filters["datetimeformat"] = _datetimeformat
