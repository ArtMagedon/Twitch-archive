# Twitch Archive

Docker-сервис для мониторинга публичных Twitch-каналов и записи прямых эфиров в реальном времени. Управление каналами и архивом доступно через веб-интерфейс.

## Возможности

- Мониторинг настраиваемого списка Twitch-каналов через Helix API.
- Автоматический запуск Streamlink при обнаружении публичного эфира.
- Запись в качестве `best` в MPEG-TS (`.ts`).
- React + Vite frontend, FastAPI backend, SQLite.
- Docker Compose; данные и видео хранятся на хосте в `./data` и `./recordings`.
- Нет автоматического удаления архива и программного лимита хранения.
- Восстановление состояния записей после аварийного перезапуска контейнера.

## Требования

- Docker Engine и Docker Compose plugin.
- Twitch Developer Application с Client ID и Client Secret.
- Достаточно свободного места на диске. При отсутствии автоочистки объём архива будет постоянно расти.

## Установка

1. Склонируйте репозиторий и перейдите в каталог проекта.
2. Создайте конфигурацию: `cp .env.example .env`.
3. Создайте приложение в [Twitch Developer Console](https://dev.twitch.tv/console/apps) и укажите `TWITCH_CLIENT_ID` и `TWITCH_CLIENT_SECRET` в `.env`.
4. Запустите: `docker compose up -d --build`.
5. Откройте `http://SERVER_IP:8080`.

Для OAuth Redirect URL можно указать `http://localhost`: пользовательский OAuth-поток не используется. Приложение получает App Access Token по Client Credentials Flow для запросов к публичному Helix API.

## Конфигурация

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `TWITCH_CLIENT_ID` | Client ID приложения Twitch | — |
| `TWITCH_CLIENT_SECRET` | Client Secret приложения Twitch | — |
| `POLL_INTERVAL_SECONDS` | Интервал проверки эфиров (минимум 15 секунд) | `45` |
| `STREAM_QUALITY` | Качество Streamlink | `best` |
| `TZ` | Часовой пояс контейнера | `Europe/Berlin` |

## Хранение

- `./data/archive.db` — база SQLite.
- `./recordings/<channel>/<YYYY-MM-DD>/` — видеофайлы.

Записи не удаляются автоматически. Удаление записи из интерфейса удаляет соответствующий файл. Удаление канала удаляет его метаданные и историю из SQLite, но оставляет видеофайлы на диске.

## Команды

```bash
docker compose up -d --build
docker compose logs -f twitch-archive
docker compose ps
docker compose down
```

`docker compose down` не удаляет каталоги `data` и `recordings`, так как они подключены как bind mounts.

## API

Документация FastAPI: `http://SERVER_IP:8080/docs`.
Основные маршруты: `/api/health`, `/api/settings`, `/api/status`, `/api/channels`, `/api/recordings`.

## Ограничения и правовые аспекты

Запись начинается после обнаружения трансляции очередным циклом мониторинга, поэтому фрагмент эфира до обнаружения не сохраняется. Сервис не восстанавливает пропущенные части, не скачивает скрытые или удалённые VOD, не обходит ограничения доступа и не получает доступ к приватным трансляциям. Используйте его только для публичного контента, на который у вас есть соответствующие права, соблюдая условия Twitch и применимое законодательство.

Первая версия не записывает чат. OAuth приложения используется только для доступа к публичным данным Helix API, а не для авторизации от имени пользователя.
