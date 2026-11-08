from datetime import date, timedelta
import pytest

from logic import (
    sm2_update, levenshtein, normalize, answer_matches,
    compute_streak, build_exam_questions,
)
from models import UserCardProgress


class FakeCard:
    def __init__(self, id, en, ru, example=None):
        self.id = id
        self.english_text = en
        self.russian_text = ru
        self.example = example


def make_progress():
    return UserCardProgress(
        user_id=1, card_id=1, repetitions=0, interval=0,
        ease_factor=2.5, correct_count=0, wrong_count=0,
    )


# ---------- SM-2 ----------

def test_sm2_first_correct():
    p = sm2_update(make_progress(), quality=4)
    assert p.repetitions == 1
    assert p.interval == 1
    assert p.correct_count == 1
    assert p.next_review_at is not None


def test_sm2_second_correct():
    p = make_progress()
    p = sm2_update(p, 4)
    p = sm2_update(p, 4)
    assert p.repetitions == 2
    assert p.interval == 6


def test_sm2_third_correct_uses_ef():
    p = make_progress()
    for _ in range(3):
        p = sm2_update(p, 4)
    assert p.repetitions == 3
    assert p.interval >= 6


def test_sm2_wrong_resets():
    p = make_progress()
    p = sm2_update(p, 4)
    p = sm2_update(p, 4)
    p = sm2_update(p, 1)
    assert p.repetitions == 0
    assert p.interval == 1
    assert p.wrong_count == 1


def test_sm2_ef_floor():
    p = make_progress()
    for _ in range(10):
        p = sm2_update(p, 0)
    assert p.ease_factor >= 1.3


def test_sm2_invalid_quality():
    with pytest.raises(ValueError):
        sm2_update(make_progress(), 6)


# ---------- Левенштейн / нормализация ----------

def test_levenshtein_basic():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


def test_normalize():
    assert normalize("  Hello, World!  ") == "hello world"
    assert normalize("Don't   stop") == "dont stop"


def test_answer_matches_exact_and_typos():
    assert answer_matches("Hello", "hello")
    assert answer_matches("helo", "hello")          # 1 опечатка, len=5 -> limit 1
    assert not answer_matches("hellooo", "hello")   # 2 опечатки, len=5 -> limit 1
    assert answer_matches("restaurant", "restaraunt")  # len>5 -> limit 2


def test_answer_matches_punctuation():
    assert answer_matches("Where is the station?", "where is the station")


# ---------- Streak ----------

def test_streak_empty():
    assert compute_streak(set(), date(2026, 1, 1)) == 0


def test_streak_today():
    today = date(2026, 1, 10)
    days = {today, today - timedelta(days=1), today - timedelta(days=2)}
    assert compute_streak(days, today) == 3


def test_streak_yesterday_chain():
    today = date(2026, 1, 10)
    days = {today - timedelta(days=1), today - timedelta(days=2)}
    assert compute_streak(days, today) == 2


def test_streak_broken():
    today = date(2026, 1, 10)
    days = {today - timedelta(days=2)}
    assert compute_streak(days, today) == 0


# ---------- Экзамен ----------

def test_build_exam_questions_count_and_types():
    cards = [FakeCard(i, f"en{i}", f"ru{i}") for i in range(30)]
    qs = build_exam_questions(cards, count=20)
    assert len(qs) == 20
    for q in qs:
        assert q["type"] in ("choice", "text")
        if q["type"] == "choice":
            assert len(q["options"]) == 4
            assert q["correct"] in q["options"]