from datetime import datetime
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def _datetimeformat(value: str, fmt: str = "%A") -> str:
    """Format a YYYY-MM-DD date string with strftime."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime(fmt)
    except (ValueError, TypeError):
        return value


templates.env.filters["datetimeformat"] = _datetimeformat
