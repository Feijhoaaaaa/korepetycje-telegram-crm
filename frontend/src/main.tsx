import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
import { uiLanguage } from './i18n'

const webApp = window.Telegram?.WebApp
document.documentElement.lang = uiLanguage
const syncViewport = () => {
  const height = webApp?.viewportStableHeight || webApp?.viewportHeight
  if (height) document.documentElement.style.setProperty('--app-height', `${height}px`)
}
webApp?.ready()
webApp?.expand()
syncViewport()
webApp?.onEvent?.('viewportChanged', syncViewport)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
