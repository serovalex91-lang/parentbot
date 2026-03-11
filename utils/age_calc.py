from dataclasses import dataclass
from datetime import date
from typing import Optional
import dateutil.parser


@dataclass
class AgeResult:
    months: int
    days_total: int
    years: int
    remaining_months: int
    remaining_days: int
    display: str      # "1 год 4 месяца" или "15 дней"
    context: str      # "период активного освоения речи"


def calculate_age(birthdate_str: str) -> Optional[AgeResult]:
    """Вычислить возраст ребёнка по дате рождения."""
    try:
        birth = dateutil.parser.parse(birthdate_str).date()
    except Exception:
        return None

    today = date.today()
    if birth > today:
        return None

    # Точный расчёт в днях
    days_total = (today - birth).days

    # Расчёт в месяцах с учётом дня
    months_total = (today.year - birth.year) * 12 + (today.month - birth.month)
    if today.day < birth.day:
        months_total -= 1
    months_total = max(0, months_total)

    # Оставшиеся дни после полных месяцев
    if months_total > 0:
        # Дата начала текущего неполного месяца
        m = birth.month + months_total
        y = birth.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        try:
            last_month_date = date(y, m, birth.day)
        except ValueError:
            # Если день не существует (например 31 в коротком месяце)
            import calendar
            last_day = calendar.monthrange(y, m)[1]
            last_month_date = date(y, m, min(birth.day, last_day))
        remaining_days = (today - last_month_date).days
    else:
        remaining_days = days_total

    years = months_total // 12
    remaining_months = months_total % 12

    display = _format_age(years, remaining_months, remaining_days, days_total)
    context = _age_context(months_total)

    return AgeResult(
        months=months_total,
        days_total=days_total,
        years=years,
        remaining_months=remaining_months,
        remaining_days=remaining_days,
        display=display,
        context=context,
    )


def parse_birthdate(text: str) -> Optional[str]:
    """Распарсить дату рождения из текста пользователя → ISO формат YYYY-MM-DD."""
    text = text.strip().replace(".", "-").replace("/", "-")
    for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y"]:
        try:
            from datetime import datetime
            dt = datetime.strptime(text, fmt).date()
            return _validate_birthdate(dt)
        except ValueError:
            continue
    try:
        dt = dateutil.parser.parse(text, dayfirst=True).date()
        return _validate_birthdate(dt)
    except Exception:
        return None


def _validate_birthdate(dt: date) -> Optional[str]:
    today = date.today()
    if dt > today:
        return None
    # Проверяем возраст 0-18 лет по дням (точнее чем по месяцам)
    days = (today - dt).days
    if days < 0 or days > 18 * 365 + 5:  # +5 на високосные
        return None
    return dt.isoformat()


def _format_age(years: int, months: int, days: int, total_days: int) -> str:
    # Менее месяца — показываем дни
    if years == 0 and months == 0:
        if total_days == 0:
            return "новорождённый"
        if total_days == 1:
            return "1 день"
        return f"{total_days} {_plural(total_days, 'день', 'дня', 'дней')}"

    parts = []
    if years > 0:
        parts.append(f"{years} {_plural(years, 'год', 'года', 'лет')}")
    if months > 0:
        parts.append(f"{months} {_plural(months, 'месяц', 'месяца', 'месяцев')}")

    # Для детей до 3 месяцев — показываем и дни
    if years == 0 and months < 3 and days > 0:
        parts.append(f"{days} {_plural(days, 'день', 'дня', 'дней')}")

    return " ".join(parts) if parts else "новорождённый"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def _age_context(months: int) -> str:
    """Краткое описание этапа развития для промта."""
    if months < 1:
        return "период новорождённости — адаптация к внешнему миру"
    if months < 3:
        return "период новорождённости — сенсорное освоение мира"
    if months < 6:
        return "период первых улыбок и социального контакта"
    if months < 9:
        return "период активного моторного развития и познания"
    if months < 12:
        return "период подготовки к первым шагам и словам"
    if months < 18:
        return "период первых шагов и активного освоения пространства"
    if months < 24:
        return "период «нет» и первых слов"
    if months < 36:
        return "период покорения мира и формирования «тайной опоры»"
    if months < 48:
        return "период «почему» и активного познания"
    if months < 60:
        return "период фантазии и ролевых игр"
    if months < 72:
        return "период подготовки к школе"
    if months < 96:
        return "младший школьный возраст"
    if months < 144:
        return "подростковый возраст — формирование идентичности"
    return "старший подростковый возраст — сепарация и самоопределение"
