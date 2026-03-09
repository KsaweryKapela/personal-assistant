from datetime import datetime


def friendly_datetime(date_str: str, time_str: str) -> str:
    """Return a human-readable date/time string, e.g. 'Thursday, March 9 at 15:00'."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.strftime("%A, %B %-d at %H:%M")
    except ValueError:
        return f"{date_str} at {time_str}"
