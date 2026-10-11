import { describe, expect, it } from 'vitest'
import { addDays, formatDate, isoDate, startOfWeek } from './utils'

describe('week navigation', () => {
  it('starts on Monday', () => {
    expect(isoDate(startOfWeek(new Date(2026, 8, 23)))).toBe('2026-09-21')
  })

  it('moves by complete weeks', () => {
    expect(isoDate(addDays(new Date(2026, 8, 21), 7))).toBe('2026-09-28')
  })

  it('formats report dates as dd.mm.yyyy', () => {
    expect(formatDate('2026-09-30')).toBe('30.09.2026')
  })
})
