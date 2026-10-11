import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, Lesson, Receipt, Role, ServiceRequest, Student, Teacher } from './api'
import { addDays, formatDate, formatDay, formatShort, isoDate, paymentText, startOfWeek, statusText } from './utils'
import { t, uiLocale } from './i18n'

type Tab = 'calendar' | 'teachers' | 'students' | 'payments' | 'reports' | 'availability' | 'settings' | 'account' | 'requests' | 'more'

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const values: Record<string, string> = {
      network_unavailable: t('network_unavailable', 'Brak połączenia z serwerem.'),
      request_timeout: t('request_timeout', 'Serwer nie odpowiedział na czas. Spróbuj ponownie.'),
      invalid_lesson_transition: t('invalid_lesson_transition', 'Ta akcja nie jest już dostępna.'),
      student_not_assigned: t('student_not_assigned', 'Najpierw przypisz ucznia.'),
      student_not_found: t('student_not_found', 'Uczeń lub grupa nie są już dostępne.'),
      telegram_user_already_linked: t('telegram_user_already_linked', 'To konto Telegram jest już połączone z innym uczniem.'),
      telegram_user_linked_to_archived_student: t('telegram_user_linked_to_archived_student', 'To konto Telegram jest połączone z uczniem w archiwum.'),
      insufficient_unallocated_funds: t('insufficient_unallocated_funds', 'Brak wystarczających środków na opłacenie lekcji.'),
      outside_availability: t('outside_availability', 'Termin jest poza wspólną dostępnością.'),
      account_not_linked: t('account_not_linked', 'Konto nie zostało jeszcze połączone.'),
      cannot_delete_current_admin: t('cannot_delete_current_admin', 'Nie można usunąć bieżącego administratora.'),
      historical_lesson_read_only: t('historical_lesson_read_only', 'Tej lekcji historycznej nie można edytować.'),
    }
    if (typeof error.detail === 'object' && error.detail && 'code' in error.detail) return values[String(error.detail.code)] || t('conflict', 'Wykryto konflikt.')
    return values[String(error.detail)] || error.message
  }
  return t('genericError', 'Nie udało się wykonać operacji. Spróbuj ponownie.')
}

function Spinner() { return <div className="state"><span className="spinner" />{t('loading', 'Ładowanie…')}</div> }
function Empty({ children }: { children: string }) { return <div className="empty"><span className="empty-icon">○</span><strong>{children}</strong></div> }
function Notice({ text, success = false }: { text: string; success?: boolean }) { return <div className={`notice ${success ? 'success' : ''}`}>{text}</div> }
function Header({ eyebrow, title, right }: { eyebrow: string; title: string; right?: ReactNode }) { return <header className="topbar"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>{right}</header> }
function AsyncButton({ children, className, run }: { children: ReactNode; className?: string; run: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const click = async () => {
    if (busy) return
    setBusy(true)
    try { await run() } finally { setBusy(false) }
  }
  return <button type="button" className={className} disabled={busy} aria-busy={busy} onClick={click}>{busy ? <><span className="button-spinner" />{t('wait', 'Proszę czekać')}</> : children}</button>
}

function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span> }
function DetailGrid({ items }: { items: [string, ReactNode][] }) { return <dl className="detail-grid">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>)}</dl> }
function Sheet({ title, eyebrow, onClose, children }: { title: string; eyebrow: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => { const close = (event: KeyboardEvent) => event.key === 'Escape' && onClose(); document.body.classList.add('modal-open'); window.addEventListener('keydown', close); return () => { document.body.classList.remove('modal-open'); window.removeEventListener('keydown', close) } }, [onClose])
  return <div className="modal-wrap" role="dialog" aria-modal="true" aria-label={title}><button className="scrim" aria-label={t('close', 'Zamknij')} onClick={onClose} /><div className="sheet"><div className="sheet-handle" /><Header eyebrow={eyebrow} title={title} right={<button className="close" aria-label={t('close', 'Zamknij')} onClick={onClose}>×</button>} />{children}</div></div>
}

function LessonCard({ lesson, onClick }: { lesson: Lesson; onClick?: () => void }) {
  return <button className={`lesson-card status-${lesson.status}`} onClick={onClick} disabled={!onClick}>
    <span className="lesson-time">{lesson.time}</span>
    <span className="lesson-main"><strong>{lesson.student_name || t('noName', 'Bez nazwy')}</strong><small>{lesson.teacher_name || t('noTeacher', 'Bez nauczyciela')}</small><span className="lesson-meta"><Badge tone={lesson.status}>{statusText[lesson.status]}</Badge><span>{lesson.price ? `${lesson.price} PLN` : t('noPrice', 'Brak ceny')}</span><span>{paymentText[lesson.payment_status]}</span></span></span>
    <span className="chevron" aria-hidden="true">›</span>
  </button>
}

function CalendarView({ refresh, canManage, teacherId, onChanged }: { refresh: number; canManage: boolean; teacherId?: number; onChanged: () => void }) {
  const [monday, setMonday] = useState(() => startOfWeek(new Date()))
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [students, setStudents] = useState<Student[]>([])
  const [editing, setEditing] = useState<Lesson | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')
  const end = addDays(monday, 7)
  useEffect(() => {
    api.calendar(isoDate(monday), isoDate(end)).then(data => setLessons(teacherId ? data.lessons.filter(row => row.teacher_id === teacherId) : data.lessons)).catch(e => setError(errorText(e)))
    if (canManage) api.students().then(data => setStudents(data.active.filter(row => row.active && (!teacherId || row.teacher_id === teacherId))))
  }, [monday.getTime(), refresh, canManage, teacherId])
  const days = Array.from({ length: 7 }, (_, index) => addDays(monday, index))
  return <>
    <Header eyebrow={t('plan', 'Plan')} title={t('calendar', 'Kalendarz')} />
    <div className="week-nav"><button onClick={() => setMonday(addDays(monday, -7))}>‹</button><b>{formatShort(isoDate(monday))} — {formatShort(isoDate(addDays(monday, 6)))}</b><button onClick={() => setMonday(addDays(monday, 7))}>›</button></div>
    {error && <Notice text={error} />}
    <div className="day-list">{days.map(day => { const key = isoDate(day); const rows = lessons.filter(row => row.date === key); return <section className="day" key={key}><div className="day-heading"><h2>{formatDay(key)}</h2><span>{rows.length || '—'}</span></div>{rows.length ? rows.map(row => <LessonCard key={row.id} lesson={row} onClick={() => setEditing(row)} />) : <Empty>{t('freeDay', 'Wolny dzień')}</Empty>}</section> })}</div>
    {canManage && <button className="fab" aria-label={t('addLesson', 'Dodaj lekcję')} onClick={() => setAdding(true)}>+</button>}
    {adding && <LessonForm students={students} onClose={() => setAdding(false)} onSaved={() => { onChanged(); setAdding(false) }} />}
    {editing && <LessonEditor lesson={editing} canManage={canManage} onClose={() => setEditing(null)} onSaved={() => { onChanged(); setEditing(null) }} />}
  </>
}

