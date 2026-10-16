import streamlit as st
import json
import random
from tempfile import NamedTemporaryFile
from pathlib import Path

st.set_page_config(page_title="Тренажёр ISTQB CTFL v4.0", page_icon="📘", layout="centered")

QUESTIONS_FILE = Path(__file__).parent / "questions.json"
PASS_THRESHOLD = 0.65


@st.cache_data
def load_questions():
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def question_key(question):
    return question.get("set"), question.get("number"), question.get("question")


def save_explanation(question, explanation):
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        questions = json.load(f)

    key = question_key(question)
    matching_questions = [q for q in questions if question_key(q) == key]
    if not matching_questions:
        raise ValueError("Вопрос не найден в questions.json.")

    for matching_question in matching_questions:
        matching_question["explanation"] = explanation

    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=QUESTIONS_FILE.parent,
            prefix=f"{QUESTIONS_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(questions, temp_file, ensure_ascii=False, indent=2)
            temp_path = Path(temp_file.name)
        temp_path.replace(QUESTIONS_FILE)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    question["explanation"] = explanation
    for session_question in st.session_state.questions + st.session_state.wrong_list:
        if question_key(session_question) == key:
            session_question["explanation"] = explanation
    load_questions.clear()


def init_session():
    if "index" not in st.session_state:
        st.session_state.index = 0
        st.session_state.correct = 0
        st.session_state.answered = False
        st.session_state.show_answer = False
        st.session_state.selected = None
        st.session_state.questions = []
        st.session_state.wrong_list = []
        st.session_state.per_chapter = {}


def start_quiz(mode, chapter=None):
    all_q = load_questions()
    if mode == "chapter" and chapter is not None:
        all_q = [q for q in all_q if q["chapter"] == chapter]
    elif mode == "wrong":
        wrong_file = Path(__file__).parent / "wrong.json"
        if wrong_file.exists():
            with open(wrong_file, encoding="utf-8") as f:
                all_q = json.load(f)
            canonical_by_key = {question_key(q): q for q in load_questions()}
            for q in all_q:
                canonical_question = canonical_by_key.get(question_key(q))
                if canonical_question is not None:
                    q["explanation"] = canonical_question.get("explanation", "")
        else:
            st.warning("Сохранённых ошибок нет, будут использованы все вопросы.")

    if not all_q:
        st.error("Вопросы не найдены.")
        return

    random.shuffle(all_q)
    st.session_state.questions = all_q
    st.session_state.index = 0
    st.session_state.correct = 0
    st.session_state.answered = False
    st.session_state.show_answer = False
    st.session_state.selected = None
    st.session_state.wrong_list = []
    st.session_state.per_chapter = {}
    st.session_state.total = len(all_q)


def shuffle_options(q):
    indexed = list(enumerate(q["options"]))
    random.shuffle(indexed)
    new_options = [opt for _, opt in indexed]
    correct_indices = q["correct"] if isinstance(q["correct"], list) else [q["correct"]]
    shuffled_correct = [
        i for i, (orig, _) in enumerate(indexed) if orig in correct_indices
    ]
    new_correct = shuffled_correct if isinstance(q["correct"], list) else shuffled_correct[0]
    return new_options, new_correct


