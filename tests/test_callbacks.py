from app.bot.callbacks import FlowCb, LessonCb, MenuCb


def test_callback_payloads_fit_telegram_limit() -> None:
    values = [
        MenuCb(action="export_month", value=9_223_372_036_854_775_807).pack(),
        LessonCb(action="reschedule", lesson_id=9_223_372_036_854_775_807).pack(),
        FlowCb(action="date", value="2026-09-24").pack(),
    ]
    assert all(len(value.encode()) <= 64 for value in values)
