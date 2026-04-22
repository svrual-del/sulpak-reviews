"""
Ежедневная сводка по модерации отзывов Sulpak.
Собирает результаты из JSONL-лога за день и отправляет HTML-отчёт на email.

Запуск: py daily_report.py [YYYYMMDD]
  без аргумента — отчёт за сегодня
  с аргументом  — отчёт за указанную дату
"""

import os
import sys
import json
import logging
from datetime import datetime
from collections import Counter
from dotenv import load_dotenv

# Загружаем .env
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"), override=True)

# Конфиг из .env
EWS_SERVER = os.getenv("EWS_SERVER", "mail2016.sulpak.kz")
EWS_EMAIL = os.getenv("EWS_EMAIL", "")
EWS_USERNAME = os.getenv("EWS_USERNAME", "")
EWS_PASSWORD = os.getenv("EWS_PASSWORD", "")
SKIP_SSL_VERIFY = os.getenv("SKIP_SSL_VERIFY", "false").lower() == "true"
REPORT_TO = os.getenv("REPORT_TO", EWS_EMAIL)

log = logging.getLogger("daily_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_daily_log(date_str: str) -> list:
    """Читает JSONL-лог за указанную дату."""
    log_file = os.path.join(_script_dir, f"moderation_log_{date_str}.jsonl")
    if not os.path.exists(log_file):
        log.warning(f"Файл {log_file} не найден")
        return []

    records = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_html_report(records: list, date_str: str) -> str:
    """Формирует HTML-отчёт в табличном формате."""
    if not records:
        return f"""
        <h2>Модерация отзывов Sulpak — {date_str}</h2>
        <p>За этот день отзывов не обработано.</p>
        """

    # Считаем статистику
    total = len(records)
    decisions = Counter(r.get("decision", "unknown") for r in records)
    approve_count = decisions.get("approve", 0)
    reject_count = decisions.get("reject", 0)
    manual_count = decisions.get("manual_review", 0)

    # Считаем причины отклонения
    reject_reasons = Counter(
        r.get("reason_code", "unknown")
        for r in records if r.get("decision") == "reject"
    )

    # Считаем опубликованные — разбивка по наличию медиа
    published_count = sum(1 for r in records if r.get("published"))
    published_no_media = sum(1 for r in records if r.get("published") and not r.get("has_media"))
    published_with_media = sum(1 for r in records if r.get("published") and r.get("has_media"))

    # Статистика по медиа и vision
    media_records = [r for r in records if r.get("has_media")]
    media_total = len(media_records)

    vision_records = [r for r in records if r.get("vision") is not None]
    vision_decisions = Counter(
        (r.get("vision") or {}).get("decision") for r in vision_records
    )
    vision_approve = vision_decisions.get("approve", 0)
    vision_reject = vision_decisions.get("reject", 0)
    vision_manual = vision_decisions.get("manual_review", 0)

    # Средний confidence
    confidences = [r.get("confidence", 0) for r in records if r.get("confidence")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    # Формируем HTML
    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
            h2 {{ color: #1a1a1a; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }}
            h3 {{ color: #555; margin-top: 25px; }}
            table {{ border-collapse: collapse; margin: 10px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px 16px; text-align: left; }}
            th {{ background-color: #f5f5f5; font-weight: bold; }}
            .approve {{ color: #2e7d32; font-weight: bold; }}
            .reject {{ color: #c62828; font-weight: bold; }}
            .manual {{ color: #f57f17; font-weight: bold; }}
            .summary {{ background-color: #f9f9f9; padding: 15px; border-radius: 8px; margin: 15px 0; }}
            .footer {{ color: #999; font-size: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <h2>Модерация отзывов Sulpak — {date_str}</h2>

        <div class="summary">
            <table>
                <tr>
                    <th>Решение</th>
                    <th>Кол-во</th>
                    <th>%</th>
                </tr>
                <tr>
                    <td class="approve">✅ Одобрено (approve)</td>
                    <td><b>{approve_count}</b></td>
                    <td>{approve_count/total*100:.1f}%</td>
                </tr>
                <tr>
                    <td class="reject">❌ Отклонено (reject)</td>
                    <td><b>{reject_count}</b></td>
                    <td>{reject_count/total*100:.1f}%</td>
                </tr>
                <tr>
                    <td class="manual">⚠️ На проверку (manual_review)</td>
                    <td><b>{manual_count}</b></td>
                    <td>{manual_count/total*100:.1f}%</td>
                </tr>
                <tr style="border-top: 2px solid #333;">
                    <td><b>Всего обработано</b></td>
                    <td><b>{total}</b></td>
                    <td>100%</td>
                </tr>
            </table>
        </div>

        <p>
            📤 Опубликовано автоматически: <b>{published_count}</b>
            (без медиа: {published_no_media}, с медиа: {published_with_media})
            | Средний confidence: <b>{avg_confidence:.2f}</b>
        </p>
    """

    # Блок «Медиа» — только если хоть один отзыв с фото
    if media_total > 0:
        def _pct(n, total):
            return f"{n/total*100:.1f}%" if total else "0%"

        html += f"""
        <h3>📷 Медиа в отзывах</h3>
        <p>Всего отзывов с медиа: <b>{media_total}</b>.
        Сами фото/видео на сайт не публикуются — vision помечает категорию письма для модератора.</p>
        <table>
            <tr><th>Vision-вердикт</th><th>Кол-во</th><th>% от медиа-отзывов</th></tr>
            <tr>
                <td class="approve">✅ фото ок</td>
                <td><b>{vision_approve}</b></td>
                <td>{_pct(vision_approve, media_total)}</td>
            </tr>
            <tr>
                <td class="reject">❌ фото отклонено (нужна ручная чистка в CMS)</td>
                <td><b>{vision_reject}</b></td>
                <td>{_pct(vision_reject, media_total)}</td>
            </tr>
            <tr>
                <td class="manual">⚠️ фото на ручную проверку</td>
                <td><b>{vision_manual}</b></td>
                <td>{_pct(vision_manual, media_total)}</td>
            </tr>
        </table>
        """

    # Таблица: медиа, которые vision отклонил — требуют ручной чистки в CMS
    vision_rejects = [
        r for r in records
        if (r.get("vision") or {}).get("decision") == "reject"
    ]
    if vision_rejects:
        html += """
        <h3>❌ Медиа требуют ручной чистки в CMS</h3>
        <p><i>Текст отзыва опубликован, но фото отклонены vision — зайти в CMS и удалить неуместные фото.</i></p>
        <table>
            <tr><th>Автор</th><th>Фото</th><th>Confidence</th><th>Причина</th><th>Товар</th></tr>
        """
        for r in vision_rejects:
            v = r.get("vision") or {}
            name = r.get("name", "—")
            media_count = r.get("media_count", 0)
            v_conf = v.get("confidence", 0.0)
            v_reason = v.get("reason", "—")
            product = r.get("link_product", "")
            product_short = product[:60] + "..." if len(product) > 60 else product
            html += (
                f"<tr>"
                f"<td>{name}</td>"
                f"<td>{media_count}</td>"
                f"<td>{int(v_conf*100)}%</td>"
                f"<td>{v_reason}</td>"
                f'<td><a href="{product}">{product_short}</a></td>'
                f"</tr>"
            )
        html += "</table>"

    # Причины отклонения
    if reject_reasons:
        reason_labels = {
            "service_not_product": "Отзыв о сервисе",
            "spam": "Спам/реклама",
            "profanity": "Нецензурная лексика",
            "too_short": "Слишком короткий",
            "personal_data": "Персональные данные",
            "wrong_product": "Не соответствует товару",
            "mixed_content": "Смешанный контент",
            "unclear": "Неразборчиво",
        }
        html += """
        <h3>Причины отклонения</h3>
        <table>
            <tr><th>Причина</th><th>Кол-во</th></tr>
        """
        for code, count in reject_reasons.most_common():
            label = reason_labels.get(code, code)
            html += f"<tr><td>{label}</td><td>{count}</td></tr>"
        html += "</table>"

    # Отзывы на ручную проверку — список
    manual_reviews = [r for r in records if r.get("decision") == "manual_review"]
    if manual_reviews:
        html += """
        <h3>⚠️ Требуют ручной проверки</h3>
        <table>
            <tr><th>Автор</th><th>Оценка</th><th>Причина</th><th>Текст</th></tr>
        """
        for r in manual_reviews:
            name = r.get("name", "—")
            rating = r.get("rating", "—")
            reason = r.get("reason", "—")
            text = r.get("text_preview", "")[:80]
            html += f"<tr><td>{name}</td><td>{rating}</td><td>{reason}</td><td>{text}...</td></tr>"
        html += "</table>"

    # Отклонённые — топ-10
    rejected = [r for r in records if r.get("decision") == "reject"]
    if rejected:
        html += """
        <h3>❌ Отклонённые отзывы</h3>
        <table>
            <tr><th>Автор</th><th>Причина</th><th>Текст</th></tr>
        """
        for r in rejected[:15]:
            name = r.get("name", "—")
            reason = r.get("reason", "—")
            text = r.get("text_preview", "")[:60]
            html += f"<tr><td>{name}</td><td>{reason}</td><td>{text}...</td></tr>"
        if len(rejected) > 15:
            html += f'<tr><td colspan="3"><i>...и ещё {len(rejected)-15}</i></td></tr>'
        html += "</table>"

    html += f"""
        <p class="footer">
            Автоматический отчёт модерации отзывов Sulpak<br>
            Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}
        </p>
    </body>
    </html>
    """
    return html


def send_report_email(html_body: str, date_str: str):
    """Отправляет HTML-отчёт через Exchange."""
    from exchangelib import (
        Credentials, Account, Configuration, DELEGATE, Message, HTMLBody
    )
    from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

    if SKIP_SSL_VERIFY:
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    credentials = Credentials(username=EWS_USERNAME, password=EWS_PASSWORD)
    config = Configuration(server=EWS_SERVER, credentials=credentials)
    account = Account(
        primary_smtp_address=EWS_EMAIL,
        config=config,
        autodiscover=False,
        access_type=DELEGATE
    )

    msg = Message(
        account=account,
        subject=f"Модерация отзывов Sulpak — отчёт за {date_str}",
        body=HTMLBody(html_body),
        to_recipients=[REPORT_TO]
    )
    msg.send()
    log.info(f"Отчёт отправлен на {REPORT_TO}")


def main():
    # Дата: аргумент или сегодня
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    log.info(f"Формирую отчёт за {date_str}")

    records = load_daily_log(date_str)
    log.info(f"Найдено записей: {len(records)}")

    html = build_html_report(records, date_str)

    # Отправка
    if not EWS_EMAIL or not EWS_PASSWORD:
        log.error("Не заданы EWS_EMAIL / EWS_PASSWORD в .env")
        # Сохраняем HTML локально
        out_file = os.path.join(_script_dir, f"report_{date_str}.html")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Отчёт сохранён в {out_file}")
        return

    send_report_email(html, date_str)


if __name__ == "__main__":
    main()
