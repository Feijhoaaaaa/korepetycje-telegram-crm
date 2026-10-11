export type Role = 'admin' | 'teacher' | 'student'
export type LessonStatus = 'planned' | 'completed' | 'cancelled'
export type PaymentStatus = 'unknown' | 'paid' | 'unpaid'

export interface Lesson {
  id: number
  study_chat_id: number
  student_id: number
  teacher_id: number
  student_name: string | null
  teacher_name: string | null
  telegram_chat_title: string | null
  datetime: string
  date: string
  time: string
  duration_minutes: number
  price: string | null
  price_missing: boolean
  status: LessonStatus
  payment_status: PaymentStatus
}

export interface Student {
  id: number
  student_id: number
  teacher_id: number
  teacher_name: string | null
  telegram_chat_id: number
  telegram_chat_title: string | null
  student_name: string | null
  student_telegram_id: number | null
  balance?: string
  assigned: boolean
  active: boolean
}

export interface Teacher {
  id: number
  telegram_user_id: number
  name: string
  active: boolean
  is_admin: boolean
  default_price: string | null
  default_duration_minutes: number
  quiet_hours_start: string | null
  quiet_hours_end: string | null
}

export interface Receipt {
  id: number
  student_id: number
  teacher_id: number
  student_name: string
  file_type: 'photo' | 'document'
  status: 'pending' | 'approved' | 'rejected'
  submitted_amount: string | null
  amount: string | null
  currency: 'PLN' | 'UAH' | 'EUR'
  rejection_reason: string | null
  created_at: string
}

export interface ServiceRequest {
  id: number
  type: 'payment_confirmation' | 'lesson_reschedule' | 'lesson_cancellation' | 'lesson_time_proposal' | 'other'
  status: 'pending' | 'approved' | 'rejected' | 'cancelled' | 'expired'
  student_id: number
  teacher_id: number
  student_name: string
  lesson_id: number | null
  payment_id: number | null
  comment: string | null
  decision_reason: string | null
  phase: 'teacher_review' | 'student_confirmation' | 'resolved'
  duration_minutes: number | null
  study_chat_id: number | null
  current_datetime: string | null
  proposed_datetime: string | null
  submitted_amount: string | null
  amount: string | null
  currency: 'PLN' | 'UAH' | 'EUR'
  created_at: string
  resolved_at: string | null
  next_reminder_at: string | null
  last_reminder_at: string | null
  reminder_count: number
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === 'string' ? detail : 'Ошибка запроса')
  }
}

const REQUEST_TIMEOUT_MS = 15000
const initData = () => window.Telegram?.WebApp.initData ?? ''

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'X-Telegram-Init-Data': initData(),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })
    if (!response.ok) {
      let detail: unknown = response.statusText
      try { detail = (await response.json()).detail } catch { /* response is not JSON */ }
      throw new ApiError(response.status, detail)
    }
    return response.status === 204 ? undefined as T : response.json() as Promise<T>
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(error instanceof DOMException && error.name === 'AbortError' ? 408 : 0, error instanceof DOMException && error.name === 'AbortError' ? 'request_timeout' : 'network_unavailable')
  } finally {
    window.clearTimeout(timeout)
  }
}

