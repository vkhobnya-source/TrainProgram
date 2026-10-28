from pypdf import PdfReader
from PIL import Image, ImageOps
import io
import logging
import re
import json
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)


def extract_pages(pdf_path):
    reader = PdfReader(pdf_path)
    return [
        page.extract_text(extraction_mode="layout") or ""
        for page in reader.pages
    ]


def extract_text(pdf_path):
    return "\n".join(extract_pages(pdf_path))


def clean(text):
    lines = []
    for line in text.splitlines():
        if line.startswith("  ") or len(re.findall(r'\S {2,}\S', line)) >= 2:
            lines.append(line.rstrip())
        else:
            lines.append(re.sub(r'[ \t]+', ' ', line).strip())
    text = "\n".join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _max_blank_run(text):
    best = current = 0
    for line in text.splitlines():
        if line.strip():
            current = 0
        else:
            current += 1
            best = max(best, current)
    return best


def _on_background(image, background):
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, background + (255,))
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")
    return image.convert("RGB")


def _dark_pixels(visible):
    histogram = visible.convert("L").histogram()
    return sum(histogram[:215])


def _prepare_image(data):
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None, 0

    if image.width < 300 or image.height < 120:
        return None, 0

    visible = _on_background(image, (255, 255, 255))
    dark = _dark_pixels(visible)
    total = visible.width * visible.height

    if dark < total * 0.001:
        visible = ImageOps.invert(_on_background(image, (0, 0, 0)))
        dark = _dark_pixels(visible)
    elif dark > total * 0.5:
        visible = ImageOps.invert(visible)
        dark = _dark_pixels(visible)

    if dark < total * 0.001 or dark > total * 0.5:
        return None, 0
    return visible, dark


def _without_footer(text):
    return re.split(
        r'\n\s*Version\s+\S+.*?Page\s+\d+\s+of\s+\d+.*', text, maxsplit=1
    )[0]


def extract_diagrams(pdf_path):
    reader = PdfReader(pdf_path)
    current_set = None
    diagrams = {}

    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        text = _without_footer(text)
        set_match = re.search(
            r'Sample\s+Exams?\s+set\s+([A-F])\b', text, re.IGNORECASE
        )
        if set_match:
            current_set = set_match.group(1).upper()

        question_matches = list(re.finditer(r'Question #(\d+)', text))
        if not question_matches or current_set is None:
            continue

        segments = []
        for i, match in enumerate(question_matches):
            end = (
                question_matches[i + 1].start()
                if i + 1 < len(question_matches)
                else len(text)
            )
            segment = text[match.start():end].strip()
            segments.append(
                (int(match.group(1)), _max_blank_run(segment))
            )

        number, gap = max(segments, key=lambda item: item[1])
        if gap < 4:
            continue

        best = None
        for embedded in page.images:
            visible, score = _prepare_image(embedded.data)
            if visible is not None and (best is None or score > best[1]):
                best = (visible, score)

        if best is not None:
            diagrams[(current_set, number)] = best[0]

    return diagrams


