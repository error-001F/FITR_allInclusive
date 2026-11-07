"""Справочники и цены. Меняйте значения здесь — прайс и кнопки в боте обновятся сами."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

# Описание бота на экране «Что умеет этот бот?» — его видят до нажатия «Старт».
# Бот выставляет его при запуске. Лимит Telegram — 512 символов, без форматирования.
BOT_DESCRIPTION = "Жми «Старт» 👇"

# Время в БД хранится в UTC; клиенту показываем в этом часовом поясе.
LOCAL_TIMEZONE = ZoneInfo("Europe/Minsk")

# Статус заказа в БД → как его видит клиент. Статусы, кроме "new", выставляет мастер.
ORDER_STATUS_TITLES = {
    "new": "🆕 Принят, ждёт оценки мастера",
    "awaiting_payment": "💳 Ожидает оплаты",
    "payment_check": "🔎 Оплата на проверке",
    "paid": "💰 Оплачен",
    "printing": "🖨 Печатается",
    "ready": "✅ Готов к выдаче",
    "rejected": "❌ Отклонён",
}

# Способ оплаты (ключ хранится в БД) → как его видит клиент. Реквизиты карты и IBAN — в .env;
# способ без реквизитов клиенту не предлагается.
PAYMENT_METHOD_TITLES = {
    "card": "💳 Картой",
    "iban": "🏦 По IBAN",
    "cash": "💵 Наличными при получении",
}
CASH = "cash"

# Сколько хранить отклонённые заказы, и как часто бот проверяет, не пора ли их удалить.
REJECTED_ORDER_TTL = timedelta(days=1)
CLEANUP_INTERVAL = timedelta(hours=1)

MAX_PRINT_SIZE_MM = (250, 250, 250)
DESIGN_PRICE_FROM = Decimal("18")

PRINT_EXTENSIONS = (".stl", ".step", ".stp")

MATERIALS = ["PETG", "PLA"]
COLORS = ["Чёрный", "Белый", "Серый"]

# Толщина слоя (мм) → цена за грамм пластика, BYN.
LAYER_PRICES = {
    "0.28": Decimal("0.20"),
    "0.20": Decimal("0.20"),
    "0.16": Decimal("0.27"),
    "0.12": Decimal("0.41"),
    "0.08": Decimal("0.57"),
}

# Пояснения для клиентов, которые не разбираются в 3D-печати (показываются курсивом на шаге выбора).
LAYER_HINT = (
    "Толщина слоя — насколько тонкими слоями печатается модель. Чем тоньше слой, "
    "тем глаже поверхность и точнее мелкие детали, но печать дольше и дороже.\n"
)

INFILL_HINT = (
    "Заполнение — насколько плотно модель заполнена пластиком внутри "
    "(снаружи она всегда сплошная). Чем более надежная деталь нужна, тем выше заполнение "
    "следует выбрать. Но помните что она станет тяжелее.\n"
)

# Плотность заполнения (%) → наценка к итоговой сумме, BYN.
INFILL_SURCHARGES = {
    10: Decimal("0"),
    20: Decimal("0"),
    40: Decimal("2.47"),
    60: Decimal("4.94"),
    80: Decimal("7.41"),
    100: Decimal("9.88"),
}

# Расчёт цены печати: вес из слайсера × цена за грамм (по слою) + наценка за заполнение.
MAX_WEIGHT_GRAMS = Decimal("5000")  # стол 250×250×250 мм — тяжелее практически не бывает
PRICE_ROUNDING = Decimal("0.1")  # итог округляется вверх до 10 копеек


@dataclass(frozen=True)
class PriceCalc:
    weight: Decimal
    price_per_gram: Decimal
    print_cost: Decimal  # вес × цена за грамм
    surcharge: Decimal  # наценка за заполнение
    exact: Decimal  # сумма до округления
    total: Decimal  # итог, округлённый вверх до 10 копеек


def calculate_print_price(weight: Decimal, layer_height: str, infill: int) -> PriceCalc:
    price_per_gram = LAYER_PRICES[layer_height]
    print_cost = (weight * price_per_gram).quantize(Decimal("0.01"))
    surcharge = INFILL_SURCHARGES[infill]
    exact = print_cost + surcharge
    total = exact.quantize(PRICE_ROUNDING, rounding=ROUND_CEILING).quantize(Decimal("0.01"))
    return PriceCalc(weight, price_per_gram, print_cost, surcharge, exact, total)


def round_price(amount: Decimal) -> Decimal:
    """Округление итога вверх до 10 копеек (как в calculate_print_price)."""
    return amount.quantize(PRICE_ROUNDING, rounding=ROUND_CEILING).quantize(Decimal("0.01"))


# --- Промокоды с подарочных сертификатов ---
#
# Впишите коды в нужный список — каждый в кавычках, через запятую, по одному в строке.
# Каждый код работает ОДИН раз: использованные бот хранит в БД (таблица promo_uses),
# удалять код отсюда после использования не нужно. Если мастер отклонит
# заказ, код снова начнёт действовать.
# Регистр, пробелы и дефисы при вводе не важны: клиент может ввести «fitr 7k2q 9xmd».
# Делайте коды длинными и случайными (например, FITR-7K2Q-9XMD), чтобы их нельзя было угадать.
# Длина кода без пробелов и дефисов — не больше 32 символов.

# «Печать до 200 г бесплатно»: свыше лимита клиент платит за лишние граммы + наценку за заполнение.
FREE_PROMO_CODES: list[str] = [
    # "FITR-FREE-0001",
]
# «Скидка 50%» на всю сумму счёта.
HALF_PROMO_CODES: list[str] = [
    # "FITR-HALF-0001",
]

PROMO_FREE = "free"
PROMO_HALF = "half"
FREE_PROMO_GRAMS = Decimal("200")
HALF_PROMO_DISCOUNT = Decimal("0.5")  # доля, которую платит клиент
PROMO_TITLES = {
    PROMO_FREE: f"🎟 печать до {FREE_PROMO_GRAMS} г бесплатно",
    PROMO_HALF: "🎟 скидка 50%",
}
# Защита от подбора: столько неверных кодов за час, потом ввод временно блокируется.
PROMO_MAX_ATTEMPTS = 100
PROMO_ATTEMPTS_WINDOW = timedelta(hours=1)


def normalize_promo(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch not in " -\t\n")


def promo_kind(code: str) -> str | None:
    """Вид промокода (PROMO_FREE / PROMO_HALF) по нормализованному коду; None — такого нет."""
    if code in {normalize_promo(c) for c in FREE_PROMO_CODES}:
        return PROMO_FREE
    if code in {normalize_promo(c) for c in HALF_PROMO_CODES}:
        return PROMO_HALF
    return None


def apply_promo(kind: str | None, amount: Decimal, calc: PriceCalc | None = None) -> Decimal:
    """Сумма к оплате с учётом промокода.

    half — половина суммы. free — по расчёту веса: до FREE_PROMO_GRAMS бесплатно, свыше —
    лишние граммы по цене за грамм + наценка за заполнение. free без расчёта (разработка,
    своя сумма мастера) — сумму не меняем: мастер учитывает сертификат сам.
    """
    if kind == PROMO_HALF:
        return round_price(amount * HALF_PROMO_DISCOUNT)
    if kind == PROMO_FREE and calc is not None:
        if calc.weight <= FREE_PROMO_GRAMS:
            return Decimal("0.00")
        extra = ((calc.weight - FREE_PROMO_GRAMS) * calc.price_per_gram).quantize(Decimal("0.01"))
        return round_price(extra + calc.surcharge)
    return amount

DELIVERY_METHODS = ["самовывоз", "европочта"]
# Способ доставки, для которого клиент указывает адрес пункта выдачи.
PICKUP_POINT_METHOD = "европочта"
# Способ доставки, для которого адрес вписывает мастер (кнопка «📍 Адрес самовывоза»).
SELF_PICKUP_METHOD = "самовывоз"
# Показывается в прайсе; пустая строка — адрес не выводится.
PICKUP_ADDRESS = "Адрес договорной"


def delivery_text(method: str, address: str | None) -> str:
    """«Самовывоз, адрес: …» / «Европочта, ПВЗ: …» — без HTML, экранирует вызывающий."""
    text = delivery_title(method)
    if address:
        label = "адрес" if method == SELF_PICKUP_METHOD else "ПВЗ"
        text += f", {label}: {address}"
    return text


def delivery_title(method: str) -> str:
    # Не capitalize(): он переводит остальные буквы в нижний регистр.
    return f"{method[:1].upper()}{method[1:]}"


def price_info_text() -> str:
    size = " × ".join(str(side) for side in MAX_PRINT_SIZE_MM)
    layers = "\n".join(
        f"• слой {layer} мм — {price} BYN/г" for layer, price in LAYER_PRICES.items()
    )
    infills = "\n".join(
        f"• {infill}% — " + (f"+{surcharge} BYN" if surcharge else "без доплаты")
        for infill, surcharge in INFILL_SURCHARGES.items()
    )
    delivery = ", ".join(DELIVERY_METHODS)
    pickup = f"\nАдрес самовывоза: {PICKUP_ADDRESS}." if PICKUP_ADDRESS else ""
    return (
        "<b>Печать</b>\n"
        f"Максимальные габариты печати: {size} мм.\n"
        f"Материалы: {', '.join(MATERIALS)}.\n"
        "Стоимость считается за грамм пластика и зависит от толщины слоя:\n"
        f"{layers}\n\n"
        "Наценка за плотное заполнение:\n"
        f"{infills}\n\n"
        "<b>Разработка 3D-моделей</b>\n"
        f"От {DESIGN_PRICE_FROM} BYN, итоговая цена зависит от сложности.\n\n"
        "<b>Доставка</b>\n"
        # Не capitalize(): он переводит остальные буквы в нижний регистр («европочта»).
        f"{delivery[:1].upper()}{delivery[1:]}.{pickup}"
    )
