from datetime import datetime

def datetimeformat(value, format='%d.%m.%Y %H:%M'):
    """
    Фильтр для форматирования даты и времени в шаблонах Jinja2.
    
    Args:
        value: Значение даты/времени для форматирования
        format: Строка формата (по умолчанию: '%d.%m.%Y %H:%M')
    
    Returns:
        str: Отформатированная строка даты/времени
    """
    if value is None:
        return ''
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime(format) 