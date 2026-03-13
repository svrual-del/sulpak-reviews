"""
Автомодерация отзывов Sulpak
Exchange (EWS) → парсинг письма → Claude API → публикация / пометка

Режимы работы:
  AUTO_PUBLISH = False  → только логирует рекомендации (помощник модератора)
  AUTO_PUBLISH = True   → автоматически публикует approve с confidence ≥ CONFIDENCE_THRESHOLD
"""

import os
from dotenv import load_dotenv
from exchangelib import (
    Credentials, Account, Configuration, DELEGATE,
    EWSDateTime, EWSTimeZone
)
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from bs4 import BeautifulSoup
import anthropic
import requests
import json
import logging
from datetime import datetime
from dataclasses import dataclass

# Загружаем переменные окружения из .env (рядом со скриптом)
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"), override=True)

# ============================================================
# КОНФИГУРАЦИЯ — все секреты читаются из .env файла
# ============================================================

EWS_SERVER = os.getenv("EWS_SERVER", "mail2016.sulpak.kz")
EWS_EMAIL = os.getenv("EWS_EMAIL", "")
EWS_USERNAME = os.getenv("EWS_USERNAME", "")
EWS_PASSWORD = os.getenv("EWS_PASSWORD", "")

SKIP_SSL_VERIFY = os.getenv("SKIP_SSL_VERIFY", "false").lower() == "true"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "false").lower() == "true"
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
SUBJECT_FILTER = "Новый отзыв на сайте sulpak.kz"
REVIEW_FOLDER = os.getenv("REVIEW_FOLDER", "Сайт отзывы на товар")

LOG_FILE = f"moderation_log_{datetime.now().strftime('%Y%m%d')}.jsonl"

# ============================================================
# СИСТЕМНЫЙ ПРОМПТ
# ============================================================

SYSTEM_PROMPT = """Ты — модератор отзывов на товары интернет-магазина Sulpak (Казахстан). Твоя задача — проанализировать отзыв покупателя и дать рекомендацию: опубликовать, отклонить или отправить на ручную проверку.

## Правила модерации

### ОТКЛОНИТЬ отзыв, если:
1. **Отзыв не о товаре, а о компании/сервисе** — описание процесса покупки, доставки, работы менеджеров, обслуживания в магазине, возврата, гарантийного ремонта. Если более 70% текста посвящено сервису, а не товару — отклонить.
2. **Отзыв не соответствует товару** — описание характеристик или опыта использования явно не совпадает с указанным товаром.
3. **Отзыв слишком короткий и неинформативный** — менее 2 предложений, не содержит конкретики о товаре (например: «Норм», «Хороший товар», «👍»).
4. **Спам или реклама** — ссылки на сторонние сайты, реклама других магазинов или товаров, промокоды.
5. **Нецензурная лексика, оскорбления, угрозы** — в любой форме, включая замаскированную.
6. **Персональные данные** — телефоны, адреса, полные ФИО сотрудников или других лиц.

### ОДОБРИТЬ отзыв, если:
1. Отзыв содержит описание опыта использования товара.
2. Упоминаются конкретные характеристики, плюсы или минусы товара.
3. Отзыв информативен и поможет другим покупателям в выборе.
4. Допускается краткое упоминание сервиса (1-2 предложения), если основная часть — о товаре.

### ОТПРАВИТЬ НА РУЧНУЮ ПРОВЕРКУ, если:
1. Отзыв пограничный — примерно 50/50 о товаре и сервисе.
2. Есть сомнения в соответствии отзыва товару.
3. Отзыв содержит жалобу на брак/дефект — может быть ценен, но требует проверки.
4. Текст на другом языке (не русский/казахский) или сильно неразборчив.

## Формат ответа

Ответ строго в формате JSON, без дополнительного текста:

{
  "decision": "approve" | "reject" | "manual_review",
  "confidence": 0.0-1.0,
  "reason_code": "код причины",
  "reason": "краткое пояснение на русском для модератора",
  "content_summary": "о чём отзыв в 1 предложение",
  "service_ratio": 0.0-1.0,
  "flags": ["список обнаруженных проблем"]
}

## Коды причин (reason_code):
- "ok" — всё в порядке
- "service_not_product" — отзыв о сервисе, а не о товаре
- "wrong_product" — не соответствует товару
- "too_short" — слишком короткий/неинформативный
- "spam" — спам или реклама
- "profanity" — нецензурная лексика
- "personal_data" — содержит персональные данные
- "mixed_content" — смешанный контент (товар + сервис)
- "defect_claim" — жалоба на брак
- "unclear" — неразборчиво или неоднозначно"""

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            f"moderation_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        )
    ]
)
log = logging.getLogger("sulpak_moderation")

# Подавляем лишний вывод exchangelib и SSL-предупреждения
logging.getLogger("exchangelib").setLevel(logging.WARNING)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# СТРУКТУРА ДАННЫХ
# ============================================================