function LessonForm({ students, onClose, onSaved }: { students: Student[]; onClose: () => void; onSaved: () => void }) {
  const [chat, setChat] = useState(String(students[0]?.id || ''))
  const [day, setDay] = useState(isoDate(new Date()))
  const [clock, setClock] = useState('16:00')
  const [duration, setDuration] = useState(60)
  const [price, setPrice] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const selectedChatId = students.some(row => String(row.id) === chat) ? chat : String(students[0]?.id || '')
  const save = async () => {
    if (savingRef.current) return
    savingRef.current = true
    setSaving(true)
    const payload = { study_chat_id: Number(selectedChatId), date: day, time: clock, duration_minutes: duration, price: price || null }
    try {
      try { await api.createLesson({ ...payload, force: false }) }
      catch (e) {
        if (!(e instanceof ApiError && e.status === 409 && confirm('Есть конфликт или время вне доступности. Сохранить всё равно?'))) throw e
        await api.createLesson({ ...payload, force: true })
      }
      onSaved()
    } catch (e) { setError(errorText(e)) }
    finally { savingRef.current = false; setSaving(false) }
  }
  return <Sheet eyebrow={t('newEvent', 'Nowe wydarzenie')} title={t('addLesson', 'Dodaj lekcję')} onClose={onClose}><form className="form-grid" onSubmit={e => { e.preventDefault(); save() }}>{error && <Notice text={error} />}<label>{t('student', 'Uczeń')}<select required value={selectedChatId} onChange={e => setChat(e.target.value)}>{students.map(row => <option key={row.id} value={row.id}>{row.student_name}</option>)}</select></label><div className="two-cols"><label>{t('date', 'Data')}<input required type="date" value={day} onChange={e => setDay(e.target.value)} /></label><label>{t('time', 'Godzina')}<input required type="time" value={clock} onChange={e => setClock(e.target.value)} /></label></div><div className="two-cols"><label>{t('duration', 'Czas trwania')}<input required type="number" min="15" max="480" value={duration} onChange={e => setDuration(Number(e.target.value))} /><small>{t('minutes', 'min')}</small></label><label>{t('price', 'Cena')} PLN<input type="number" min="0" step="0.01" value={price} onChange={e => setPrice(e.target.value)} /></label></div><div className="form-actions"><button type="button" className="secondary" onClick={onClose}>{t('cancel', 'Anuluj')}</button><button className="primary" disabled={saving || !students.length}>{saving ? t('saving', 'Zapisywanie…') : t('saveLesson', 'Zapisz lekcję')}</button></div></form></Sheet>
}

function LessonEditor({ lesson, canManage, onClose, onSaved }: { lesson: Lesson; canManage: boolean; onClose: () => void; onSaved: () => void }) {
  const [day, setDay] = useState(lesson.date), [clock, setClock] = useState(lesson.time)
  const [duration, setDuration] = useState(lesson.duration_minutes), [price, setPrice] = useState(lesson.price || '')
  const [error, setError] = useState(''), [editing, setEditing] = useState(false)
  const mutate = async (operation: () => Promise<unknown>) => { try { await operation(); onSaved() } catch (e) { setError(errorText(e)) } }
  const save = async (force = false) => {
    try { await api.editLesson(lesson.id, { date: day, time: clock, duration_minutes: duration, price: price || null, price_set: true, force }); onSaved() }
    catch (e) { if (e instanceof ApiError && e.status === 409 && confirm('Сохранить несмотря на предупреждение?')) return save(true); setError(errorText(e)) }
  }
  const remove = async () => { if (!confirm(`Usunąć trwale lekcję ${lesson.date} o ${lesson.time}? Tej operacji nie można cofnąć.`)) return; await mutate(() => api.remove(lesson.id)) }
  const cancel = async () => { if (!confirm(`Anulować lekcję ${lesson.student_name || ''} ${lesson.date} o ${lesson.time}?`)) return; await mutate(() => api.action(lesson.id, 'cancel')) }
  return <Sheet eyebrow={t('lessonDetails', 'Szczegóły lekcji')} title={lesson.student_name || t('lessonDetails', 'Lekcja')} onClose={onClose}><div className="form-grid">{error && <Notice text={error} />}<div className="status-line"><Badge tone={lesson.status}>{statusText[lesson.status]}</Badge><Badge tone={lesson.payment_status}>{paymentText[lesson.payment_status]}</Badge></div>{editing ? <><div className="two-cols"><label>{t('date', 'Data')}<input required type="date" value={day} onChange={e => setDay(e.target.value)} /></label><label>{t('time', 'Godzina')}<input required type="time" value={clock} onChange={e => setClock(e.target.value)} /></label></div><div className="two-cols"><label>{t('duration', 'Czas trwania')}<input type="number" min="15" max="480" value={duration} onChange={e => setDuration(Number(e.target.value))} /></label><label>{t('price', 'Cena')} PLN<input type="number" min="0" step="0.01" value={price} onChange={e => setPrice(e.target.value)} /></label></div><div className="form-actions"><button className="secondary" onClick={() => setEditing(false)}>{t('cancel', 'Anuluj')}</button><AsyncButton className="primary" run={() => save()}>{t('saveChanges', 'Zapisz zmiany')}</AsyncButton></div></> : <DetailGrid items={[["ID", lesson.id], [t('student', 'Uczeń'), lesson.student_name], [t('teacher', 'Nauczyciel'), lesson.teacher_name], [t('date', 'Data'), lesson.date], [t('time', 'Godzina'), `${lesson.time}–${new Date(new Date(`2000-01-01T${lesson.time}`).getTime() + lesson.duration_minutes * 60000).toTimeString().slice(0, 5)}`], [t('duration', 'Czas trwania'), `${lesson.duration_minutes} ${t('minutes', 'min')}`], [t('price', 'Cena'), lesson.price ? `${lesson.price} PLN` : t('notProvided', 'Nie podano')], [t('payment', 'Płatność'), paymentText[lesson.payment_status]], [t('telegramChat', 'Czat Telegram'), lesson.telegram_chat_title]]} />}{canManage && !editing && <button className="primary wide" onClick={() => setEditing(true)}>{t('editLesson', 'Edytuj lekcję')}</button>}{canManage && lesson.status === 'planned' && !editing && <div className="action-grid"><AsyncButton className="secondary" run={() => mutate(() => api.action(lesson.id, 'complete'))}>{t('markCompleted', 'Oznacz jako zakończoną')}</AsyncButton><AsyncButton className="danger" run={cancel}>{t('cancelLesson', 'Anuluj lekcję')}</AsyncButton></div>}{canManage && lesson.status === 'completed' && !editing && <AsyncButton className="secondary" run={() => mutate(() => api.payment(lesson.id, lesson.payment_status === 'paid' ? 'unpaid' : 'paid'))}>{lesson.payment_status === 'paid' ? t('undoPayment', 'Cofnij płatność') : t('markPaid', 'Oznacz jako opłaconą')}</AsyncButton>}{canManage && !editing && <AsyncButton className="danger-text" run={remove}>{t('delete', 'Usuń trwale')}</AsyncButton>}</div></Sheet>
}

