from aiogram.fsm.state import State, StatesGroup


class OrderForm(StatesGroup):
    # Ветка печати.
    waiting_file = State()
    choosing_material = State()
    choosing_layer = State()
    choosing_infill = State()
    choosing_color = State()
    # Ветка разработки.
    waiting_description = State()
    waiting_references = State()
    # Общие шаги.
    waiting_name = State()
    waiting_contact = State()
    confirming = State()
