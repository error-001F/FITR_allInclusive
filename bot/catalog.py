"""Справочники и цены. Меняйте значения здесь — прайс и кнопки в боте обновятся сами."""
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

# Время в БД хранится в UTC; клиенту показываем в этом часовом поясе.
LOCAL_TIMEZONE = ZoneInfo("Europe/Minsk")

# Статус заказа в БД → как его видит клиент. Статусы, кроме "new", выставляет мастер.
ORDER_STATUS_TITLES = {
    "new": "🆕 Принят, ждёт оценки мастера",
    "awaiting_payment": "💳 Ожидает оплаты",
    "printing": "🖨 Печатается",
    "ready": "✅ Готов к выдаче",
    "rejected": "❌ Отклонён",
}

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
    "0.28": Decimal("0.30"),
    "0.20": Decimal("0.30"),
    "0.16": Decimal("0.37"),
    "0.12": Decimal("0.51"),
    "0.08": Decimal("0.67"),
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

DELIVERY_METHODS = ["самовывоз", "европочта", "белпочта"]
# Показывается в прайсе; пустая строка — адрес не выводится.
PICKUP_ADDRESS = "Сурганова 37/2(общежитие 12)"


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