@dataclass
class Review:
    name: str = ""
    rating: str = ""
    pros: str = ""
    cons: str = ""
    text: str = ""
    phone: str = ""
    media: str = ""
    source: str = ""
    link_product: str = ""
    link_cms: str = ""
    link_publish: str = ""       # "Отображать на сайте"
    link_cms_media: str = ""
    email_id: str = ""
    email_date: str = ""

# ============================================================
# ПОДКЛЮЧЕНИЕ К EXCHANGE
# ============================================================

def connect_exchange() -> Account:
    """Подключается к Exchange через EWS."""

    if SKIP_SSL_VERIFY:
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

    credentials = Credentials(
        username=EWS_USERNAME,
        password=EWS_PASSWORD
    )

    config = Configuration(
        server=EWS_SERVER,
        credentials=credentials
    )

    account = Account(
        primary_smtp_address=EWS_EMAIL,
        config=config,
        autodiscover=False,
        access_type=DELEGATE
    )

    return account

# ============================================================
# ПАРСИНГ ПИСЬМА
# ============================================================

def parse_review_email(item) -> Review:
    """Парсит HTML-письмо с отзывом в структуру Review."""
    review = Review()

    html_body = item.body or ""
    if not html_body:
        log.warning("Пустое тело письма")
        return review

    soup = BeautifulSoup(html_body, "html.parser")

    # Парсим таблицу с данными отзыва
    rows = soup.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)

            if "имя" in label:
                review.name = value
            elif "общая оценка" in label:
                review.rating = value
            elif "плюсы" in label:
                review.pros = value
            elif "минусы" in label:
                review.cons = value
            elif "текст отзыва" in label:
                review.text = value
            elif "номер телефона" in label:
                review.phone = value
            elif "медіа" in label or "медиа" in label:
                review.media = value
            elif "источник" in label:
                review.source = value

    # Парсим ссылки
    links = soup.find_all("a")
    for link in links:
        href = link.get("href", "")
        text = link.get_text(strip=True).lower()

        if "показать на сайте страницу" in text:
            review.link_product = href
        elif "показать в cms текстовый" in text:
            review.link_cms = href
        elif "отображать на сайте" in text:
            review.link_publish = href
        elif "показать в cms медиа" in text:
            review.link_cms_media = href

    # Метаданные письма
    review.email_id = str(item.id) if item.id else ""
    review.email_date = str(item.datetime_received or "")

    return review

# ============================================================
# МОДЕРАЦИЯ ЧЕРЕЗ CLAUDE API
# ============================================================

def moderate_review(review: Review) -> dict:
    """Отправляет отзыв в Claude API и получает решение."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    review_text = f"""Имя: {review.name}
Оценка: {review.rating}
Плюсы: {review.pros}
Минусы: {review.cons}
Текст отзыва: {review.text}
Медиа файлы: {review.media}
Источник: {review.source}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": review_text}
            ]
        )

        response_text = response.content[0].text.strip()
        # Убираем возможные markdown-обёртки
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0]

        return json.loads(response_text)

    except json.JSONDecodeError as e:
        log.error(f"Ошибка парсинга JSON от Claude: {e}")
        return _error_result("Ошибка парсинга ответа от ИИ", ["parse_error"])
    except Exception as e:
        log.error(f"Ошибка вызова Claude API: {e}")
        return _error_result(f"Ошибка API: {str(e)}", ["api_error"])


def _error_result(reason: str, flags: list) -> dict:
    return {
        "decision": "manual_review",
        "confidence": 0.0,
        "reason_code": "unclear",
        "reason": reason,
        "content_summary": "",
        "service_ratio": 0.0,
        "flags": flags
    }

# ============================================================
# ПУБЛИКАЦИЯ ОТЗЫВА
# ============================================================

def publish_review(review: Review) -> bool:
    """Публикует отзыв, переходя по ссылке."""
    if not review.link_publish:
        log.error("Нет ссылки для публикации")
        return False

    try:
        response = requests.get(review.link_publish, timeout=30)
        if response.status_code == 200:
            log.info(f"✅ Отзыв от {review.name} опубликован")
            return True
        else:
            log.error(f"Ошибка публикации: HTTP {response.status_code}")
            return False
    except Exception as e:
        log.error(f"Ошибка при публикации: {e}")
        return False

# ============================================================
# ЛОГИРОВАНИЕ РЕЗУЛЬТАТОВ
# ============================================================

def log_result(review: Review, moderation: dict, published: bool):
    """Записывает результат в JSONL-файл."""
    record = {
        "timestamp": datetime.now().isoformat(),
        "name": review.name,
        "rating": review.rating,
        "text_preview": review.text[:100],
        "decision": moderation.get("decision"),
        "confidence": moderation.get("confidence"),
        "reason_code": moderation.get("reason_code"),
        "reason": moderation.get("reason"),
        "service_ratio": moderation.get("service_ratio"),
        "flags": moderation.get("flags"),
        "published": published,
        "auto_mode": AUTO_PUBLISH,
        "link_publish": review.link_publish,
        "link_product": review.link_product
    }

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ============================================================
# ПОМЕТКА ПИСЬМА В EXCHANGE
# ============================================================

