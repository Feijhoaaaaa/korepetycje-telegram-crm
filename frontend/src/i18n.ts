export type UiLanguage = 'pl' | 'ru'

export function resolveUiLanguage(languageCode?: string): UiLanguage { return languageCode?.toLowerCase().startsWith('ru') ? 'ru' : 'pl' }

const telegramLanguage = typeof window === 'undefined' ? undefined : window.Telegram?.WebApp.initDataUnsafe?.user?.language_code
export const uiLanguage = resolveUiLanguage(telegramLanguage)
export const uiLocale = uiLanguage === 'ru' ? 'ru-RU' : 'pl-PL'

const ru: Record<string, string> = {
  loading: 'Загрузка…', wait: 'Подождите', close: 'Закрыть', cancel: 'Отмена', save: 'Сохранить', edit: 'Редактировать', delete: 'Удалить навсегда', add: 'Добавить', more: 'Ещё',
  plan: 'Расписание', calendar: 'Календарь', students: 'Ученики', teachers: 'Преподаватели', requests: 'Заявки', payments: 'Оплаты', reports: 'Отчёты', settings: 'Настройки', availability: 'Доступность', account: 'Счёт', menu: 'Меню',
  freeDay: 'Свободный день', noName: 'Без имени', noTeacher: 'Без преподавателя', noPrice: 'Цена не указана', planned: 'Запланировано', completed: 'Проведено', cancelled: 'Отменено', paid: 'Оплачено', unpaid: 'Не оплачено', unknown: 'Не указано',
  allLessons: 'Все занятия', month: 'Месяц', sendExcel: 'Отправить Excel', revenue: 'Выручка', debt: 'Задолженность', noLessonsMonth: 'В этом месяце занятий нет', date: 'Дата', time: 'Время', student: 'Ученик', teacher: 'Преподаватель', price: 'Цена', duration: 'Продолжительность', minutes: 'мин',
  addLesson: 'Добавить занятие', newEvent: 'Новое событие', saveLesson: 'Сохранить занятие', saving: 'Сохранение…', lessonDetails: 'Детали занятия', payment: 'Оплата', telegramChat: 'Чат Telegram', notProvided: 'Не указано', saveChanges: 'Сохранить изменения', editLesson: 'Редактировать занятие', markCompleted: 'Отметить проведённым', cancelLesson: 'Отменить занятие', markPaid: 'Отметить оплаченным', undoPayment: 'Отменить оплату',
  administration: 'Администрирование', active: 'Активен', archive: 'Архив', role: 'Роль', lessonPrice: 'Цена занятия', lessonDuration: 'Длительность занятия', quietHours: 'Тихие часы', disabled: 'Выключены', editData: 'Редактировать данные', manageRecord: 'Управление записью', restore: 'Восстановить',
  finances: 'Финансы', confirmations: 'Подтверждения', pendingLessons: 'Неоплаченные занятия', noDebt: 'Нет задолженности', accept: 'Принять', reject: 'Отклонить', changeAmount: 'Изменить сумму', correct: 'Исправить',
  taskQueue: 'Рабочая очередь', type: 'Тип', status: 'Статус', all: 'Все', pending: 'Ожидает', approved: 'Принято', rejected: 'Отклонено', expired: 'Истекло', newRequest: 'Новая заявка', newLesson: 'Новое занятие', proposeTime: 'Предложить время', counterTime: 'Другой термин', confirm: 'Подтвердить', later: 'Позже', noRequests: 'Нет заявок',
  chooseDateTime: 'Выберите день и время', day: 'День', message: 'Сообщение', optional: 'необязательно', summary: 'Итого', sendRequest: 'Отправить заявку', sending: 'Отправка…', proposeLesson: 'Предложить занятие', proposeNewTime: 'Предложить новое время', proposeCounterTime: 'Предложить другой термин', request: 'Заявка', messagePlaceholder: 'Добавьте короткое сообщение преподавателю',
  mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс',
  profile: 'Профиль', profilePreferences: 'Профиль и настройки', teacherAccounts: 'Аккаунты, ставки и права', lessonExports: 'Занятия и экспорт данных', regularHours: 'Постоянные часы и исключения', offline: 'Нет подключения к интернету', openFailed: 'Не удалось открыть приложение', tryAgain: 'Попробовать снова', exportSent: 'Файл отправлен в личный чат с ботом.', administrator: 'администратор', mainNavigation: 'Основная навигация',
  network_unavailable: 'Нет соединения с сервером.', request_timeout: 'Сервер не ответил вовремя. Попробуйте ещё раз.', invalid_lesson_transition: 'Это действие больше недоступно.', student_not_assigned: 'Сначала назначьте ученика.', student_not_found: 'Ученик или группа больше недоступны.', telegram_user_already_linked: 'Этот Telegram-аккаунт уже связан с другим учеником.', telegram_user_linked_to_archived_student: 'Этот Telegram-аккаунт связан с архивным учеником.', insufficient_unallocated_funds: 'Недостаточно свободных средств для оплаты занятия.', outside_availability: 'Время находится вне общей доступности.', account_not_linked: 'Аккаунт ещё не подключён.', cannot_delete_current_admin: 'Нельзя удалить текущего администратора.', historical_lesson_read_only: 'Историческое занятие нельзя редактировать.', conflict: 'Обнаружен конфликт.', genericError: 'Не удалось выполнить действие. Попробуйте ещё раз.',
}

export function t(key: string, polish: string): string { return uiLanguage === 'ru' ? ru[key] || polish : polish }
