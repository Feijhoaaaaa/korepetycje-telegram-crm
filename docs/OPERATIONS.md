# Ręczna obsługa Korepetycje CRM na Oracle Cloud

Bot działa jako usługa systemowa `korepetycje-crm.service`. Codex nie jest potrzebny
do żadnej z poniższych operacji.

## Połączenie z serwerem

```bash
chmod 600 ~/Downloads/ssh-key-2026-10-11.key
ssh -i ~/Downloads/ssh-key-2026-10-11.key ubuntu@<PUBLICZNY_IP_ORACLE>
```

Publiczny adres IP znajdziesz w OCI: **Compute → Instances → Networking**.

## Codzienna obsługa

```bash
sudo systemctl status korepetycje-crm --no-pager
sudo systemctl start korepetycje-crm
sudo systemctl stop korepetycje-crm
sudo systemctl restart korepetycje-crm

sudo journalctl -u korepetycje-crm -n 100 --no-pager
sudo journalctl -u korepetycje-crm -f

curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/ready
```

Oczekiwane odpowiedzi to `{"status":"ok"}` i `{"status":"ready"}`. Usługa ma
włączony autostart; po restarcie VM nie trzeba uruchamiać jej ręcznie.

## Aktualizacja z GitHuba

```bash
cd /home/ubuntu/korepetycje-telegram-crm
git pull --ff-only

$HOME/.local/bin/uv pip install --python .venv/bin/python -e .

docker run --rm \
  -v "$PWD/frontend:/app" \
  -w /app node:22-alpine \
  sh -c 'npm ci && npm run build && rm -rf node_modules'
sudo chown -R ubuntu:ubuntu frontend/dist

.venv/bin/alembic upgrade head
sudo systemctl restart korepetycje-crm

curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/ready
sudo journalctl -u korepetycje-crm -n 50 --no-pager
```

Po restarcie Cloudflare Quick Tunnel może nadać nowy URL. Launcher automatycznie
zaktualizuje przyciski Mini App użytkowników.

## Cofnięcie nieudanej aktualizacji

```bash
cd /home/ubuntu/korepetycje-telegram-crm
git log --oneline -n 10
git checkout <POPRZEDNI_POPRAWNY_COMMIT>

$HOME/.local/bin/uv pip install --python .venv/bin/python -e .
docker run --rm -v "$PWD/frontend:/app" -w /app node:22-alpine \
  sh -c 'npm ci && npm run build && rm -rf node_modules'
sudo chown -R ubuntu:ubuntu frontend/dist
sudo systemctl restart korepetycje-crm
```

Powrót na najnowszą wersję:

```bash
git switch main
git pull --ff-only
```

Migracji bazy nie cofaj ręcznie. Jeśli aktualizacja zawierała migrację, napraw kod
lub przygotuj osobną migrację naprawczą.

## Pierwsza instalacja na nowej VM

1. Zainstaluj `git`, Docker i `cloudflared`.
2. Zainstaluj `uv` oraz Python 3.12 przez `uv python install 3.12`.
3. Sklonuj repozytorium do `/home/ubuntu/korepetycje-telegram-crm`.
4. Skopiuj `.env.example` jako `.env`, uzupełnij sekrety i wykonaj `chmod 600 .env`.
5. Utwórz `.venv`, zainstaluj zależności i zbuduj frontend komendami z sekcji aktualizacji.
6. Uruchom `.venv/bin/alembic upgrade head`.
7. Skopiuj `deploy/korepetycje-crm.service` do `/etc/systemd/system/`, a następnie:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now korepetycje-crm
```

Nie otwieraj publicznie portów `5432` ani `8080`. W regułach OCI potrzebny jest
tylko port SSH `22`; aplikacja i Cloudflare Tunnel wykonują połączenia wychodzące.
