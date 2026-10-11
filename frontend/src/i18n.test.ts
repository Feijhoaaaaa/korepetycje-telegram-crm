import { describe, expect, it } from 'vitest'
import { resolveUiLanguage } from './i18n'

describe('Telegram UI language', () => {
  it('uses Polish by default and for unsupported languages', () => {
    expect(resolveUiLanguage()).toBe('pl')
    expect(resolveUiLanguage('en')).toBe('pl')
  })

  it('uses Russian for Russian Telegram variants', () => {
    expect(resolveUiLanguage('ru')).toBe('ru')
    expect(resolveUiLanguage('ru-RU')).toBe('ru')
  })
})
