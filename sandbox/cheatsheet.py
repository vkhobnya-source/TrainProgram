"""Генерация русско-английской шпаргалки по ошибкам последнего прохода.

Конспект теории по главам Syllabus, в которых были ошибки:
изложение по-русски, ключевые термины дублируются английским оригиналом,
плюс «ловушки» по мотивам ошибочных вопросов.
"""

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CHEATSHEETS_DIR = Path(__file__).parent / "cheatsheets"

CHAPTER_TITLES = {
    1: "Основы тестирования (Fundamentals of Testing)",
    2: "Тестирование в течение жизненного цикла разработки ПО (Testing Throughout the SDLC)",
    3: "Статическое тестирование (Static Testing)",
    4: "Анализ и проектирование тестов (Test Analysis and Design)",
    5: "Управление тестовой деятельностью (Managing the Test Activities)",
    6: "Инструменты тестирования (Test Tools)",
}

SYSTEM_PROMPT = (
    "You are a precise ISTQB CTFL v4.0 exam tutor. "
    "You write concise bilingual study notes in Russian with English ISTQB terms."
)


def group_by_chapter(wrong_questions):
    """Группирует ошибочные вопросы по главам."""
    chapters = defaultdict(list)
    for q in wrong_questions:
        chapters[q.get("chapter", 0)].append(q)
    return dict(sorted(chapters.items()))


def _question_block(q):
    options = q.get("options", [])
    correct = q.get("correct")
    correct_indices = correct if isinstance(correct, list) else [correct]
    correct_options = [options[i] for i in correct_indices if 0 <= i < len(options)]
    block = f"Question: {q.get('question', '').strip()}\n"
    block += "Correct answer(s): " + "; ".join(correct_options) + "\n"
    explanation = (q.get("explanation") or "").strip()
    if explanation:
        # Пояснения бывают длинными — ограничиваем, чтобы не раздувать промпт.
        if len(explanation) > 1200:
            explanation = explanation[:1200] + "…"
        block += f"Existing explanation (Russian): {explanation}\n"
    return block


def build_prompt(wrong_questions):
    """Собирает промпт для генерации шпаргалки по ошибкам."""
    chapters = group_by_chapter(wrong_questions)
    parts = [
        "Создай краткую русско-английскую шпаргалку для подготовки к экзамену ISTQB CTFL v4.0 "
        "по темам, в которых я ошибся. Требования к формату:\n"
        "- Markdown, по разделу `## Глава N. <название>` на каждую главу ниже.\n"
        "- Изложение по-русски; каждый ключевой термин дублируй английским оригиналом в скобках, "
        "например: «критерии выхода (exit criteria)».\n"
        "- Для каждой главы: 3-7 тезисов самой важной теории, затем блок «⚠️ Ловушки» "
        "по мотивам приведённых вопросов (почему в них легко ошибиться).\n"
        "- Кратко: это шпаргалка, а не учебник. Без вступлений и заключений.\n"
    ]
    for chapter, questions in chapters.items():
        title = CHAPTER_TITLES.get(chapter, "")
        parts.append(f"\n### Chapter {chapter}. {title}\nQuestions I answered incorrectly:\n")
        for q in questions:
            parts.append(_question_block(q))
    return "\n".join(parts)


def request_cheatsheet(prompt):
    """Запрашивает текст шпаргалки у OpenRouter (та же интеграция, что и пояснения)."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан OPENROUTER_API_KEY.")

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    request = Request(
        os.getenv(
            "OPENROUTER_API_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        ),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "ISTQB CTFL Trainer"),
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        provider_detail = ""
        try:
            error_body = json.loads(error.read().decode("utf-8"))
            provider_detail = error_body.get("error", {}).get("message", "")
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        detail = f"OpenRouter вернул HTTP {error.code}."
        if provider_detail:
            detail = f"{detail} {provider_detail}"
        raise RuntimeError(detail) from error
    except URLError as error:
        raise RuntimeError("Не удалось подключиться к OpenRouter.") from error
    except (TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("OpenRouter вернул некорректный ответ или запрос истёк.") from error

    try:
        text = result["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("OpenRouter не вернул текст шпаргалки.") from error

    return text.strip()


def generate_cheatsheet(wrong_questions):
    """Генерирует шпаргалку и сохраняет её версию в cheatsheets/.

    Возвращает (путь к файлу, текст шпаргалки).
    """
    if not wrong_questions:
        raise RuntimeError("Нет ошибок для шпаргалки.")

    text = request_cheatsheet(build_prompt(wrong_questions))

    now = datetime.now()
    chapters = ", ".join(str(ch) for ch in group_by_chapter(wrong_questions))
    header = (
        f"# 📝 Шпаргалка по ошибкам — {now:%d.%m.%Y %H:%M}\n\n"
        f"Ошибок: {len(wrong_questions)} · Главы: {chapters}\n\n---\n\n"
    )
    content = header + text + "\n"

    CHEATSHEETS_DIR.mkdir(exist_ok=True)
    path = CHEATSHEETS_DIR / f"cheatsheet_{now:%Y-%m-%d_%H-%M-%S}.md"
    path.write_text(content, encoding="utf-8")
    return path, content


def list_cheatsheets():
    """Список сохранённых шпаргалок, новые первыми."""
    if not CHEATSHEETS_DIR.exists():
        return []
    return sorted(CHEATSHEETS_DIR.glob("cheatsheet_*.md"), reverse=True)
