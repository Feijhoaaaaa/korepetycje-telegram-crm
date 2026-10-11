KOREPETYCJE CRM — WINDOWS 10/11 x64
===================================

1. Jeżeli pakiet nie zawiera pliku .env, skopiuj .env.example jako .env
   i wpisz BOT_TOKEN, OWNER_TELEGRAM_ID oraz DATABASE_URL.
2. Uruchom „Uruchom Korepetycje CRM.bat”.
3. Opcjonalnie uruchom „Utworz skrot na pulpicie.cmd”, aby dodać ikonę.
4. Nie zamykaj konsoli podczas korzystania z Mini App.
5. Ctrl+C zatrzymuje bota, API oraz Cloudflare Tunnel.

Program zawiera własny interpreter Python i cloudflared.exe. Nie wymaga
instalowania Pythona, Node.js ani Cloudflare na komputerze docelowym.

UWAGA: plik .env zawiera prywatne dane. Nie wysyłaj go innym osobom.
Skrypt budowania nigdy nie dołącza pliku .env do archiwum.

Windows Defender może po raz pierwszy zapytać o dostęp do sieci lokalnej.
Zezwól dla sieci prywatnej. Niezależnie od tego Quick Tunnel wymaga dostępu
wychodzącego HTTPS/QUIC do Cloudflare oraz HTTPS do Telegrama i Supabase.