function StudentsView({ refresh, canManage, isAdmin, teacherId, onChanged }: { refresh: number; canManage: boolean; isAdmin: boolean; teacherId?: number; onChanged: () => void }) {
  const [data, setData] = useState<{ pending: Student[]; active: Student[] } | null>(null)
  const [archived, setArchived] = useState<Student[]>([])
  const [links, setLinks] = useState<Awaited<ReturnType<typeof api.pendingLinks>>['links']>([])
  const [selected, setSelected] = useState<Student | null>(null), [names, setNames] = useState<Record<number, string>>({}), [saved, setSaved] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    api.students(isAdmin).then(value => {
      const filter = (row: Student) => !teacherId || row.teacher_id === teacherId
      setData({ pending: value.pending.filter(filter), active: value.active.filter(filter) })
      setArchived(isAdmin ? value.active.filter(row => !row.active && filter(row)) : [])
    }).catch(e => setError(errorText(e)))
    if (canManage) api.pendingLinks().then(value => setLinks(value.links.filter(row => !teacherId || row.teacher_id === teacherId))).catch(e => setError(errorText(e)))
  }, [refresh, isAdmin, canManage, teacherId])
  const assign = async (row: Student) => { try { await api.assignStudent(row.id, names[row.id] || ''); onChanged() } catch (e) { setError(errorText(e)) } }
  const approveLink = async (id: number) => { try { await api.approveLink(id); onChanged() } catch (e) { setError(errorText(e)) } }
  return <><Header eyebrow="Группы Telegram" title="Ученики" />{error && <Notice text={error} />}{!data ? <Spinner /> : <div className="content-stack">
    {saved && <Notice text={saved} success />}{canManage && data.pending.map(row => <div className="panel" key={row.id}><strong>{row.telegram_chat_title}</strong><div className="inline-form"><input placeholder="Имя ученика" value={names[row.id] || ''} onChange={e => setNames({ ...names, [row.id]: e.target.value })} /><AsyncButton run={() => assign(row)}>Назначить</AsyncButton><button className="secondary" onClick={async () => { try { const link = await api.createStudentLink(row.id); await navigator.clipboard?.writeText(link.url); setSaved(`Link ważny ${Math.round(link.expires_in_seconds / 60)} min`) } catch (e) { setError(errorText(e)) } }}>Wyślij link</button></div></div>)}
    {canManage && links.length > 0 && <section><div className="section-title"><h2>Ожидают подтверждения</h2><span>{links.length}</span></div>{links.map(link => <div className="panel payment-row" key={link.id}><span><strong>{link.student_name}</strong><small>Telegram ID: {link.telegram_user_id}</small></span><AsyncButton run={() => approveLink(link.id)}>Подключить</AsyncButton></div>)}</section>}
    {data.active.filter(row => row.active).map(row => <button className="panel student-row" key={row.id} onClick={() => setSelected(row)}><span className="student-initial">{row.student_name?.[0]}</span><span><strong>{row.student_name}</strong><small>{row.teacher_name || 'Преподаватель'} · баланс {row.balance} PLN</small></span><b>›</b></button>)}
    {isAdmin && archived.filter(row => row.student_id != null).map(row => <div className="panel payment-row" key={`archived-${row.id}`}><span><strong>{row.student_name}</strong><small>Archiwum</small></span><button onClick={async () => { try { await api.restoreStudent(row.student_id); onChanged() } catch (e) { setError(errorText(e)) } }}>Przywróć</button></div>)}
  </div>}{selected && <StudentPanel student={selected} canManage={canManage} isAdmin={isAdmin} onClose={() => setSelected(null)} onChanged={onChanged} />}</>
}