def mark_email(item, moderation: dict):
    """Помечает письмо в Exchange в зависимости от решения."""
    decision = moderation.get("decision", "manual_review")

    if decision == "approve":
        item.is_read = True
        item.categories = ["✅ Одобрено ИИ"]
        item.save(update_fields=["is_read", "categories"])

    elif decision == "reject":
        item.is_read = True
        item.categories = ["❌ Отклонено ИИ"]
        item.save(update_fields=["is_read", "categories"])

    else:  # manual_review
        item.is_read = False
        item.categories = ["⚠️ Проверить вручную"]
        item.importance = "High"
        item.save(update_fields=["is_read", "categories", "importance"])

# ============================================================
# ПЕРЕМЕЩЕНИЕ В ПОДПАПКИ (опционально)
# ============================================================

def move_to_folder(account, item, folder_name: str):
    """
    Перемещает письмо в подпапку.
    Создаёт папку, если её нет.
    Раскомментируй вызовы в process_new_reviews() если хочешь сортировку.
    """
    from exchangelib import Folder

    inbox = account.inbox
    try:
        target = inbox / folder_name
    except Exception:
        target = Folder(parent=inbox, name=folder_name)
        target.save()
        log.info(f"Создана папка: Inbox/{folder_name}")

    item.move(target)
    log.info(f"Письмо перемещено в {folder_name}")

# ============================================================
# ОСНОВНОЙ ЦИКЛ
# ============================================================

def process_new_reviews():
    """Проверяет почту и обрабатывает новые отзывы."""
    try:
        account = connect_exchange()

        # Находим папку с отзывами
        review_folder = None
        for folder in account.inbox.children:
            if folder.name == REVIEW_FOLDER:
                review_folder = folder
                break

        if review_folder is None:
            # Если папка не найдена — ищем во входящих
            log.warning(f"Папка '{REVIEW_FOLDER}' не найдена, ищу во Входящих")
            review_folder = account.inbox

        log.info(f"Папка: {review_folder.name}")

        # Ищем непрочитанные письма с нужной темой
        unread_reviews = (
            review_folder
            .filter(
                is_read=False,
                subject__contains=SUBJECT_FILTER
            )
            .order_by("datetime_received")
        )

        items = list(unread_reviews)
        if not items:
            log.info("Новых отзывов нет")
            return 0

        log.info(f"Найдено {len(items)} новых отзывов")

        processed = 0
        for item in items:
            log.info(f"--- Обработка: {item.subject} ---")

            # Парсим отзыв
            review = parse_review_email(item)

            if not review.text and not review.pros:
                log.warning(f"Пустой отзыв от {review.name}, пропускаем")
                continue

            log.info(f"Отзыв от: {review.name} | Оценка: {review.rating}")
            log.info(f"Текст: {review.text[:80]}...")

            # Модерация через Claude API
            moderation = moderate_review(review)
            decision = moderation.get("decision", "manual_review")
            confidence = moderation.get("confidence", 0)
            reason = moderation.get("reason", "")

            log.info(f"Решение: {decision} (confidence={confidence:.2f})")
            log.info(f"Причина: {reason}")

            # Действие
            published = False

            if AUTO_PUBLISH:
                if decision == "approve" and confidence >= CONFIDENCE_THRESHOLD:
                    published = publish_review(review)
                    mark_email(item, moderation)
                    # move_to_folder(account, item, "Одобрено")
                elif decision == "reject":
                    log.info(f"❌ Отклонён: {reason}")
                    mark_email(item, moderation)
                    # move_to_folder(account, item, "Отклонено")
                else:
                    log.info(f"⚠️ На ручную проверку: {reason}")
                    mark_email(item, moderation)
                    # move_to_folder(account, item, "На проверку")
            else:
                # Режим помощника — помечаем категорией, не публикуем
                emoji = {"approve": "✅", "reject": "❌", "manual_review": "⚠️"}
                log.info(f"{emoji.get(decision, '?')} Рекомендация: {decision}")
                mark_email(item, moderation)

            # Логируем результат
            log_result(review, moderation, published)
            processed += 1

        return processed

    except Exception as e:
        log.error(f"Ошибка: {e}")
        return 0

# ============================================================
# ЗАПУСК
# ============================================================

def main():
    # Проверяем обязательные переменные
    missing = []
    if not EWS_EMAIL:
        missing.append("EWS_EMAIL")
    if not EWS_PASSWORD:
        missing.append("EWS_PASSWORD")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        log.error(f"Не заданы переменные окружения: {', '.join(missing)}")
        log.error("Заполни .env файл и запусти снова")
        return

    mode = "АВТОМАТ" if AUTO_PUBLISH else "ПОМОЩНИК"
    log.info(f"=== Запуск модерации отзывов Sulpak ({mode}) ===")
    log.info(f"Сервер: {EWS_SERVER}")
    log.info(f"Порог автопубликации: {CONFIDENCE_THRESHOLD}")
    log.info(f"Лог: {LOG_FILE}")
    log.info("=" * 50)

    count = process_new_reviews()
    log.info(f"Обработано отзывов: {count}")
    log.info("=== Завершено ===")


if __name__ == "__main__":
    main()