export const api = {
  bootstrap: () => request<{ timezone: string; status: string; role: Role; capabilities: string[]; user: { name: string } }>('/bootstrap'),
  settings: () => request<{ quiet_hours_start: string | null; quiet_hours_end: string | null }>('/settings'),
  saveQuietHours: (start: string | null, end: string | null) => request('/settings/quiet-hours', { method: 'PUT', body: JSON.stringify({ start, end }) }),
  calendar: (start: string, end: string) => request<{ lessons: Lesson[] }>(`/calendar?start=${start}&end=${end}`),
  students: (includeArchived = false) => request<{ pending: Student[]; active: Student[] }>(`/students${includeArchived ? '?include_archived=true' : ''}`),
  student: (id: number) => request<Student & { balance: string; balances: Record<string, string>; available_pool: string; account_history: { datetime: string; kind: string; description: string; amount: string; balance: string; currency: string }[]; upcoming: Lesson[]; history: Lesson[] }>(`/students/${id}`),
  assignStudent: (id: number, name: string) => request<{ student: Student; warnings: string[] }>(`/students/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  createStudentLink: (id: number) => request<{ token: string; url: string; expires_in_seconds: number }>(`/students/${id}/link`, { method: 'POST' }),
  pendingLinks: () => request<{ links: { id: number; student_id: number; teacher_id: number; student_name: string; telegram_user_id: number; expires_at: string }[] }>('/student-links/pending'),
  approveLink: (id: number) => request<{ student_id: number; telegram_user_id: number }>(`/student-links/${id}/approve`, { method: 'POST' }),
  transferStudent: (studentId: number, teacherId: number) => request(`/admin/students/${studentId}/teacher`, { method: 'PATCH', body: JSON.stringify({ teacher_id: teacherId }) }),
  teachers: () => request<{ teachers: Teacher[] }>('/admin/teachers'),
  saveTeacher: (teacher: Omit<Teacher, 'id'>, id?: number) => request<Teacher>(id ? `/admin/teachers/${id}` : '/admin/teachers', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(teacher) }),
  archiveTeacher: (id: number) => request<Teacher>(`/admin/teachers/${id}/archive`, { method: 'POST' }),
  restoreTeacher: (id: number) => request<Teacher>(`/admin/teachers/${id}/restore`, { method: 'POST' }),
  archiveStudent: (id: number) => request(`/admin/students/${id}/archive`, { method: 'POST' }),
  restoreStudent: (id: number) => request(`/admin/students/${id}/restore`, { method: 'POST' }),
  deleteStudent: (id: number) => request<{ student_id: number; deleted: boolean; lessons_preserved: boolean }>(`/admin/students/${id}`, { method: 'DELETE' }),
  deleteTeacher: (id: number) => request<{ teacher_id: number; deleted: boolean; lessons_preserved: boolean }>(`/admin/teachers/${id}`, { method: 'DELETE' }),
  lessons: (params: URLSearchParams) => request<{ lessons: Lesson[] }>(`/lessons?${params}`),
  createLesson: (data: { study_chat_id: number; date: string; time: string; duration_minutes: number; price: string | null; force: boolean }) => request<{ lesson: Lesson; warnings: string[] }>('/lessons', { method: 'POST', body: JSON.stringify(data) }),
  reschedule: (id: number, data: { date: string; time: string; duration_minutes: number; force: boolean }) => request<{ lesson: Lesson; warnings: string[] }>(`/lessons/${id}/reschedule`, { method: 'PATCH', body: JSON.stringify(data) }),
  editLesson: (id: number, data: Record<string, unknown>) => request<{ lesson: Lesson }>(`/lessons/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  action: (id: number, action: 'complete' | 'cancel') => request<{ lesson: Lesson; warnings: string[] }>(`/lessons/${id}/${action}`, { method: 'POST' }),
  remove: (id: number) => request<{ warnings: string[] }>(`/lessons/${id}`, { method: 'DELETE' }),
  payment: (id: number, payment_status: 'paid' | 'unpaid') => request<{ lesson: Lesson }>(`/lessons/${id}/payment`, { method: 'PATCH', body: JSON.stringify({ payment_status }) }),
  unpaid: () => request<{ students: { student_id: number; study_chat_id: number; student_name: string; lessons: Lesson[] }[]; total: number }>('/payments/unpaid'),
  receipts: (status = '') => request<{ receipts: Receipt[] }>(`/payments/receipts${status ? `?status=${status}` : ''}`),
  approveReceipt: (id: number, amount: string, lessonIds: number[], currency: Receipt['currency'] = 'PLN') => request<Receipt>(`/payments/receipts/${id}/approve`, { method: 'POST', body: JSON.stringify({ amount, lesson_ids: lessonIds, currency }) }),
  rejectReceipt: (id: number, reason: string) => request<Receipt>(`/payments/receipts/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) }),
  correctReceipt: (id: number, status: 'approved' | 'rejected', amount: string | null, reason = '', currency: Receipt['currency'] = 'PLN') => request<Receipt>(`/admin/payments/${id}`, { method: 'PATCH', body: JSON.stringify({ status, amount, reason, currency }) }),
  availability: () => request<{ rules: { id: number; weekday: number; start_time: string; end_time: string }[]; exceptions: { id: number; date: string; available: boolean; start_time: string | null; end_time: string | null }[] }>('/availability'),
  saveAvailability: (rules: { weekday: number; start_time: string; end_time: string }[]) => request('/availability', { method: 'PUT', body: JSON.stringify({ rules }) }),
  addException: (data: { date: string; available: boolean; start_time: string | null; end_time: string | null }) => request('/availability/exceptions', { method: 'POST', body: JSON.stringify(data) }),
  deleteException: (date: string) => request(`/availability/exceptions/${date}`, { method: 'DELETE' }),
  requests: (type = '', status = '') => {
    const params = new URLSearchParams()
    if (type) params.set('type', type)
    if (status) params.set('status', status)
    return request<{ requests: ServiceRequest[] }>(`/requests${params.size ? `?${params}` : ''}`)
  },
  rescheduleRequests: () => request<{ requests: { id: number; lesson_id: number; student_name: string; proposed_datetime: string; status: string; reason: string | null }[] }>('/reschedule-requests'),
  requestReschedule: (lessonId: number, data: { date: string; time: string; duration_minutes: number; reason: string }) => request(`/lessons/${lessonId}/reschedule-requests`, { method: 'POST', body: JSON.stringify(data) }),
  requestCancellation: (lessonId: number, reason = '') => request(`/lessons/${lessonId}/cancellation-requests`, { method: 'POST', body: JSON.stringify({ reason }) }),
  requestTimeProposal: (lessonId: number, data: { date: string; time: string; duration_minutes: number; reason: string }) => request(`/lessons/${lessonId}/time-proposals`, { method: 'POST', body: JSON.stringify(data) }),
  requestNewTimeProposal: (data: { date: string; time: string; duration_minutes: number; study_chat_id?: number; reason: string }) => request('/time-proposals', { method: 'POST', body: JSON.stringify(data) }),
  approveRequest: (id: number, force = false) => request<ServiceRequest>(`/requests/${id}/approve`, { method: 'POST', body: JSON.stringify({ force }) }),
  rejectRequest: (id: number, reason = '') => request<ServiceRequest>(`/requests/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) }),
  counterRequest: (id: number, data: { date: string; time: string; duration_minutes?: number; force?: boolean; reason?: string }) => request<ServiceRequest>(`/requests/${id}/counter`, { method: 'POST', body: JSON.stringify(data) }),
  confirmRequest: (id: number, force = false) => request<ServiceRequest>(`/requests/${id}/confirm`, { method: 'POST', body: JSON.stringify({ force }) }),
  cancelRequest: (id: number) => request(`/requests/${id}/cancel`, { method: 'POST' }),
  remindLater: (id: number, minutes: 60 | 180 | 1440) => request<ServiceRequest>(`/requests/${id}/remind-later`, { method: 'POST', body: JSON.stringify({ minutes }) }),
  decideReschedule: (id: number, action: 'accept' | 'reject' | 'cancel', reason = '', force = false) => request(`/reschedule-requests/${id}/${action}`, { method: 'POST', body: JSON.stringify({ reason, force }) }),
  export: (month?: string) => request<{ sent: boolean; filename: string }>(`/exports/send${month ? `?month=${month}` : ''}`, { method: 'POST' }),
}