function StudentPanel({ student, canManage, isAdmin, onClose, onChanged }: { student: Student; canManage: boolean; isAdmin: boolean; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.student>> | null>(null), [name, setName] = useState(student.student_name || ''), [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  useEffect(() => { api.student(student.id).then(setData) }, [student.id])
  const saveName = async (event: FormEvent) => {
    event.preventDefault()
    if (savingRef.current) return
    savingRef.current = true
    setSaving(true)
    try { await api.assignStudent(student.id, name); onChanged() }
    catch (cause) { setError(errorText(cause)) }
    finally { savingRef.current = false; setSaving(false) }
  }
  const remove = async () => { if (!confirm('Usunąć ucznia trwale? Lekcje pozostaną w historii.')) return; try { await api.deleteStudent(student.student_id); onChanged(); onClose() } catch (e) { setError(errorText(e)) } }
  return <div className="overlay page-overlay"><Header eyebrow="Karta ucznia" title={student.student_name || 'Bez nazwy'} right={<button className="close" aria-label="Zamknij" onClick={onClose}>×</button>} />{error && <Notice text={error} />}{!data ? <Spinner /> : <div className="content-stack"><div className="profile-layout"><section className="profile-main"><div className="panel balance-card"><small>Bieżące saldo</small><h2>{data.balance} PLN</h2><span>Dostępne do rozliczenia: {data.available_pool} PLN</span></div><div className="panel"><div className="section-title"><h2>Dane ucznia</h2><Badge tone={student.active ? 'completed' : 'neutral'}>{student.active ? 'Aktywny' : 'Archiwum'}</Badge></div><DetailGrid items={[["ID wewnętrzne", student.student_id], ["ID grupy", student.id], ["Telegram ID", student.student_telegram_id], ["Czat Telegram", student.telegram_chat_title], ["Nauczyciel", student.teacher_name], ["Status przypisania", student.assigned ? 'Przypisany' : 'Oczekuje']]} /></div>{canManage && <form className="panel form-grid" onSubmit={saveName}><label>Imię ucznia<input required value={name} onChange={e => setName(e.target.value)} /></label><button className="primary" disabled={saving || name === student.student_name}>{saving ? 'Zapisywanie…' : 'Zapisz zmiany'}</button></form>}{isAdmin && <details className="danger-zone panel"><summary>Zarządzanie rekordem</summary><button className="secondary" onClick={async () => { await api.archiveStudent(student.student_id); onChanged(); onClose() }}>Archiwizuj ucznia</button><button className="danger" onClick={remove}>Usuń trwale, zachowaj lekcje</button></details>}</section><section><div className="section-title"><h2>Historia konta</h2><span>{data.account_history.length}</span></div>{data.account_history.length ? data.account_history.map((row, index) => <div className="panel account-row" key={`${row.kind}-${index}`}><span><strong>{row.description}</strong><small>{new Date(row.datetime).toLocaleString('pl-PL')}</small></span><span className={Number(row.amount) >= 0 ? 'credit' : 'debit'}>{row.amount} PLN<br /><small>{row.balance} PLN</small></span></div>) : <Empty>Brak historii</Empty>}</section></div></div>}</div>
}

function TeachersView({ refresh, onChanged }: { refresh: number; onChanged: () => void }) {
  const [rows, setRows] = useState<Teacher[]>([]), [editing, setEditing] = useState<Teacher | null>(null), [workspace, setWorkspace] = useState<Teacher | null>(null), [error, setError] = useState('')
  useEffect(() => { api.teachers().then(data => setRows(data.teachers)).catch(e => setError(errorText(e))) }, [refresh])
  const remove = async (row: Teacher) => { if (!confirm(`Usunąć ${row.name} trwale? Lekcje pozostaną w historii. Tej operacji nie można cofnąć.`)) return false; try { await api.deleteTeacher(row.id); onChanged(); return true } catch (e) { setError(errorText(e)); return false } }
  return <><Header eyebrow="Administracja" title="Nauczyciele" right={<button className="primary compact" onClick={() => setEditing({ id: 0, telegram_user_id: 0, name: '', active: true, is_admin: false, default_price: '', default_duration_minutes: 60, quiet_hours_start: null, quiet_hours_end: null })}>+ Dodaj</button>} />{error && <Notice text={error} />}<div className="record-list">{rows.map(row => <button className="panel student-row record-card" key={row.id} onClick={() => setWorkspace(row)}><span className="student-initial">{row.name?.[0] || '?'}</span><span><span className="record-title"><strong>{row.name}</strong><Badge tone={row.active ? 'completed' : 'neutral'}>{row.active ? 'Aktywny' : 'Archiwum'}</Badge></span><small>ID Telegram: {row.telegram_user_id || '—'}</small><small>{row.default_price || '—'} PLN · {row.default_duration_minutes} min</small></span><b>›</b></button>)}</div>{editing && <TeacherForm teacher={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged() }} />}{workspace && <TeacherWorkspace teacher={workspace} refresh={refresh} onClose={() => setWorkspace(null)} onEdit={() => setEditing(workspace)} onArchive={async () => { try { if (workspace.active) await api.archiveTeacher(workspace.id); else await api.restoreTeacher(workspace.id); setWorkspace(null); onChanged() } catch (e) { setError(errorText(e)) } }} onDelete={async () => { if (await remove(workspace)) setWorkspace(null) }} onChanged={onChanged} />}</>
}

function TeacherWorkspace({ teacher, refresh, onClose, onEdit, onArchive, onDelete, onChanged }: { teacher: Teacher; refresh: number; onClose: () => void; onEdit: () => void; onArchive: () => Promise<void>; onDelete: () => Promise<void>; onChanged: () => void }) {
  const [tab, setTab] = useState<'calendar' | 'students' | 'requests'>('calendar')
  return <div className="overlay page-overlay"><Header eyebrow="Karta nauczyciela" title={teacher.name} right={<button className="close" aria-label="Zamknij" onClick={onClose}>×</button>} /><div className="panel teacher-summary"><DetailGrid items={[["ID", teacher.id], ["Telegram ID", teacher.telegram_user_id], ["Rola", teacher.is_admin ? 'Administrator' : 'Nauczyciel'], ["Status", teacher.active ? 'Aktywny' : 'Archiwum'], ["Cena lekcji", teacher.default_price ? `${teacher.default_price} PLN` : 'Nie podano'], ["Czas lekcji", `${teacher.default_duration_minutes} min`], ["Godziny ciszy", teacher.quiet_hours_start ? `${teacher.quiet_hours_start}–${teacher.quiet_hours_end}` : 'Wyłączone']]} /><div className="profile-actions"><button className="primary" onClick={onEdit}>Edytuj dane</button><details className="action-menu"><summary aria-label="Więcej działań">•••</summary><AsyncButton className="secondary" run={onArchive}>{teacher.active ? 'Archiwizuj' : 'Przywróć'}</AsyncButton><AsyncButton className="danger" run={onDelete}>Usuń trwale</AsyncButton></details></div></div><div className="workspace-tabs"><button className={tab === 'calendar' ? 'active' : ''} onClick={() => setTab('calendar')}>Plan</button><button className={tab === 'students' ? 'active' : ''} onClick={() => setTab('students')}>Uczniowie</button><button className={tab === 'requests' ? 'active' : ''} onClick={() => setTab('requests')}>Zgłoszenia</button></div>{tab === 'calendar' && <CalendarView refresh={refresh} canManage teacherId={teacher.id} onChanged={onChanged} />}{tab === 'students' && <StudentsView refresh={refresh} canManage isAdmin teacherId={teacher.id} onChanged={onChanged} />}{tab === 'requests' && <RequestsView role="admin" refresh={refresh} teacherId={teacher.id} onChanged={onChanged} />}</div>
}

function TeacherForm({ teacher, onClose, onSaved }: { teacher: Teacher; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState(teacher), [error, setError] = useState(''), [saving, setSaving] = useState(false)
  const save = async (e: FormEvent) => { e.preventDefault(); if (saving) return; setSaving(true); try { const { id, ...body } = form; await api.saveTeacher(body, id || undefined); onSaved() } catch (err) { setError(errorText(err)); setSaving(false) } }
  return <Sheet eyebrow="Nauczyciel" title={teacher.id ? 'Edytuj dane' : 'Nowe konto'} onClose={onClose}><form className="form-grid" onSubmit={save}>{error && <Notice text={error} />}<label>Telegram ID<input required type="number" value={form.telegram_user_id || ''} onChange={e => setForm({ ...form, telegram_user_id: Number(e.target.value) })} /></label><label>Imię i nazwisko<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><div className="two-cols"><label>Cena PLN<input type="number" min="0" step="0.01" value={form.default_price || ''} onChange={e => setForm({ ...form, default_price: e.target.value })} /></label><label>Czas lekcji<input type="number" min="15" max="480" value={form.default_duration_minutes} onChange={e => setForm({ ...form, default_duration_minutes: Number(e.target.value) })} /></label></div><div className="two-cols"><label>Tryb cichy od<input type="time" value={form.quiet_hours_start || ''} onChange={e => setForm({ ...form, quiet_hours_start: e.target.value || null })} /></label><label>Tryb cichy do<input type="time" value={form.quiet_hours_end || ''} onChange={e => setForm({ ...form, quiet_hours_end: e.target.value || null })} /></label></div><label className="check-row"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} /> Konto aktywne</label><label className="check-row"><input type="checkbox" checked={form.is_admin} onChange={e => setForm({ ...form, is_admin: e.target.checked })} /> Uprawnienia administratora</label><div className="form-actions"><button type="button" className="secondary" onClick={onClose}>Anuluj</button><button className="primary" disabled={saving}>{saving ? 'Zapisywanie…' : 'Zapisz zmiany'}</button></div></form></Sheet>
}

function PaymentsView({ refresh, canReview, isAdmin, onChanged }: { refresh: number; canReview: boolean; isAdmin: boolean; onChanged: () => void }) {
  const [receipts, setReceipts] = useState<Receipt[]>([]), [unpaid, setUnpaid] = useState<Lesson[]>([]), [selected, setSelected] = useState<Lesson | null>(null), [error, setError] = useState('')
  useEffect(() => { api.receipts().then(data => setReceipts(data.receipts)); api.unpaid().then(data => setUnpaid(data.students.flatMap(row => row.lessons))).catch(e => setError(errorText(e))) }, [refresh])
  const approve = async (row: Receipt, corrected = false) => { const amount = corrected || !row.submitted_amount ? prompt(`Сумма платежа ${row.currency}`, row.submitted_amount || '') : row.submitted_amount; if (!amount) return; const ids = row.currency === 'PLN' ? unpaid.filter(item => item.student_id === row.student_id && confirm(`Оплатить занятие ${item.date}, ${item.price} PLN?`)).map(item => item.id) : []; try { await api.approveReceipt(row.id, amount, ids, row.currency); onChanged() } catch (e) { setError(errorText(e)) } }
  return <><Header eyebrow="Finanse" title="Płatności" />{error && <Notice text={error} />}{!canReview && <Notice text="Aby wysłać potwierdzenie płatności, prześlij zdjęcie lub PDF do bota komendą /pay." success />}<section><div className="section-title"><h2>Potwierdzenia</h2><span>{receipts.length}</span></div>{receipts.map(row => <div className="panel payment-row" key={row.id}><div><span className="record-title"><strong>{row.student_name}</strong><Badge tone={row.status}>{row.status}</Badge></span><small>{new Date(row.created_at).toLocaleString('pl-PL')} · {row.currency} {row.submitted_amount ? `· zgłoszono ${row.submitted_amount}` : ''} {row.amount ? `· potwierdzono ${row.amount}` : ''}</small></div>{canReview && row.status === 'pending' && <div><AsyncButton run={() => approve(row)}>Przyjmij</AsyncButton><AsyncButton run={() => approve(row, true)}>Zmień kwotę</AsyncButton><AsyncButton className="danger-text" run={async () => { const reason = prompt('Powód odrzucenia') || ''; if (reason) { await api.rejectReceipt(row.id, reason); onChanged() } }}>Odrzuć</AsyncButton></div>}{isAdmin && row.status !== 'pending' && <div><AsyncButton run={async () => { const amount = prompt(`Poprawiona kwota ${row.currency}`, row.amount || ''); if (amount) { await api.correctReceipt(row.id, 'approved', amount, '', row.currency); onChanged() } }}>Popraw</AsyncButton><AsyncButton className="danger-text" run={async () => { const reason = prompt('Powód anulowania') || ''; if (reason) { await api.correctReceipt(row.id, 'rejected', null, reason, row.currency); onChanged() } }}>Anuluj</AsyncButton></div>}</div>)}</section><section><div className="section-title"><h2>Nieopłacone lekcje</h2><span>{unpaid.length}</span></div>{unpaid.length ? unpaid.map(row => <LessonCard key={row.id} lesson={row} onClick={() => setSelected(row)} />) : <Empty>Brak zaległych płatności</Empty>}</section>{selected && <LessonEditor lesson={selected} canManage={canReview} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); onChanged() }} />}</>
}