def save_diagrams(diagrams, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for (set_name, number), image in diagrams.items():
        filename = f"{set_name}{number:02d}.png"
        image.save(out_dir / filename)
        paths[(set_name, number)] = f"assets/diagrams/{filename}"
    return paths


def split_by_sets(text):
    """Режет текст на блоки по наборам."""
    pattern = r'Sample\s+Exams?\s+set\s+([A-F])\b'
    matches = []
    current_set = None
    for match in re.finditer(pattern, text, re.IGNORECASE):
        set_name = match.group(1).upper()
        if set_name != current_set:
            matches.append((match.start(), set_name))
            current_set = set_name

    if not matches:
        return [("ALL", text)]

    blocks = []
    for i, (start, set_name) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        blocks.append((set_name, text[start:end]))
    return blocks


def parse_questions(text):
    questions = []
    parts = re.split(r'(?=Question #(?:[A-Z]?\d+))', text)

    for part in parts:
        part = part.strip()
        question_match = re.match(r'Question #(\d+)', part)
        if not question_match:
            continue

        num = int(question_match.group(1))
        body = re.sub(r'^Question #\d+\s*\([^)]*\)\s*', '', part, count=1).strip()

        # Опции начинаются с новой строки; это не даст принять текст вопроса за вариант.
        option_pattern = (
            r'^[ \t]*([a-e])\)[ \t]*(.*?)(?=^[ \t]*[a-e]\)[ \t]*|'
            r'^[ \t]*Select\s+(?:ONE|TWO|THREE)\s+options?\.?[ \t]*$|\Z)'
        )
        option_matches = list(re.finditer(
            option_pattern, body, re.DOTALL | re.MULTILINE | re.IGNORECASE
        ))

        if not option_matches:
            continue

        q_text = clean(body[:option_matches[0].start()])
        options = [
            re.sub(r'\s+', ' ', match.group(2)).strip()
            for match in option_matches
        ]

        questions.append({
            "number": num,
            "question": q_text,
            "options": options,
            "correct": None,
            "chapter": None,
            "explanation": "",
            "set": None,
        })
    return questions


def parse_answers(text, set_name="ALL"):
    """
    Возвращает dict: (set_letter, number) -> (correct_letter, explanation)
    Поддерживает форматы:
      A1. Correct: c
      A1. ... c) CORRECT ...
      | 1 | c |   (без набора — тогда ключ ('ALL', 1))
    """
    answers = {}
    explanations = {}

    # Official ISTQB answer-key tables: "1 c FL-1.1.1 K1 1".
    table_pattern = (
        r'(?<!\S)(\d+)\s+([a-e](?:\s*,\s*[a-e])*)\s+FL-(\d+)\.'
    )
    for num, answer, _chapter in re.findall(
            table_pattern, text, re.IGNORECASE):
        letters = [letter.strip().lower() for letter in answer.split(",")]
        answers[(set_name, int(num))] = letters[0] if len(letters) == 1 else letters

    # Формат с префиксом набора: "A1. Correct: c"
    pattern_set = r'([A-F])(\d+)\.\s*(?:Correct(?:\s*answer)?\s*[:：]?\s*([a-d]))'
    found_set = re.findall(pattern_set, text, re.IGNORECASE)

    if found_set:
        for s, num, ans in found_set:
            answers[(s.upper(), int(num))] = ans.lower()

        # Разбор: "A1. ... <text> ... A2. ..."
        expl_pattern = r'([A-F])(\d+)\.(.*?)(?=[A-F]\d+\.|$)'
        for s, num, block in re.findall(expl_pattern, text, re.DOTALL):
            block = clean(block)
            key = (s.upper(), int(num))
            m = re.search(r'(?:CORRECT|is correct)[^\n]*\n(.*)', block,
                          re.DOTALL | re.IGNORECASE)
            if m:
                explanations[key] = clean(m.group(1))[:800]
    else:
        # Формат без префикса — таблица
        for num, ans in re.findall(r'\|\s*(\d+)\s*\|\s*([a-d])\s*\|', text):
            answers[(set_name, int(num))] = ans.lower()
        # Или "N. Correct: c"
        for num, ans in re.findall(r'(\d+)\.\s*Correct\s*[:：]?\s*([a-d])',
                                   text, re.IGNORECASE):
            answers[(set_name, int(num))] = ans.lower()

    return answers, explanations


def lo_map_from_answers(text):
    """Возвращает dict (set, number) -> chapter, выведенный из LO."""
    result = {}
    # "A1. FL-1.1.1 (K1) ..."
    for s, num, ch in re.findall(r'([A-F])(\d+)\.\s*FL-(\d+)\.', text, re.IGNORECASE):
        result[(s.upper(), int(num))] = int(ch)
    return result


def lo_map_from_answer_table(text, set_name):
    """Извлекает главу из строк официальной таблицы ответов."""
    return {
        (set_name, int(num)): int(chapter)
        for num, chapter in re.findall(
            r'(?<!\S)(\d+)\s+[a-e](?:\s*,\s*[a-e])*\s+FL-(\d+)\.',
            text, re.IGNORECASE)
    }


def load_explanations(out_json):
    path = Path(out_json)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        (q.get("set"), q.get("number")): q.get("explanation", "")
        for q in previous
        if q.get("explanation")
    }


def main(q_pdf, a_pdf, out_json):
    q_text = clean(extract_text(q_pdf))
    a_text = clean(extract_text(a_pdf))
    old_explanations = load_explanations(out_json)

    all_questions = []
    for set_name, block in split_by_sets(q_text):
        qs = parse_questions(block)
        for q in qs:
            q["set"] = set_name
        all_questions.extend(qs)

    print(f"Найдено вопросов: {len(all_questions)}")
    print("По наборам:",
          {s: sum(1 for q in all_questions if q['set'] == s)
           for s in sorted({q['set'] for q in all_questions})})

    answers = {}
    explanations = {}
    lo_map = lo_map_from_answers(a_text)
    for set_name, block in split_by_sets(a_text):
        block_answers, block_explanations = parse_answers(block, set_name)
        answers.update(block_answers)
        explanations.update(block_explanations)
        lo_map.update(lo_map_from_answer_table(block, set_name))

    print(f"Найдено ответов: {len(answers)}")

    letter_to_idx = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4}

    for q in all_questions:
        # Ключ с набором
        key = (q["set"], q["number"])
        # Если ответов с набором нет — пробуем ALL
        if key not in answers:
            key = ("ALL", q["number"])

        if key in answers:
            answer = answers[key]
            if isinstance(answer, list):
                q["correct"] = [letter_to_idx[letter] for letter in answer]
            else:
                q["correct"] = letter_to_idx[answer]
        if key in explanations:
            q["explanation"] = explanations[key]
        if key in lo_map:
            q["chapter"] = lo_map[key]

    if not all_questions:
        raise ValueError("Не удалось найти вопросы в PDF с вопросами.")
    if not answers:
        raise ValueError("Не удалось найти ответы в PDF с ответами.")

    valid = [q for q in all_questions if q["correct"] is not None]
    skipped = len(all_questions) - len(valid)
    if skipped:
        print(f"Пропущено без ответа: {skipped}")
    if not valid:
        raise ValueError("Ни один вопрос не сопоставлен с правильным ответом.")

    diagrams = extract_diagrams(q_pdf)
    image_paths = save_diagrams(
        diagrams, Path(out_json).resolve().parent / "assets" / "diagrams"
    )
    for q in valid:
        image_path = image_paths.get((q["set"], q["number"]))
        if image_path:
            q["image"] = image_path
        if not q["explanation"]:
            q["explanation"] = old_explanations.get(
                (q["set"], q["number"]), ""
            )

    print(f"Найдено схем: {len(image_paths)}")

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2)

    print(f"Сохранено: {out_json}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("Использование: python parsing_all.py questions_all.pdf answers_all.pdf questions.json")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])