def run():
    init_session()

    st.title("📘 Тренажёр ISTQB CTFL v4.0")
    st.caption("Простой инструмент для подготовки по Syllabus v4.0")

    # Боковая панель с настройками
    with st.sidebar:
        st.header("Настройки")
        all_q = load_questions()
        chapters = sorted({q["chapter"] for q in all_q})

        mode = st.radio("Режим", ["Все вопросы", "По главам", "Работа над ошибками"], key="mode_select")

        chapter = None
        if mode == "По главам":
            chapter = st.selectbox("Выберите главу", chapters, format_func=lambda x: f"Глава {x}")

        if st.button("Начать / Начать заново", type="primary"):
            m = "all" if mode == "Все вопросы" else ("chapter" if mode == "По главам" else "wrong")
            start_quiz(m, chapter)
            st.rerun()

        st.divider()
        st.metric("Всего вопросов", len(all_q))

    # Если тест ещё не начат
    if not st.session_state.questions:
        st.info("👈 Выберите режим в левой панели и нажмите «Начать / Начать заново»")
        return

    qs = st.session_state.questions
    total = len(qs)
    idx = st.session_state.index

    # Завершение
    if idx >= total:
        st.balloons()
        st.success("🎉 Готово!")

        score = st.session_state.correct / total
        st.metric("Результат", f"{st.session_state.correct}/{total}", f"{score * 100:.1f}%")

        if score >= PASS_THRESHOLD:
            st.success(f"✅ Сдано (порог {PASS_THRESHOLD * 100:.0f}%)")
        else:
            st.error(f"❌ Не сдано (порог {PASS_THRESHOLD * 100:.0f}%)")

        if st.session_state.per_chapter:
            st.subheader("Результат по главам")
            for ch in sorted(st.session_state.per_chapter):
                c, t = st.session_state.per_chapter[ch]
                st.write(f"Глава {ch}: {c}/{t}")

        # Сохраняем ошибки
        if st.session_state.wrong_list:
            wrong_file = Path(__file__).parent / "wrong.json"
            with open(wrong_file, "w", encoding="utf-8") as f:
                json.dump(st.session_state.wrong_list, f, ensure_ascii=False, indent=2)
            st.info(
                f"Сохранено {len(st.session_state.wrong_list)} вопросов с ошибками — доступны в режиме «Работа над ошибками».")

        return

    q = qs[idx]
    options, correct_idx = shuffle_options(q)

    st.progress(idx / total)
    st.caption(f"Вопрос {idx + 1} из {total}  ·  Глава {q['chapter']}")

    # Вопрос
    st.markdown(f"### {q['question']}")

    # Варианты ответа
    is_multi_select = isinstance(q["correct"], list)
    if is_multi_select:
        selected = st.multiselect(
            "Выберите ответы",
            options,
            key=f"q_{idx}",
            disabled=st.session_state.answered
        )
    else:
        selected = st.radio(
            "Выберите ответ",
            options,
            index=None,
            key=f"q_{idx}",
            label_visibility="collapsed",
            disabled=st.session_state.answered
        )

    # Кнопка «Ответить»
    if not st.session_state.answered:
        if not st.session_state.show_answer:
            if st.button("Подсмотреть правильный ответ"):
                st.session_state.show_answer = True
                st.rerun()

        if st.session_state.show_answer:
            correct_options = (
                [options[i] for i in correct_idx]
                if is_multi_select else [options[correct_idx]]
            )
            st.success(f"Правильный ответ: {', '.join(correct_options)}")
            st.text_area(
                "Пояснение к правильному ответу",
                value=str(q.get("explanation") or ""),
                key=f"explanation_{idx}",
                height=120,
            )
            if st.button("Сохранить пояснение", key=f"save_explanation_{idx}"):
                save_explanation(q, st.session_state[f"explanation_{idx}"])
                st.success("Пояснение сохранено в questions.json.")

        if st.button("Ответить", type="primary") and selected:
            st.session_state.selected = selected
            st.session_state.answered = True
            ch = q["chapter"]
            st.session_state.per_chapter.setdefault(ch, [0, 0])
            st.session_state.per_chapter[ch][1] += 1

            if is_multi_select:
                selected_indices = {options.index(option) for option in selected}
                is_correct = selected_indices == set(correct_idx)
            else:
                is_correct = options.index(selected) == correct_idx

            if is_correct:
                st.session_state.correct += 1
                st.session_state.per_chapter[ch][0] += 1
            else:
                st.session_state.wrong_list.append(q)
            st.rerun()
    else:
        # Обратная связь
        if is_multi_select:
            selected_indices = {options.index(option) for option in selected}
            is_correct = selected_indices == set(correct_idx)
        else:
            is_correct = options.index(selected) == correct_idx

        if is_correct:
            st.success("✅ Верно!")
        else:
            correct_options = (
                [options[i] for i in correct_idx]
                if is_multi_select else [options[correct_idx]]
            )
            st.error(f"❌ Неверно. Правильный ответ: {', '.join(correct_options)}")

        st.info(f"💡 {q['explanation']}")

        if st.button("Следующий вопрос", type="primary"):
            st.session_state.index += 1
            st.session_state.answered = False
            st.session_state.show_answer = False
            st.session_state.selected = None
            st.rerun()


if __name__ == "__main__":
    run()