const weekDays = [t('mon', 'Pn'), t('tue', 'Wt'), t('wed', 'Śr'), t('thu', 'Cz'), t('fri', 'Pt'), t('sat', 'Sb'), t('sun', 'Nd')]
function AvailabilityView() {
  const [rules, setRules] = useState<{ weekday: number; start_time: string; end_time: string }[]>([]), [exceptions, setExceptions] = useState<{ date: string; available: boolean; start_time: string | null; end_time: string | null }[]>([]), [error, setError] = useState(''), [saved, setSaved] = useState('')
  const [quiet, setQuiet] = useState({ start: '', end: '' })
  useEffect(() => { api.availability().then(data => { setRules(data.rules); setExceptions(data.exceptions) }).catch(e => setError(errorText(e))); api.settings().then(data => setQuiet({ start: data.quiet_hours_start || '', end: data.quiet_hours_end || '' })) }, [])
  const addRule = (weekday: number) => setRules([...rules, { weekday, start_time: '10:00', end_time: '18:00' }])
  const updateRule = (index: number, field: 'start_time' | 'end_time', value: string) => setRules(rules.map((row, item) => item === index ? { ...row, [field]: value } : row))
  const addException = () => setExceptions([...exceptions, { date: isoDate(new Date()), available: false, start_time: null, end_time: null }])
  const updateException = (index: number, value: Partial<typeof exceptions[number]>) => setExceptions(exceptions.map((row, item) => item === index ? { ...row, ...value } : row))
  const save = async () => { try { await api.saveAvailability(rules.filter(row => row.start_time && row.end_time)); for (const row of exceptions) await api.addException(row); setSaved('Dostępność zapisana') } catch (e) { setError(errorText(e)) } }
  const removeException = async (row: typeof exceptions[number], index: number) => { try { await api.deleteException(row.date); setExceptions(exceptions.filter((_, item) => item !== index)) } catch (e) { setError(errorText(e)) } }
  return <><Header eyebrow="Dostępność" title="Dostępność" />{error && <Notice text={error} />}{saved && <Notice text={saved} success />}<div className="content-stack"><div className="panel"><strong>Godziny ciszy</strong><p><small>Powiadomienia przychodzą od razu, ale bez dźwięku.</small></p><div className="two-cols"><input type="time" value={quiet.start} onChange={e => setQuiet({ ...quiet, start: e.target.value })} /><input type="time" value={quiet.end} onChange={e => setQuiet({ ...quiet, end: e.target.value })} /></div><button onClick={async () => { await api.saveQuietHours(quiet.start || null, quiet.end || null); setSaved('Godziny ciszy zapisane') }}>Zapisz</button></div><section><div className="section-title"><h2>Stałe przedziały</h2><span>Możesz dodać kilka na dzień</span></div>{weekDays.map((label, weekday) => <div className="panel availability-day" key={label}><strong>{label}</strong>{rules.map((row, index) => row.weekday === weekday && <div className="availability-row" key={`${weekday}-${index}`}><input type="time" value={row.start_time} onChange={e => updateRule(index, 'start_time', e.target.value)} /><input type="time" value={row.end_time} onChange={e => updateRule(index, 'end_time', e.target.value)} /><button onClick={() => setRules(rules.filter((_, item) => item !== index))}>Usuń</button></div>)}<button onClick={() => addRule(weekday)}>Dodaj przedział</button></div>)}</section><section><div className="section-title"><h2>Wyjątki dat</h2><button onClick={addException}>Dodaj datę</button></div>{exceptions.map((row, index) => <div className="panel exception-row" key={`${row.date}-${index}`}><input type="date" value={row.date} onChange={e => updateException(index, { date: e.target.value })} /><select value={row.available ? 'available' : 'off'} onChange={e => updateException(index, e.target.value === 'available' ? { available: true, start_time: '10:00', end_time: '18:00' } : { available: false, start_time: null, end_time: null })}><option value="off">Wolne</option><option value="available">Specjalne godziny</option></select>{row.available && <div className="two-cols"><input type="time" value={row.start_time || ''} onChange={e => updateException(index, { start_time: e.target.value })} /><input type="time" value={row.end_time || ''} onChange={e => updateException(index, { end_time: e.target.value })} /></div>}<button className="danger-text" onClick={() => removeException(row, index)}>Usuń</button></div>)}</section><button className="primary wide" onClick={save}>Zapisz dostępność</button></div></>
}

