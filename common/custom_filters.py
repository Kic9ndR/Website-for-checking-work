from dateutil import tz

def datetimeformat(value, format="%d.%m.%Y %H:%M"):
    if value is None:
        return ""
    return value.astimezone(tz.gettz('Europe/Moscow')).strftime(format)