interface TelegramButton {
  show(): void
  hide(): void
  setText(text: string): void
  onClick(callback: () => void): void
  offClick(callback: () => void): void
  enable(): void
  disable(): void
  showProgress(leaveActive?: boolean): void
  hideProgress(): void
}

interface TelegramWebApp {
  initData: string
  initDataUnsafe?: { user?: { language_code?: string } }
  viewportHeight?: number
  viewportStableHeight?: number
  ready(): void
  expand(): void
  close(): void
  onEvent?(event: 'viewportChanged' | 'themeChanged', callback: () => void): void
  offEvent?(event: 'viewportChanged' | 'themeChanged', callback: () => void): void
  BackButton: TelegramButton
  MainButton: TelegramButton
  HapticFeedback?: {
    impactOccurred(style: 'light' | 'medium' | 'heavy'): void
    notificationOccurred(type: 'error' | 'success' | 'warning'): void
  }
}

interface Window {
  Telegram?: { WebApp: TelegramWebApp }
}
