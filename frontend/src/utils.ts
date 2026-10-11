export function isoDate(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function startOfWeek(value: Date): Date {
  const day = new Date(value.getFullYear(), value.getMonth(), value.getDate())
  const offset = (day.getDay() + 6) % 7
  day.setDate(day.getDate() - offset)
  return day
}

export function addDays(value: Date, count: number): Date {
  const result = new Date(value)
  result.setDate(result.getDate() + count)
  return result
}

export function formatDay(value: string): string {
  return new Intl.DateTimeFormat(uiLocale, { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date(`${value}T12:00:00`))
}

export function formatShort(value: string): string {
  return new Intl.DateTimeFormat(uiLocale, { day: '2-digit', month: 'short' }).format(new Date(`${value}T12:00:00`))
}

export function formatDate(value: string): string {
  return value.split('-').reverse().join('.')
}

export const statusText = { planned: t('planned', 'Zaplanowana'), completed: t('completed', 'Zakończona'), cancelled: t('cancelled', 'Anulowana') }
export const paymentText = { unknown: t('unknown', 'Nie określono'), paid: t('paid', 'Opłacono'), unpaid: t('unpaid', 'Nie opłacono') }
import { t, uiLocale } from './i18n'
