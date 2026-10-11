from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import MenuButtonWebApp, WebAppInfo
from sqlalchemy import func, select

from app.bot.handlers import create_router, mini_app_keyboard
from app.bot.middleware import DatabaseMiddleware
from app.bot.webapp import WebAppUrl
from app.config import Settings, get_settings
from app.database.models import Student, Teacher
from app.database.session import check_database, create_engine, create_session_factory
from app.services.chats import ChatService
from app.services.reminders import run_reminder_once
from app.utils.resources import executable_directory, resource_path
from app.web.api import create_web_app

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_tunnel_url(line: str) -> str | None:
    match = TUNNEL_URL_RE.search(line)
    return match.group(0) if match else None


class SingleInstanceLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(tempfile.gettempdir()) / "telebotkor-launcher.lock"
        self._file = None

    def acquire(self) -> None:
        self._file = self.path.open("a+")
        try:
            if sys.platform == "win32":
                self._file.seek(0)
                self._file.write("0")
                self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self._file.close()
            self._file = None
            raise RuntimeError("Korepetycje CRM jest już uruchomiony.") from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()

    def release(self) -> None:
        if self._file is not None:
            if sys.platform == "win32":
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()


class CloudflaredTunnel:
    def __init__(self, executable: str, local_url: str) -> None:
        self.executable = executable
        self.local_url = local_url
        self.process: asyncio.subprocess.Process | None = None

    async def start(self, on_url: Callable[[str], Awaitable[None]], ready: asyncio.Event) -> int:
        self.process = await asyncio.create_subprocess_exec(
            self.executable,
            "tunnel",
            "--url",
            self.local_url,
            "--no-autoupdate",
            "--protocol",
            "http2",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        if self.process.stderr is None:
            raise RuntimeError("cloudflared stderr pipe was not created")
        pending_url: str | None = None
        async for raw_line in self.process.stderr:
            line = raw_line.decode(errors="replace").rstrip()
            logger.info("cloudflared: %s", line)
            url = extract_tunnel_url(line)
            if url:
                pending_url = url
            if pending_url and "Registered tunnel connection" in line:
                await on_url(pending_url)
                ready.set()
        return await self.process.wait()

    async def stop(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=8)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


async def supervise_tunnel(
    settings: Settings,
    on_url: Callable[[str], Awaitable[None]],
    ready: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    delay = 1
    while not stop.is_set():
        tunnel = CloudflaredTunnel(
            settings.cloudflared_path,
            f"http://{settings.web_host}:{settings.web_port}",
        )
        try:
            code = await tunnel.start(on_url, ready)
            if not stop.is_set():
                logger.warning("Tunel zakończył pracę (kod %s); ponawiam za %ss", code, delay)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Nie znaleziono {settings.cloudflared_path!r}. Zainstaluj cloudflared lub ustaw CLOUDFLARED_PATH."
            ) from exc
        finally:
            await tunnel.stop()
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 15)


async def supervise_reminders(factory, bot: Bot, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with factory() as session:
                while await run_reminder_once(session, bot, settings):
                    pass
        except Exception:
            logger.exception("Scheduler przypomnień zakończył przebieg błędem")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.reminder_poll_seconds)
        except TimeoutError:
            pass


async def run_launcher(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    bundled_cloudflared = resource_path("cloudflared.exe")
    if settings.cloudflared_path == "cloudflared" and bundled_cloudflared.is_file():
        settings.cloudflared_path = str(bundled_cloudflared)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    bot = Bot(token=settings.bot_token.get_secret_value())
    stop = asyncio.Event()
    ready = asyncio.Event()
    web_url = WebAppUrl()
    server: uvicorn.Server | None = None
    tunnel_task: asyncio.Task | None = None
    reminder_task: asyncio.Task | None = None
    polling_task: asyncio.Task | None = None
    server_task: asyncio.Task | None = None

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async def publish_url(url: str) -> None:
        changed = web_url.get() != url
        web_url.set(url)
        async with factory() as session:
            teacher_ids = list(await session.scalars(select(Teacher.telegram_user_id).where(Teacher.active.is_(True))))
            student_ids = list(
                await session.scalars(
                    select(Student.telegram_user_id).where(
                        Student.active.is_(True), Student.telegram_user_id.is_not(None)
                    )
                )
            )
            missing_students = await session.scalar(
                select(func.count()).select_from(Student).where(
                    Student.active.is_(True), Student.telegram_user_id.is_(None)
                )
            )
        logger.info(
            "Synchronizacja dostępu Mini App: %d wykładowców, %d uczniów%s",
            len(teacher_ids),
            len(student_ids),
            f", {missing_students} uczniów oczekuje na połączenie" if missing_students else "",
        )
        for chat_id in set(teacher_ids + student_ids + [settings.owner_telegram_id]):
            try:
                await bot.set_chat_menu_button(
                    chat_id=chat_id,
                    menu_button=MenuButtonWebApp(text="Открыть приложение", web_app=WebAppInfo(url=url)),
                )
            except Exception as exc:
                logger.warning("Nie udało się zaktualizować menu Telegram %s: %s", chat_id, exc)
        if changed:
            logger.info("Mini App URL: %s", url)

    try:
        await check_database(engine)
        async with factory() as session:
            await ChatService(session).ensure_owner_teacher(settings.owner_telegram_id)
            await session.commit()
        web_app = create_web_app(settings, engine=engine, bot=bot)
        server = uvicorn.Server(
            uvicorn.Config(
                web_app,
                host=settings.web_host,
                port=settings.web_port,
                log_level=settings.log_level.lower(),
            )
        )
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve(), name="api")
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.05)

        tunnel_task = asyncio.create_task(supervise_tunnel(settings, publish_url, ready, stop), name="cloudflared")
        try:
            await asyncio.wait_for(ready.wait(), timeout=45)
        except TimeoutError as exc:
            raise RuntimeError("Cloudflare Quick Tunnel nie zwrócił adresu HTTPS w ciągu 45 sekund.") from exc
        if tunnel_task.done():
            await tunnel_task

        dispatcher = Dispatcher()
        middleware = DatabaseMiddleware(factory)
        dispatcher.message.outer_middleware(middleware)
        dispatcher.callback_query.outer_middleware(middleware)
        dispatcher.include_router(create_router(settings, web_url.get))
        await bot.send_message(
            settings.owner_telegram_id,
            "✅ Korepetycje CRM запущен.",
            reply_markup=mini_app_keyboard(web_url.get() or "https://example.invalid"),
        )
        polling_task = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False), name="telegram-polling")
        reminder_task = asyncio.create_task(
            supervise_reminders(factory, bot, settings, stop), name="request-reminders"
        )

        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {stop_task, polling_task, tunnel_task, server_task, reminder_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not stop.is_set() and stop_task not in done:
            for task in done:
                if task.cancelled():
                    continue
                error = task.exception()
                if error:
                    raise error
                if task is polling_task or task is server_task or task is reminder_task:
                    raise RuntimeError(f"Proces {task.get_name()} nieoczekiwanie zakończył pracę.")
    finally:
        stop.set()
        if server is not None:
            server.should_exit = True
        for task in (polling_task, tunnel_task, reminder_task):
            if task and not task.done():
                task.cancel()
        for task in (polling_task, tunnel_task, server_task):
            if task:
                await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(executable_directory())
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    lock = SingleInstanceLock()
    try:
        with lock:
            asyncio.run(run_launcher())
    except (KeyboardInterrupt, RuntimeError) as exc:
        if str(exc):
            print(f"Korepetycje CRM: {exc}", file=sys.stderr)
        if isinstance(exc, RuntimeError):
            raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