function DateTimeRequestForm({ title, initialDuration = 60, onClose, onSubmit }: { title: string; initialDuration?: number; onClose: () => void; onSubmit: (value: { date: string; time: string; duration_minutes: number; reason: string }) => Promise<void> }) {
  const [date, setDate] = useState(isoDate(addDays(new Date(), 1))), [time, setTime] = useState('16:00'), [duration, setDuration] = useState(initialDuration), [reason, setReason] = useState(''), [error, setError] = useState(''), [saving, setSaving] = useState(false)
  const submit = async (event: FormEvent) => { event.preventDefault(); if (saving) return; setSaving(true); try { await onSubmit({ date, time, duration_minutes: duration, reason }); onClose() } catch (cause) { setError(errorText(cause)); setSaving(false) } }
  return <Sheet eyebrow={t('request', 'Zgłoszenie')} title={title} onClose={onClose}><form className="form-grid" onSubmit={submit}>{error && <Notice text={error} />}<div className="step-label"><span>1</span> {t('chooseDateTime', 'Wybierz dzień i godzinę')}</div><div className="two-cols"><label>{t('day', 'Dzień')}<input required min={isoDate(new Date())} type="date" value={date} onChange={event => setDate(event.target.value)} /></label><label>{t('time', 'Godzina')}<input required type="time" value={time} onChange={event => setTime(event.target.value)} /></label></div><label>{t('duration', 'Czas trwania')}<select value={duration} onChange={event => setDuration(Number(event.target.value))}>{[30, 45, 60, 90, 120].map(value => <option key={value} value={value}>{value} {t('minutes', 'min')}</option>)}</select></label><label>{t('message', 'Wiadomość')} <small>({t('optional', 'opcjonalnie')})</small><textarea rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder={t('messagePlaceholder', 'Dodaj krótką informację dla nauczyciela')} /></label><div className="request-summary"><span>{t('summary', 'Podsumowanie')}</span><strong>{formatDay(date)}, {time}</strong><small>{duration} {t('minutes', 'min')}</small></div><div className="form-actions"><button type="button" className="secondary" onClick={onClose}>{t('cancel', 'Anuluj')}</button><button className="primary" disabled={saving}>{saving ? t('sending', 'Wysyłanie…') : t('sendRequest', 'Wyślij zgłoszenie')}</button></div></form></Sheet>
}

