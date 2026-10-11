// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('mobile navigation', () => {
  it('keeps admin navigation to five items and moves secondary screens under More', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      const body = path.includes('/bootstrap')
        ? { timezone: 'Europe/Warsaw', status: 'ok', role: 'admin', capabilities: [], user: { name: 'Admin' } }
        : path.includes('/calendar') ? { lessons: [] } : { pending: [], active: [] }
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))

    render(<App />)
    const navigation = await screen.findByRole('navigation', { name: 'Główna nawigacja' })
    expect(within(navigation).getAllByRole('button')).toHaveLength(5)

    fireEvent.click(within(navigation).getByRole('button', { name: /Więcej/ }))
    expect(await screen.findByRole('heading', { name: 'Więcej' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Nauczyciele/ })).toBeInTheDocument()
    expect(within(navigation).getByRole('button', { name: /Więcej/ })).toHaveAttribute('aria-current', 'page')
  })

  it('uses native date and time controls for a student lesson request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input)
      const body = path.includes('/bootstrap')
        ? { timezone: 'Europe/Warsaw', status: 'ok', role: 'student', capabilities: [], user: { name: 'Uczeń' } }
        : path.includes('/calendar') || path.includes('/lessons') ? { lessons: [] }
          : path.includes('/requests') ? { requests: [] } : { pending: [], active: [] }
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))

    render(<App />)
    const navigation = await screen.findByRole('navigation', { name: 'Główna nawigacja' })
    fireEvent.click(within(navigation).getByRole('button', { name: /Zgłoszenia/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Nowe zajęcie/ }))

    expect(screen.getByLabelText('Dzień')).toHaveAttribute('type', 'date')
    expect(screen.getByLabelText('Godzina')).toHaveAttribute('type', 'time')
    expect(screen.getByRole('button', { name: 'Wyślij zgłoszenie' })).toBeEnabled()
  })
})
