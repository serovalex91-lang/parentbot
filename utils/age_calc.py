from dataclasses import dataclass
from datetime import date
from typing import Optional
import dateutil.parser


@dataclass
class AgeResult:
    months: int
    years: int
    remaining_months: int
    display: str      # "1 год 4 месяца"
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

    months_total = (today.year - birth.year) * 12 + (today.month - birth.month)
    if today.day < birth.day:
        months_total -= 1
    months_total = max(0, months_total)

    years = months_total // 12
    remaining_months = months_total % 12

    display = _format_age(years, remaining_months)
    context = _age_context(months_total)

    return AgeResult(
        months=months_total,
        years=years,
        remaining_months=remaining_months,
        display=display,
        context=context,
    )


def parse_birthdate(text: str) -> Optional[str]:
    """Распарсить дату рождения из текста пользователя → ISO формат YYYY-MM-DD.
    Валидирует: дата в прошлом, возраст 0–18 лет."""
    text = text.strip().replace(".", "-").replace("/", "-")
    # Попытаться стандартные форматы
    for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y"]:
        try:
            from datetime import datetime
            dt = datetime.strptime(text, fmt).date()
            return _validate_birthdate(dt)
        except ValueError:
            continue
    # dateutil как fallback
    try:
        dt = dateutil.parser.parse(text, dayfirst=True).date()
        return _validate_birthdate(dt)
    except Exception:
        return None


def _validate_birthdate(dt: date) -> Optional[str]:
    today = date.today()
    if dt > today:
        return None
    months_total = (today.year - dt.year) * 12 + (today.month - dt.month)
    if months_total < 0 or months_total > 18 * 12:
        return None
    return dt.isoformat()


def _format_age(years: int, months: int) -> str:
    parts = []
    if years > 0:
        parts.append(f"{years} {_plural(years, 'год', 'года', 'лет')}")
    if months > 0 or years == 0:
        parts.append(f"{months} {_plural(months, 'месяц', 'месяца', 'месяцев')}")
    return " ".join(parts) if parts else "0 месяцев"


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
    return "школьный возраст"