function RequestsView({ role, refresh, teacherId, onChanged }: { role: Role; refresh: number; teacherId?: number; onChanged: () => void }) {
  const [requests, setRequests] = useState<ServiceRequest[]>([]), [lessons, setLessons] = useState<Lesson[]>([]), [error, setError] = useState('')
  const [type, setType] = useState(''), [status, setStatus] = useState('')
  const [proposal, setProposal] = useState<{ kind: 'new' | 'lesson' | 'counter'; lesson?: Lesson; request?: ServiceRequest } | null>(null)
  useEffect(() => { api.requests(type, status).then(data => setRequests(data.requests.filter(row => !teacherId || row.teacher_id === teacherId))).catch(e => setError(errorText(e))); if (role === 'student') api.lessons(new URLSearchParams({ status: 'planned' })).then(data => setLessons(data.lessons)) }, [refresh, role, type, status, teacherId])
  const cancelLesson = async (lesson: Lesson) => {
    try { await api.requestCancellation(lesson.id, prompt('Powód anulowania') || ''); onChanged() } catch (e) { setError(errorText(e)) }
  }
  const approve = async (row: ServiceRequest) => {
    try {
      if (row.type === 'payment_confirmation' && row.payment_id) {
        const amount = prompt('Сумма платежа PLN', row.submitted_amount || '')
        if (!amount) return
        await api.approveReceipt(row.payment_id, amount, [], row.currency)
      } else {
        try { await api.approveRequest(row.id) } catch (e) {
          if (e instanceof ApiError && e.status === 409 && confirm('Przyjąć mimo ostrzeżenia o dostępności?')) await api.approveRequest(row.id, true)
          else throw e
        }
      }
      onChanged()
    } catch (e) { setError(errorText(e)) }
  }
  const reject = async (row: ServiceRequest) => {
    const reason = prompt('Причина отказа') || ''
    if (row.type === 'payment_confirmation' && !reason) return
    try {
      if (row.type === 'payment_confirmation' && row.payment_id) await api.rejectReceipt(row.payment_id, reason)
      else await api.rejectRequest(row.id, reason)
      onChanged()
    } catch (e) { setError(errorText(e)) }
  }
  const confirmCounter = async (row: ServiceRequest) => {
    try { await api.confirmRequest(row.id) } catch (e) {
      if (e instanceof ApiError && e.status === 409 && confirm('Potwierdzić mimo ostrzeżenia o dostępności?')) await api.confirmRequest(row.id, true)
      else { setError(errorText(e)); return }
    }
    onChanged()
  }
  const remindLater = async (row: ServiceRequest) => {
    const value = prompt('Odroczyć przypomnienie o ile minut? Wybierz: 60, 180 lub 1440', '60')
    const minutes = Number(value)
    if (minutes !== 60 && minutes !== 180 && minutes !== 1440) return
    try { await api.remindLater(row.id, minutes); onChanged() } catch (e) { setError(errorText(e)) }
  }
  const typeText: Record<ServiceRequest['type'], string> = { payment_confirmation: 'Оплата', lesson_reschedule: 'Перенос', lesson_cancellation: 'Отмена', lesson_time_proposal: 'Новое время', other: 'Другое' }
  const requestSummary = (row: ServiceRequest) => row.type === 'payment_confirmation'
    ? `${row.submitted_amount || '—'} PLN`
    : row.proposed_datetime ? new Date(row.proposed_datetime).toLocaleString('ru') : '—'
  const submitProposal = async (value: { date: string; time: string; duration_minutes: number; reason: string }) => { if (!proposal) return; if (proposal.kind === 'new') await api.requestNewTimeProposal(value); if (proposal.kind === 'lesson' && proposal.lesson) await api.requestTimeProposal(proposal.lesson.id, value); if (proposal.kind === 'counter' && proposal.request) await api.counterRequest(proposal.request.id, value); onChanged() }
  return <><Header eyebrow="Kolejka zadań" title="Zgłoszenia" />{error && <Notice text={error} />}<div className="panel filters"><label>Typ<select value={type} onChange={e => setType(e.target.value)}><option value="">Wszystkie</option><option value="payment_confirmation">Płatności</option><option value="lesson_reschedule">Przeniesienia</option><option value="lesson_cancellation">Anulowania</option><option value="lesson_time_proposal">Nowy termin</option></select></label><label>Status<select value={status} onChange={e => setStatus(e.target.value)}><option value="">Oczekujące</option><option value="pending">Oczekujące</option><option value="approved">Przyjęte</option><option value="rejected">Odrzucone</option><option value="cancelled">Anulowane</option><option value="expired">Wygasłe</option></select></label></div>{role === 'student' && <section><div className="section-title"><h2>Nowe zgłoszenie</h2><button className="primary compact" onClick={() => setProposal({ kind: 'new' })}>+ Nowe zajęcie</button></div>{lessons.map(row => <div className="panel payment-row" key={row.id}><span><strong>{formatDay(row.date)}</strong><small>{row.time} · {row.duration_minutes} min</small></span><div><button onClick={() => setProposal({ kind: 'lesson', lesson: row })}>Zaproponuj termin</button><AsyncButton className="danger-text" run={() => cancelLesson(row)}>Anuluj zajęcie</AsyncButton></div></div>)}</section>}<section><div className="section-title"><h2>Zgłoszenia</h2><span>{requests.length}</span></div>{requests.length ? requests.map(row => <div className="panel payment-row" key={row.id}><div><span className="record-title"><strong>{row.student_name} · {typeText[row.type]}</strong><Badge tone={row.status}>{row.status}</Badge></span><small>{requestSummary(row)} · {row.phase === 'student_confirmation' ? 'oczekuje na ucznia' : row.phase === 'resolved' ? 'zamknięte' : 'do rozpatrzenia'}</small>{row.next_reminder_at && row.status === 'pending' && <small>Następne przypomnienie: {new Date(row.next_reminder_at).toLocaleString('pl-PL')}</small>}{row.comment && <small>{row.comment}</small>}{row.decision_reason && <small>{row.decision_reason}</small>}</div>{role !== 'student' && row.status === 'pending' && row.phase === 'teacher_review' && <div><AsyncButton run={() => approve(row)}>Przyjmij</AsyncButton><AsyncButton run={() => reject(row)}>Odrzuć</AsyncButton><AsyncButton run={() => remindLater(row)}>Później</AsyncButton>{(row.type === 'lesson_reschedule' || row.type === 'lesson_time_proposal') && <button onClick={() => setProposal({ kind: 'counter', request: row })}>Kontrtermin</button>}</div>}{role === 'student' && row.status === 'pending' && row.phase === 'student_confirmation' && <div><AsyncButton run={() => confirmCounter(row)}>Potwierdź</AsyncButton><AsyncButton className="danger-text" run={async () => { try { await api.cancelRequest(row.id); onChanged() } catch (e) { setError(errorText(e)) } }}>Anuluj</AsyncButton></div>}{role === 'student' && row.status === 'pending' && row.phase === 'teacher_review' && <AsyncButton className="danger-text" run={async () => { try { await api.cancelRequest(row.id); onChanged() } catch (e) { setError(errorText(e)) } }}>Anuluj</AsyncButton>}</div>) : <Empty>Brak zgłoszeń</Empty>}</section>{proposal && <DateTimeRequestForm title={proposal.kind === 'counter' ? 'Zaproponuj kontrtermin' : proposal.kind === 'lesson' ? 'Zaproponuj nowy termin' : 'Zaproponuj zajęcia'} initialDuration={proposal.lesson?.duration_minutes || proposal.request?.duration_minutes || 60} onClose={() => setProposal(null)} onSubmit={submitProposal} />}</>
}

function ReportsView({ refresh }: { refresh: number }) {
  const [month, setMonth] = useState(isoDate(new Date()).slice(0, 7)), [lessons, setLessons] = useState<Lesson[]>([]), [selected, setSelected] = useState<Lesson | null>(null), [notice, setNotice] = useState(''), [error, setError] = useState('')
  useEffect(() => {
    const [year, monthNumber] = month.split('-').map(Number)
    const lastDay = new Date(year, monthNumber, 0).getDate()
    const params = new URLSearchParams({ limit: '500', date_from: `${month}-01`, date_to: `${month}-${String(lastDay).padStart(2, '0')}` })
    api.lessons(params).then(data => { setLessons(data.lessons); setError('') }).catch(e => setError(errorText(e)))
  }, [month, refresh])
  const completed = lessons.filter(row => row.status === 'completed'), paid = completed.filter(row => row.payment_status === 'paid')
  const total = completed.reduce((sum, row) => sum + Number(row.price || 0), 0), unpaid = completed.filter(row => row.payment_status !== 'paid').reduce((sum, row) => sum + Number(row.price || 0), 0)
  return <><Header eyebrow={t('reports', 'Raporty')} title={t('allLessons', 'Wszystkie lekcje')} />{error && <Notice text={error} />}<div className="panel filters"><label>{t('month', 'Miesiąc')}<input type="month" value={month} onChange={e => setMonth(e.target.value)} /></label><button onClick={async () => { try { await api.export(month); setNotice(t('exportSent', 'Plik został wysłany w prywatnej wiadomości z botem.')) } catch (e) { setError(errorText(e)) } }}>{t('sendExcel', 'Wyślij Excel')}</button></div>{notice && <Notice text={notice} success />}<div className="report-summary"><div className="panel"><small>{t('completed', 'Zakończone')}</small><strong>{completed.length}</strong></div><div className="panel"><small>{t('paid', 'Opłacone')}</small><strong>{paid.length}</strong></div><div className="panel"><small>{t('revenue', 'Przychód')}</small><strong>{total.toFixed(2)} PLN</strong></div><div className="panel"><small>{t('debt', 'Zaległość')}</small><strong>{unpaid.toFixed(2)} PLN</strong></div></div><div className="mobile-cards">{lessons.length ? lessons.map(row => <LessonCard key={row.id} lesson={row} onClick={() => setSelected(row)} />) : <Empty>{t('noLessonsMonth', 'Brak lekcji w tym miesiącu')}</Empty>}</div><div className="desktop-table"><div className="table-head"><span>{t('date', 'Data')}</span><span>{t('student', 'Uczeń')}</span><span>{t('teacher', 'Nauczyciel')}</span><span>{t('price', 'Cena')}</span></div>{lessons.map(row => <button className="table-row" key={row.id} onClick={() => setSelected(row)}><span>{formatDate(row.date)} {row.time}</span><span>{row.student_name}</span><span>{row.teacher_name}</span><span>{row.price || '—'} PLN</span></button>)}</div>{selected && <LessonEditor lesson={selected} canManage={false} onClose={() => setSelected(null)} onSaved={() => setSelected(null)} />}</>
}

