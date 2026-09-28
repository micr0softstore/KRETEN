"""Present KRÉTA timestamps in the school's timezone, preserving calendar dates."""
from datetime import datetime
from zoneinfo import ZoneInfo

SCHOOL_TIMEZONE = ZoneInfo('Europe/Budapest')


def local_datetime(value):
    """Convert offset timestamps; date-only and naive local values stay local."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(SCHOOL_TIMEZONE) if parsed.tzinfo else parsed
    except (ValueError, TypeError):
        return None


def local_date_string(value):
    parsed = local_datetime(value)
    return parsed.date().isoformat() if parsed else ''
