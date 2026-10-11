from aiogram.filters.callback_data import CallbackData


class MenuCb(CallbackData, prefix="m"):
    action: str
    value: int = 0


class LessonCb(CallbackData, prefix="l"):
    action: str
    lesson_id: int
    value: int = 0


class FlowCb(CallbackData, prefix="f"):
    action: str
    value: str = "-"


class ConfirmCb(CallbackData, prefix="c"):
    action: str
    entity_id: int
