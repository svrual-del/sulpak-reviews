"""
Эксперимент: проверка медиа-отзывов через Claude vision.
НЕ публикует, НЕ меняет состояние писем в Exchange. Только читает и печатает вердикт.

Запуск:
    py test_media_vision.py           # проверить последние 3 отзыва с медиа
    py test_media_vision.py 10        # последние 10
"""

import sys
import json
from urllib.parse import urlparse

# Windows-консоль: принудительно UTF-8 для stdout, иначе падает на не-ASCII
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import anthropic

from sulpak_review_moderator import (
    connect_exchange,
    parse_review_email,
    REVIEW_FOLDER,
    SUBJECT_FILTER,
    ANTHROPIC_API_KEY,
)

# Сколько последних писем сканировать (из них отберутся те, где есть медиа)
SCAN_WINDOW = 100

# Поддерживаемые Claude форматы картинок
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


VISION_SYSTEM = """Ты — модератор медиа-контента к отзывам на товары Sulpak (Казахстан).
На вход ты получаешь: текст отзыва покупателя, URL страницы товара, фотографии, которые пользователь приложил к отзыву.

Задача — оценить, уместны ли фото для публикации вместе с отзывом.

ОДОБРИТЬ (approve), если:
- На фото действительно изображён товар, о котором отзыв (или он используется / распакован)
- Фото чёткие, контент цивильный
- Допустимо: фото упаковки, коробки, инструкции, процесса сборки/использования

ОТКЛОНИТЬ (reject), если:
- На фото нет товара — случайные объекты (животные, пейзажи, еда без товара)
- Скриншоты чата, переписки с магазином, чеков, паспортов — персональные данные
- Нецензурный / непристойный / шокирующий контент
- Реклама сторонних магазинов, промокоды, логотипы конкурентов
- Фото явно чужого товара (не того, о котором отзыв)

MANUAL_REVIEW, если:
- Товар возможно есть, но качество/ракурс не позволяют уверенно подтвердить
- Виден брак/дефект — ценно, но требует человеческого решения
- Фото не удалось разобрать

Формат ответа — строго JSON без markdown-обёртки:
{
  "decision": "approve" | "reject" | "manual_review",
  "confidence": 0.0-1.0,
  "reason": "краткое пояснение на русском",
  "per_image": [
    {"index": 1, "verdict": "ok" | "not_product" | "personal_data" | "other", "note": "что видно на фото"}
  ]
}"""


def is_supported_image(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(IMAGE_EXTS)


def build_message(review, image_urls: list[str]) -> list:
    content = [
        {"type": "text", "text": f"URL страницы товара: {review.link_product or 'неизвестно'}"},
        {"type": "text", "text": (
            f"Текст отзыва:\n"
            f"Оценка: {review.rating}\n"
            f"Плюсы: {review.pros}\n"
            f"Минусы: {review.cons}\n"
            f"Текст: {review.text}"
        )},
        {"type": "text", "text": f"К отзыву приложено {len(image_urls)} фото:"},
    ]
    for url in image_urls:
        content.append({
            "type": "image",
            "source": {"type": "url", "url": url},
        })
    return [{"role": "user", "content": content}]


def check_review_media(client: anthropic.Anthropic, review) -> dict:
    images = [u for u in review.media_urls if is_supported_image(u)]
    skipped = [u for u in review.media_urls if not is_supported_image(u)]

    if not images:
        return {
            "decision": "manual_review",
            "confidence": 0.0,
            "reason": "нет поддерживаемых картинок (видео или неизвестный формат)",
            "skipped_urls": skipped,
        }

    # Claude Sonnet 4 принимает до 20 картинок на сообщение
    images = images[:20]

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=VISION_SYSTEM,
            messages=build_message(review, images),
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        result["_checked_urls"] = images
        result["_skipped_urls"] = skipped
        return result
    except json.JSONDecodeError as e:
        return {
            "decision": "manual_review",
            "confidence": 0.0,
            "reason": f"не удалось распарсить JSON: {e}. Сырой ответ: {text[:300]}",
        }
    except Exception as e:
        return {
            "decision": "manual_review",
            "confidence": 0.0,
            "reason": f"ошибка API/сети: {e}",
        }


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    if not ANTHROPIC_API_KEY:
        print("ОШИБКА: ANTHROPIC_API_KEY не задан в .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"Подключаюсь к Exchange...")
    account = connect_exchange()

    review_folder = None
    for folder in account.inbox.children:
        if folder.name == REVIEW_FOLDER:
            review_folder = folder
            break
    if review_folder is None:
        review_folder = account.inbox

    print(f"Папка: {review_folder.name}")
    print(f"Сканирую последние {SCAN_WINDOW} писем, ищу {limit} с медиа...\n")

    items = list(
        review_folder
        .filter(subject__contains=SUBJECT_FILTER)
        .order_by("-datetime_received")[:SCAN_WINDOW]
    )

    checked = 0
    for item in items:
        if checked >= limit:
            break

        review = parse_review_email(item)
        if not review.has_media or not review.media_urls:
            continue

        print("=" * 72)
        print(f"Дата письма: {review.email_date}")
        print(f"От: {review.name} | Оценка: {review.rating}")
        print(f"Товар: {review.link_product}")
        print(f"Текст: {review.text[:150]}")
        print(f"Медиа ({len(review.media_urls)}):")
        for url in review.media_urls:
            marker = "[IMG]" if is_supported_image(url) else "[VIDEO/OTHER]"
            print(f"   {marker} {url}")

        print("\nОтправляю в Claude vision...")
        result = check_review_media(client, review)

        decision = result.get("decision", "?")
        confidence = result.get("confidence", 0.0)
        reason = result.get("reason", "")

        mark = {"approve": "[APPROVE]", "reject": "[REJECT]", "manual_review": "[MANUAL]"}.get(decision, "[?]")
        print(f"\n{mark} Vision-вердикт: {decision} (confidence={confidence})")
        print(f"   Причина: {reason}")
        for pi in result.get("per_image", []) or []:
            print(f"   · фото {pi.get('index')}: {pi.get('verdict')} — {pi.get('note')}")
        if result.get("skipped_urls") or result.get("_skipped_urls"):
            skipped = result.get("skipped_urls") or result.get("_skipped_urls")
            print(f"   Пропущено (не картинки): {skipped}")

        checked += 1
        print()

    if checked == 0:
        print("Писем с медиа не найдено в последних", SCAN_WINDOW, "сообщениях.")
    else:
        print(f"Всего проверено: {checked}")


if __name__ == "__main__":
    main()
