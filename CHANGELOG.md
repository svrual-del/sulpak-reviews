# Changelog

Все значимые изменения проекта документируются здесь. Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## 2026-04-22

### Добавлено
- **Vision-проверка медиа теперь запускается всегда** при `approve + has_media`, независимо от `AUTO_PUBLISH` и порога confidence. Это даёт модератору полную статистику по качеству приложенных фото даже в режиме помощника.
- **Учёт токенов Claude API.** Функции `moderate_review` и `moderate_media` возвращают поле `_usage` с `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. Данные пишутся в jsonl как `usage: {text: {...}, vision: {...}}`.
- **Блок «💰 Стоимость Claude API»** в ежедневном отчёте: раздельные траты на текстовую модерацию и на vision, итоговая сумма за день и средняя стоимость одного отзыва (в USD и в ₸ по курсу 450).
- Разбивка публикации в саммари отчёта: `Опубликовано: N (без медиа: X, с медиа: Y)`.
- Блок «📷 Медиа в отзывах» в отчёте: vision-вердикты по категориям ок/отклонено/на проверку + проценты.
- Таблица «❌ Медиа требуют ручной чистки в CMS» — для отзывов, где текст опубликован, а vision отклонил фото.
- Новый скрипт [`test_media_vision.py`](test_media_vision.py) — ручной dry-run vision-проверки последних писем без влияния на основной пайплайн.

### Изменено
- Парсинг письма собирает прямые URL из ссылок `Медиа 1..N` в новое поле `review.media_urls`.
- Категория письма в Exchange стала составной: `✅ Опубликовано ИИ | 📷 фото ок 95%` (при `approve` с медиа).
- При vision-вердикте `reject` письмо остаётся непрочитанным с high importance — сигнал модератору почистить медиа в CMS.

### Исправлено
- Метка `"медиа"` в таблице отзыва больше не матчится на ссылки `Медиа N` (ложное перезаписывание поля `review.media`).

Коммиты: [`24863bc`](https://github.com/svrual-del/sulpak-reviews/commit/24863bc), [`76be8ee`](https://github.com/svrual-del/sulpak-reviews/commit/76be8ee), [`5d64782`](https://github.com/svrual-del/sulpak-reviews/commit/5d64782).

## 2026-04-19

### Добавлено
- Первая версия: парсинг отзывов из Exchange, текстовая модерация через Claude Sonnet 4, логирование в JSONL, ежедневный HTML-отчёт на email, задачи в Windows Task Scheduler.

Коммит: [`0724458`](https://github.com/svrual-del/sulpak-reviews/commit/0724458).
