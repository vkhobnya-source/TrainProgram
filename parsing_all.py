from pypdf import PdfReader
import re
import json
from pathlib import Path


def extract_text(pdf_path):
    reader = PdfReader(pdf_path)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def clean(text):
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


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


def main(q_pdf, a_pdf, out_json):
    q_text = clean(extract_text(q_pdf))
    a_text = clean(extract_text(a_pdf))

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

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2)

    print(f"Сохранено: {out_json}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("Использование: python parsing_all.py questions_all.pdf answers_all.pdf questions.json")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])