function SettingsView({ role, name }: { role: Role; name: string }) { return <><Header eyebrow={t('profile', 'Profil')} title={t('settings', 'Ustawienia')} /><div className="panel"><h2>{name}</h2><p>{t('role', 'Rola')}: {role === 'admin' ? t('administrator', 'administrator') : role === 'teacher' ? t('teacher', 'nauczyciel') : t('student', 'uczeń')}</p></div></> }

function MoreView({ role, go }: { role: Role; go: (tab: Tab) => void }) {
  const items: { id: Tab; icon: string; title: string; description: string }[] = role === 'admin'
    ? [{ id: 'teachers', icon: 'N', title: t('teachers', 'Nauczyciele'), description: t('teacherAccounts', 'Konta, stawki i uprawnienia') }, { id: 'reports', icon: 'R', title: t('reports', 'Raporty'), description: t('lessonExports', 'Zajęcia i eksport danych') }, { id: 'settings', icon: 'U', title: t('settings', 'Ustawienia'), description: t('profilePreferences', 'Profil i preferencje') }]
    : [{ id: 'availability', icon: 'D', title: t('availability', 'Dostępność'), description: t('regularHours', 'Stałe godziny i wyjątki') }, { id: 'settings', icon: 'U', title: t('settings', 'Ustawienia'), description: t('profilePreferences', 'Profil i preferencje') }]
  return <><Header eyebrow={t('menu', 'Menu')} title={t('more', 'Więcej')} /><div className="record-list">{items.map(item => <button key={item.id} className="panel student-row record-card" onClick={() => go(item.id)}><span className="student-initial">{item.icon}</span><span><strong>{item.title}</strong><small>{item.description}</small></span><b>›</b></button>)}</div></>
}

const nav: Record<Role, { id: Tab; icon: string; label: string }[]> = {
  admin: [{ id: 'calendar', icon: '▦', label: t('plan', 'Plan') }, { id: 'students', icon: '♙', label: t('students', 'Uczniowie') }, { id: 'requests', icon: '↔', label: t('requests', 'Zgłoszenia') }, { id: 'payments', icon: '₽', label: t('payments', 'Płatności') }, { id: 'more', icon: '•••', label: t('more', 'Więcej') }],
  teacher: [{ id: 'calendar', icon: '▦', label: t('plan', 'Plan') }, { id: 'students', icon: '♙', label: t('students', 'Uczniowie') }, { id: 'requests', icon: '↔', label: t('requests', 'Zgłoszenia') }, { id: 'payments', icon: '₽', label: t('payments', 'Płatności') }, { id: 'more', icon: '•••', label: t('more', 'Więcej') }],
  student: [{ id: 'calendar', icon: '▦', label: t('plan', 'Plan') }, { id: 'account', icon: '₽', label: t('account', 'Konto') }, { id: 'availability', icon: '◷', label: t('availability', 'Dostępność') }, { id: 'requests', icon: '↔', label: t('requests', 'Zgłoszenia') }],
}

export default function App() {
  const [role, setRole] = useState<Role | null>(null), [name, setName] = useState(''), [tab, setTab] = useState<Tab>('calendar'), [refresh, setRefresh] = useState(0), [fatal, setFatal] = useState(''), [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine)
  const reload = () => setRefresh(value => value + 1)
  useEffect(() => { api.bootstrap().then(data => { setRole(data.role); setName(data.user.name) }).catch(e => setFatal(errorText(e))) }, [])
  useEffect(() => {
    const markOnline = () => setOnline(true), markOffline = () => setOnline(false)
    window.addEventListener('online', markOnline)
    window.addEventListener('offline', markOffline)
    return () => { window.removeEventListener('online', markOnline); window.removeEventListener('offline', markOffline) }
  }, [])
  const content = useMemo(() => {
    if (!role) return <Spinner />
    if (tab === 'calendar') return <CalendarView refresh={refresh} canManage={role !== 'student'} onChanged={reload} />
    if (tab === 'teachers') return <TeachersView refresh={refresh} onChanged={reload} />
    if (tab === 'students' || tab === 'account') return <StudentsView refresh={refresh} canManage={role !== 'student'} isAdmin={role === 'admin'} onChanged={reload} />
    if (tab === 'payments') return <PaymentsView refresh={refresh} canReview={role !== 'student'} isAdmin={role === 'admin'} onChanged={reload} />
    if (tab === 'availability') return <AvailabilityView />
    if (tab === 'requests') return <RequestsView role={role} refresh={refresh} onChanged={reload} />
    if (tab === 'reports') return <ReportsView refresh={refresh} />
    if (tab === 'more') return <MoreView role={role} go={setTab} />
    return <SettingsView role={role} name={name} />
  }, [role, tab, refresh])
  if (fatal) return <main className="fatal"><div className="logo">TK</div><h1>{t('openFailed', 'Nie udało się otworzyć aplikacji')}</h1><p>{fatal}</p><button onClick={() => location.reload()}>{t('tryAgain', 'Spróbuj ponownie')}</button></main>
  const hiddenInMore = role === 'admin' ? ['teachers', 'reports', 'settings'] : role === 'teacher' ? ['availability', 'settings'] : []
  return <div className="app-shell">{!online && <div className="connection-banner" role="status">{t('offline', 'Brak połączenia z internetem')}</div>}<main className="page">{content}</main>{role && <nav className="bottom-nav" aria-label={t('mainNavigation', 'Główna nawigacja')}>{nav[role].map(item => { const active = tab === item.id || (item.id === 'more' && hiddenInMore.includes(tab)); return <button key={item.id} className={active ? 'active' : ''} aria-current={active ? 'page' : undefined} onClick={() => setTab(item.id)}><b aria-hidden="true">{item.icon}</b><span>{item.label}</span></button> })}</nav>}</div>
}
