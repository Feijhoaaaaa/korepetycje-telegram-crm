from aiogram.fsm.state import State, StatesGroup


class LessonFlow(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_confirmation = State()
    waiting_for_conflict_confirmation = State()


class StudentFlow(StatesGroup):
    waiting_for_reassignment = State()


class PaymentFlow(StatesGroup):
    choosing_currency = State()
    waiting_for_amount = State()
    waiting_for_file = State()
