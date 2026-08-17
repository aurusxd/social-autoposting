from aiogram.fsm.state import State, StatesGroup


class BotFlow(StatesGroup):
    collecting = State()
    selecting_targets = State()
    reviewing = State()
