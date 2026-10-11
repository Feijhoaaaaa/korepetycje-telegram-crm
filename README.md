# Korepetycje CRM

Telegram bot i Mini App do zarządzania uczniami, kalendarzem lekcji, dostępnością,
płatnościami i zgłoszeniami. Backend działa na FastAPI, bot na aiogram, frontend na
React, a dane są przechowywane w PostgreSQL/Supabase.

## Najważniejsze funkcje

- role administratora, wykładowcy i ucznia;
- wspólny kalendarz, dostępność i obsługa przełożeń;
- rozliczenia lekcji, wielowalutowe wpłaty i potwierdzenia płatności;
- powiadomienia w grupach Telegram oraz przypomnienia o zgłoszeniach;
- eksport historii do XLSX;
- bezpieczne uwierzytelnianie Mini App przez Telegram Init Data.

## Architektura

```text
Telegram / Mini App
        │
        ▼
FastAPI + aiogram ──► PostgreSQL / Supabase
        │
        └────────────► Cloudflare Quick Tunnel
```

Launcher uruchamia API na `127.0.0.1:8080`, tworzy tunel HTTPS, aktualizuje
przyciski Mini App i rozpoczyna polling Telegrama. Blokada pojedynczej instancji
zapobiega uruchomieniu dwóch procesów z tym samym tokenem.

## Wymagania

- Python 3.12+
- Node.js 22+
- PostgreSQL 17 lub projekt Supabase
- `cloudflared`

## Instalacja deweloperska

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env

cd frontend
npm ci
npm run build
cd ..

.venv/bin/alembic upgrade head
.venv/bin/python -m app.launcher
```

Wymagane sekrety znajdują się wyłącznie w lokalnym `.env`, który jest ignorowany
przez Git. `MIGRATION_DATABASE_URL` jest opcjonalnym bezpośrednim połączeniem do
bazy; gdy pozostaje puste, Alembic używa `DATABASE_URL`.

## Ręczna obsługa Oracle Cloud

Pełna instrukcja startu, zatrzymywania, aktualizacji, logów i rollbacku znajduje
się w [docs/OPERATIONS.md](docs/OPERATIONS.md). Usługa produkcyjna jest zdefiniowana
w `deploy/korepetycje-crm.service` i uruchamia się automatycznie po restarcie VM.

## Testy

```bash
.venv/bin/ruff check app tests migrations
.venv/bin/pytest -q

cd frontend
npm test
npm run build
```

Testy integracyjne wymagają wyłącznie osobnej bazy testowej:

```bash
docker compose -f docker-compose.test.yml up -d --wait
TEST_DATABASE_URL='postgresql+asyncpg://tutor_bot:tutor_bot_test@127.0.0.1:55432/tutor_bot_test' \
  .venv/bin/pytest -q
docker compose -f docker-compose.test.yml down -v
```

Nigdy nie ustawiaj produkcyjnej bazy jako `TEST_DATABASE_URL` — fixture testowa
usuwa schemat `tutor_bot`.

## Windows

Powtarzalny pakiet Windows 10/11 x64 buduje:

```powershell
powershell -ExecutionPolicy Bypass -File .\build\windows\build-windows.ps1
```

Wynik trafia do `dist/KorepetycjeCRM-Windows-x64.zip`. Skrypt nigdy nie umieszcza
pliku `.env` w archiwum.

## Bezpieczeństwo

API biznesowe wymaga poprawnie podpisanego `X-Telegram-Init-Data`. Prywatny schemat
`tutor_bot` ma włączone RLS i nie powinien być wystawiany przez Supabase Data API.
Sekrety bota i bazy nie mogą trafiać do frontendu, logów ani repozytorium.

## Licencja

[MIT](LICENSE)
