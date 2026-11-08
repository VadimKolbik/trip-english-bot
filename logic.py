from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import update, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from models import UserCardProgress, Card, Category, get_or_create_system_category


TZ = ZoneInfo(config.timezone)


# ---------- Время ----------

def today_local() -> date:
    """Текущая дата по TZ из конфига."""
    return datetime.now(TZ).date()


def now_local_naive() -> datetime:
    """Naive datetime в локальной TZ — для хранения в SQLite DateTime."""
    return datetime.now(TZ).replace(tzinfo=None)


# ---------- SM-2 ----------

def sm2_update(progress: UserCardProgress, quality: int) -> UserCardProgress:
    if quality < 0 or quality > 5:
        raise ValueError("quality must be 0..5")

    # Если объект новый и не прошел через БД, значения могут быть None
    if progress.ease_factor is None:
        progress.ease_factor = 2.5
    if progress.repetitions is None:
        progress.repetitions = 0
    if progress.interval is None:
        progress.interval = 0
    if progress.correct_count is None:
        progress.correct_count = 0
    if progress.wrong_count is None:
        progress.wrong_count = 0

    ef = progress.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if ef < 1.3:
        ef = 1.3
    progress.ease_factor = ef

    if quality < 3:
        progress.repetitions = 0
        progress.interval = 1
        progress.wrong_count += 1
    else:
        progress.repetitions += 1
        progress.correct_count += 1
        if progress.repetitions == 1:
            progress.interval = 1
        elif progress.repetitions == 2:
            progress.interval = 6
        else:
            progress.interval = max(1, round(progress.interval * ef))

    progress.last_reviewed_at = now_local_naive()
    progress.next_review_at = now_local_naive() + timedelta(days=progress.interval)
    return progress


# ---------- Левенштейн / нормализация ----------

def levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна, O(len(a)*len(b)) по памяти O(len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def normalize(text: str) -> str:
    """Нижний регистр, без пунктуации, схлопнутые пробелы."""
    import re
    text = text.strip().lower()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text


def answer_matches(user_answer: str, expected: str) -> bool:
    """Сравнение с допуском на опечатки: <=1 для коротких (<=5), <=2 для длинных."""
    u = normalize(user_answer)
    e = normalize(expected)
    if u == e:
        return True
    dist = levenshtein(u, e)
    limit = 1 if len(e) <= 5 else 2
    return dist <= limit


# ---------- Экзамен ----------

PASS_SCORE = 18
EXAM_TOTAL = 20


def build_exam_questions(cards: list, count: int = EXAM_TOTAL) -> list[dict]:
    """
    Строит список вопросов из карточек.
    cards: список Card (или объектов с полями id, english_text, russian_text, example).
    Возвращает список dict:
      {"card_id", "type": "choice"|"text", "prompt", "correct", "options", "example"}
    prompt — русский, correct — английский.
    """
    import random
    pool = cards[:]
    random.shuffle(pool)
    selected = pool[: count]

    all_english = list({c.english_text for c in cards})

    questions = []
    for card in selected:
        qtype = random.choice(["choice", "text"])
        if qtype == "choice":
            distractors = [t for t in all_english if t != card.english_text]
            random.shuffle(distractors)
            options = distractors[:3] + [card.english_text]
            random.shuffle(options)
            questions.append({
                "card_id": card.id,
                "type": "choice",
                "prompt": card.russian_text,
                "correct": card.english_text,
                "options": options,
                "example": card.example,
            })
        else:
            questions.append({
                "card_id": card.id,
                "type": "text",
                "prompt": card.russian_text,
                "correct": card.english_text,
                "options": None,
                "example": card.example,
            })
    return questions


# ---------- Прогресс ----------

def compute_streak(activity_dates: set[date], today: date) -> int:
    """Дни подряд с любой активностью, считая сегодня или вчера."""
    if not activity_dates:
        return 0
    start = today if today in activity_dates else (today - timedelta(days=1))
    if start not in activity_dates:
        return 0
    streak = 0
    d = start
    while d in activity_dates:
        streak += 1
        d -= timedelta(days=1)
    return streak


# ---------- Безопасное удаление категории (Вариант А) ----------

async def delete_category_safe(session: AsyncSession, category_id: int) -> None:
    """
    Переносит карточки категории в системную «Без категории»,
    затем удаляет саму категорию. Прогресс и логи карточек сохраняются.
    Системную категорию удалить нельзя.
    """
    result = await session.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat is None:
        return
    if cat.is_system:
        raise ValueError("Нельзя удалить системную категорию")

    system_cat = await get_or_create_system_category(session)
    await session.execute(
        update(Card).where(Card.category_id == category_id).values(category_id=system_cat.id)
    )
    await session.execute(delete(Category).where(Category.id == category_id))
    await session.commit()