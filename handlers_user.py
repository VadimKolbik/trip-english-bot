from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func, desc
from datetime import date
from aiogram.exceptions import TelegramBadRequest

from models import (
    async_session, User, InviteCode, Category, Card, 
    UserCardProgress, CardViewLog, ExamAttempt, ProgressShare
)
from logic import (
    sm2_update, today_local, now_local_naive, 
    build_exam_questions, answer_matches, PASS_SCORE, EXAM_TOTAL,
    compute_streak
)
from config import config
from handlers_admin import get_admin_menu_kb

router = Router(name="user")


# ==========================================
# СОСТОЯНИЯ (FSM)
# ==========================================

class ExamStates(StatesGroup):
    in_progress = State()

class LearningStates(StatesGroup):
    waiting_for_answer = State()


# ==========================================
# КЛАВИАТУРЫ
# ==========================================

def get_main_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📚 Учить"), KeyboardButton(text="📝 Экзамен")],
        [KeyboardButton(text="👥 Друзья"), KeyboardButton(text="📊 Прогресс")],
        [KeyboardButton(text="⚙️ Настройки")],
    ]
    if is_admin:
        buttons.append([KeyboardButton(text="🛠 Админка")])
    
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие..."
    )

def get_back_kb(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]
    ])


# ==========================================
# ХЕЛПЕРЫ
# ==========================================

async def get_user(user_id: int) -> User | None:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


# ==========================================
# СТАРТ И РЕГИСТРАЦИЯ
# ==========================================

@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        return
    await message.answer(
        "Клавиатура открыта. Используй кнопки ниже.",
        reply_markup=get_main_reply_kb(user_id in config.superadmin_ids)
    )

@router.message(Command("hide"))
async def cmd_hide(message: Message) -> None:
    await message.answer(
        "Клавиатура скрыта. Чтобы вернуть её, отправь /menu",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(CommandStart(deep_link=True))
async def cmd_start_invite(message: Message, command: CommandObject) -> None:
    invite_code = command.args
    user_id = message.from_user.id
    
    existing_user = await get_user(user_id)
    if existing_user:
        await message.answer(
            "Ты уже зарегистрирован!\nИспользуй кнопки меню или /menu.", 
            reply_markup=get_main_reply_kb(user_id in config.superadmin_ids)
        )
        return

    async with async_session() as session:
        result = await session.execute(select(InviteCode).where(InviteCode.code == invite_code))
        code_obj = result.scalar_one_or_none()

        if not code_obj or code_obj.used_by is not None:
            await message.answer("❌ Неверный или уже использованный инвайт-код.")
            return

        new_user = User(
            id=user_id, 
            username=message.from_user.username, 
            first_name=message.from_user.first_name
        )
        session.add(new_user)
        code_obj.used_by = user_id
        await session.commit()

    await message.answer(
        f"🎉 Добро пожаловать, <b>{message.from_user.first_name}</b>!\nТвой аккаунт успешно создан.",
        reply_markup=get_main_reply_kb(user_id in config.superadmin_ids)
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Привет! Для доступа к боту нужна инвайт-ссылка.")
        return
    await message.answer(
        f"С возвращением, <b>{user.first_name}</b>!\nВыбери действие:", 
        reply_markup=get_main_reply_kb(user_id in config.superadmin_ids)
    )


@router.callback_query(F.data == "menu_main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete() 
    await callback.message.answer(
        "Главное меню. Выбери действие:", 
        reply_markup=get_main_reply_kb(callback.from_user.id in config.superadmin_ids)
    )
    await callback.answer()


# ==========================================
# ГЛАВНОЕ МЕНЮ (Reply-кнопки)
# ==========================================

@router.message(F.text == "📚 Учить")
async def menu_learn(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        res = await session.execute(select(Category).order_by(Category.sort_order))
        categories = res.scalars().all()
        
        if not categories:
            await message.answer("Пока нет ни одной категории.")
            return

        buttons = []
        for cat in categories:
            buttons.append([InlineKeyboardButton(text=cat.title, callback_data=f"learn_cat_{cat.id}")])
        
        await message.answer(
            "Выбери категорию для изучения:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

@router.message(F.text == "📝 Экзамен")
async def menu_exam(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session() as session:
        res = await session.execute(select(Category).order_by(Category.sort_order))
        categories = res.scalars().all()

        if not categories:
            await message.answer("Пока нет ни одной категории для экзамена.")
            return

        buttons = []
        buttons.append([InlineKeyboardButton(text="🌍 Все карточки", callback_data="exam_cat_0")])
        
        for cat in categories:
            buttons.append([InlineKeyboardButton(text=cat.title, callback_data=f"exam_cat_{cat.id}")])
        
        await message.answer(
            "Выбери категорию для экзамена (20 вопросов):", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

@router.message(F.text == "👥 Друзья")
async def menu_friends(message: Message, state: FSMContext) -> None:
    await state.clear()
    await cb_friends_logic(message, message.from_user.id)

@router.message(F.text == "📊 Прогресс")
async def menu_progress(message: Message, state: FSMContext) -> None:
    await state.clear()
    await cb_progress_logic(message, message.from_user.id)

@router.message(F.text == "⚙️ Настройки")
async def menu_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await cb_settings_logic(message, message.from_user.id)

@router.message(F.text == "🛠 Админка")
async def menu_admin(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in config.superadmin_ids:
        await message.answer("У вас нет доступа к админ-панели.")
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Панель администратора</b>\nВыбери действие:",
        reply_markup=get_admin_menu_kb()
    )


# ==========================================
# ИЗУЧЕНИЕ КАРТОЧЕК
# ==========================================

@router.callback_query(F.data == "menu_learn")
async def cb_menu_learn(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    async with async_session() as session:
        res = await session.execute(select(Category).order_by(Category.sort_order))
        categories = res.scalars().all()
        
        if not categories:
            await callback.message.answer("Пока нет ни одной категории.")
            await callback.answer()
            return

        buttons = []
        for cat in categories:
            buttons.append([InlineKeyboardButton(text=cat.title, callback_data=f"learn_cat_{cat.id}")])
        
        await callback.message.answer(
            "Выбери категорию для изучения:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    await callback.answer()

@router.callback_query(F.data.startswith("learn_cat_"))
async def cb_learn_category(callback: CallbackQuery, state: FSMContext) -> None:
    cat_id = int(callback.data.split("_")[2])
    await show_next_card(callback.message, callback.from_user.id, cat_id, state, is_edit=True, force_all=False)
    await callback.answer()

@router.callback_query(F.data.startswith("learn_force_"))
async def cb_learn_force(callback: CallbackQuery, state: FSMContext) -> None:
    cat_id = int(callback.data.split("_")[2])
    await show_next_card(callback.message, callback.from_user.id, cat_id, state, is_edit=True, force_all=True)
    await callback.answer()


async def show_next_card(message: Message, user_id: int, cat_id: int, state: FSMContext, is_edit: bool = False, force_all: bool = False) -> None:
    await state.clear()
    
    async with async_session() as session:
        now = now_local_naive()
        
        query = (
            select(Card)
            .outerjoin(UserCardProgress, (UserCardProgress.card_id == Card.id) & (UserCardProgress.user_id == user_id))
            .where(Card.category_id == cat_id)
        )
        
        if not force_all:
            query = query.where((UserCardProgress.next_review_at == None) | (UserCardProgress.next_review_at <= now))
        
        query = query.order_by(UserCardProgress.next_review_at.asc().nulls_first()).limit(1)
        
        res = await session.execute(query)
        card = res.scalar_one_or_none()

        if not card:
            if force_all:
                # Если мы в режиме повторения, но карточек нет — значит, их нет вообще
                text = "В этой категории пока нет карточек (или они были удалены)."
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К категориям", callback_data="menu_learn")]
                ])
            else:
                # Если карточки есть, но на сегодня всё выучено
                text = (
                    "🎉 Ты выучил все карточки в этой категории на сегодня!\n"
                    "Но ты всегда можешь повторить их принудительно."
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Повторить все карточки", callback_data=f"learn_force_{cat_id}")],
                    [InlineKeyboardButton(text="⬅️ К категориям", callback_data="menu_learn")]
                ])
            
            if is_edit:
                # Используем try/except на случай, если текст не изменился
                try:
                    await message.edit_text(text, reply_markup=kb)
                except TelegramBadRequest:
                    pass
            else:
                await message.answer(text, reply_markup=kb)
            return

        today = today_local()
        log_query = select(CardViewLog).where(
            CardViewLog.user_id == user_id,
            CardViewLog.card_id == card.id,
            CardViewLog.view_date == today
        )
        log_res = await session.execute(log_query)
        if not log_res.scalar_one_or_none():
            session.add(CardViewLog(user_id=user_id, card_id=card.id, view_date=today))
            await session.commit()

        text = (
            f"🇬🇧 <b>{card.english_text}</b>\n\n"
            f"<i>Напиши перевод в чат для проверки или нажми кнопку ниже.</i>"
        )
        
        mode = "force" if force_all else "normal"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👀 Показать перевод", callback_data=f"card_show_{card.id}_{cat_id}_{mode}")],
            [InlineKeyboardButton(text="⬅️ К категориям", callback_data="menu_learn")]
        ])
        
        if is_edit:
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        
        await state.set_state(LearningStates.waiting_for_answer)
        await state.update_data(
            current_card_id=card.id,
            current_cat_id=cat_id,
            mode=mode,
            correct_answer=card.russian_text
        )


@router.message(LearningStates.waiting_for_answer)
async def process_learning_answer(message: Message, state: FSMContext) -> None:
    if message.text in ["📚 Учить", "📝 Экзамен", "👥 Друзья", "📊 Прогресс", "⚙️ Настройки", "🛠 Админка"]:
        return

    data = await state.get_data()
    if "current_card_id" not in data:
        await state.clear()
        return

    card_id = data["current_card_id"]
    cat_id = data["current_cat_id"]
    mode = data["mode"]
    correct_answer = data["correct_answer"]
    user_answer = message.text.strip()

    is_correct = answer_matches(user_answer, correct_answer)
    await state.clear()

    if is_correct:
        feedback = "✅ <b>Верно!</b>"
    else:
        feedback = f"❌ <b>Ошибка.</b>\nПравильный ответ: <b>{correct_answer}</b>"

    await message.answer(feedback)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Забыл", callback_data=f"card_ans_{card_id}_{cat_id}_{mode}_1"),
            InlineKeyboardButton(text="✅ Помню", callback_data=f"card_ans_{card_id}_{cat_id}_{mode}_4")
        ]
    ])
    await message.answer("Оцени, насколько хорошо ты знал эту карточку:", reply_markup=kb)


@router.callback_query(F.data.startswith("card_show_"))
async def cb_card_show(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, card_id, cat_id, mode = callback.data.split("_")
    card_id, cat_id = int(card_id), int(cat_id)

    await state.clear()

    async with async_session() as session:
        card = await session.get(Card, card_id)
        
        text = f"🇬🇧 <b>{card.english_text}</b>\n🇷🇺 {card.russian_text}"
        if card.example:
            text += f"\n\n📝 <i>{card.example}</i>"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Забыл", callback_data=f"card_ans_{card_id}_{cat_id}_{mode}_1"),
                InlineKeyboardButton(text="✅ Помню", callback_data=f"card_ans_{card_id}_{cat_id}_{mode}_4")
            ]
        ])
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("card_ans_"))
async def cb_card_answer(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, card_id, cat_id, mode, quality = callback.data.split("_")
    card_id, cat_id, quality = int(card_id), int(cat_id), int(quality)
    force_all = (mode == "force")
    user_id = callback.from_user.id

    async with async_session() as session:
        res = await session.execute(
            select(UserCardProgress).where(
                UserCardProgress.user_id == user_id, 
                UserCardProgress.card_id == card_id
            )
        )
        progress = res.scalar_one_or_none()

        if not progress:
            progress = UserCardProgress(
                user_id=user_id, 
                card_id=card_id,
                repetitions=0,
                interval=0,
                ease_factor=2.5,
                correct_count=0,
                wrong_count=0
            )
            session.add(progress)

        sm2_update(progress, quality)
        await session.commit()

    await show_next_card(callback.message, user_id, cat_id, state, is_edit=True, force_all=force_all)
    await callback.answer()


# ==========================================
# ЭКЗАМЕН
# ==========================================

@router.callback_query(F.data.startswith("exam_cat_"))
async def cb_exam_category(callback: CallbackQuery, state: FSMContext) -> None:
    cat_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        query = select(Card)
        if cat_id != 0:
            query = query.where(Card.category_id == cat_id)
        
        res = await session.execute(query)
        cards = res.scalars().all()

    if len(cards) < 4:
        await callback.message.edit_text(
            "Для экзамена нужно минимум 4 карточки.", 
            reply_markup=get_back_kb("menu_main")
        )
        return

    questions = build_exam_questions(cards, count=min(EXAM_TOTAL, len(cards)))

    await state.set_state(ExamStates.in_progress)
    await state.update_data(
        questions=questions,
        current_q=0,
        correct=0,
        cat_id=cat_id
    )

    await callback.message.edit_text(f"📝 Экзамен начался! Вопросов: {len(questions)}.\nПоехали!")
    await ask_question(callback.message, state, is_edit=False)
    await callback.answer()


async def ask_question(message: Message, state: FSMContext, is_edit: bool = True) -> None:
    data = await state.get_data()
    questions = data["questions"]
    idx = data["current_q"]

    if idx >= len(questions):
        await finish_exam(message, state, is_edit)
        return

    q = questions[idx]
    q_num = idx + 1
    text = f"<b>Вопрос {q_num}/{len(questions)}</b>\n\nПереведи на английский:\n🇷🇺 <b>{q['prompt']}</b>"

    if q["type"] == "choice":
        buttons = []
        for opt in q["options"]:
            opt_idx = q["options"].index(opt)
            buttons.append([InlineKeyboardButton(text=opt, callback_data=f"exam_ans_{opt_idx}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await state.update_data(waiting_text=False)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Пропустить", callback_data="exam_skip")]])
        text += "\n\n<i>Напиши ответ текстом:</i>"
        await state.update_data(waiting_text=True)

    if is_edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(ExamStates.in_progress, F.data.startswith("exam_ans_"))
async def cb_exam_choice(callback: CallbackQuery, state: FSMContext) -> None:
    opt_idx = int(callback.data.split("_")[2])
    
    data = await state.get_data()
    q = data["questions"][data["current_q"]]

    is_correct = (q["options"][opt_idx] == q["correct"])
    await process_answer(callback.message, state, is_correct, callback=callback)


@router.callback_query(ExamStates.in_progress, F.data == "exam_skip")
async def cb_exam_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await process_answer(callback.message, state, False, callback=callback)


@router.message(ExamStates.in_progress)
async def cb_exam_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("waiting_text"):
        return 

    q = data["questions"][data["current_q"]]
    is_correct = answer_matches(message.text, q["correct"])
    await process_answer(message, state, is_correct, is_text_input=True)


async def process_answer(message: Message, state: FSMContext, is_correct: bool, callback: CallbackQuery = None, is_text_input: bool = False) -> None:
    data = await state.get_data()
    q = data["questions"][data["current_q"]]
    
    correct_count = data["correct"]
    if is_correct:
        correct_count += 1
    
    await state.update_data(correct=correct_count, current_q=data["current_q"] + 1, waiting_text=False)

    if is_correct:
        feedback = "✅ <b>Верно!</b>"
    else:
        feedback = f"❌ <b>Ошибка.</b>\nПравильный ответ: <b>{q['correct']}</b>"
        if q.get("example"):
             feedback += f"\n<i>{q['example']}</i>"

    if callback:
        await callback.answer()
        await message.answer(feedback)
        await ask_question(message, state, is_edit=False)
    else:
        await message.answer(feedback)
        await ask_question(message, state, is_edit=False)


async def finish_exam(message: Message, state: FSMContext, is_edit: bool) -> None:
    data = await state.get_data()
    await state.clear()

    correct = data["correct"]
    total = len(data["questions"])
    cat_id = data["cat_id"]
    user_id = message.chat.id

    passed = correct >= PASS_SCORE
    if total < PASS_SCORE:
        passed = correct == total

    async with async_session() as session:
        attempt = ExamAttempt(
            user_id=user_id,
            category_id=cat_id if cat_id != 0 else None,
            finished_at=now_local_naive(),
            correct_count=correct,
            total_count=total,
            passed=passed
        )
        session.add(attempt)
        await session.commit()

    text = (
        f"🏁 <b>Экзамен завершен!</b>\n\n"
        f"Твой результат: <b>{correct} из {total}</b>\n"
        f"Проходной балл: {PASS_SCORE if total >= PASS_SCORE else total}\n\n"
    )
    if passed:
        text += "🎉 <b>Отлично! Ты сдал экзамен.</b>"
    else:
        text += "😔 <b>Пока не хватает баллов. Попробуй еще раз!</b>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Пройти еще раз", callback_data=f"exam_cat_{cat_id}")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_main")]
    ])

    if is_edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ==========================================
# ДРУЗЬЯ, ПРОГРЕСС И НАСТРОЙКИ
# ==========================================

async def cb_friends_logic(message: Message, user_id: int) -> None:
    async with async_session() as session:
        res_in = await session.execute(
            select(ProgressShare, User.first_name)
            .join(User, ProgressShare.from_user_id == User.id)
            .where(ProgressShare.to_user_id == user_id, ProgressShare.status == "pending")
        )
        incoming = res_in.all()

        res_out = await session.execute(
            select(ProgressShare, User.first_name)
            .join(User, ProgressShare.to_user_id == User.id)
            .where(ProgressShare.from_user_id == user_id, ProgressShare.status == "approved")
        )
        approved_friends = res_out.all()

    text = "👥 <b>Друзья и прогресс</b>\n\n"
    
    if incoming:
        text += "<b>Входящие заявки:</b>\n"
        for req, name in incoming:
            text += f"Пользователь {name} хочет видеть твой прогресс.\n"
    
    buttons = []
    
    if incoming:
        for req, name in incoming:
            buttons.append([
                InlineKeyboardButton(text=f"✅ {name}", callback_data=f"friend_accept_{req.id}"),
                InlineKeyboardButton(text=f"❌ {name}", callback_data=f"friend_reject_{req.id}")
            ])

    if approved_friends:
        text += "\n<b>Твой прогресс видят:</b>\n"
        for req, name in approved_friends:
            text += f"• {name}\n"
            buttons.append([InlineKeyboardButton(text=f"📊 Прогресс {name}", callback_data=f"friend_view_{req.to_user_id}")])

    async with async_session() as session:
        res_pending_out = await session.execute(
            select(ProgressShare, User.first_name)
            .join(User, ProgressShare.to_user_id == User.id)
            .where(ProgressShare.from_user_id == user_id, ProgressShare.status == "pending")
        )
        pending_out = res_pending_out.all()
        if pending_out:
            text += "\n<b>Ожидают подтверждения:</b>\n"
            for req, name in pending_out:
                text += f"• {name}\n"

    buttons.append([InlineKeyboardButton(text="📨 Отправить заявку", callback_data="friend_send_request")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def cb_progress_logic(message: Message, user_id: int) -> None:
    today = today_local()

    async with async_session() as session:
        res_learned = await session.execute(
            select(func.count(UserCardProgress.id))
            .where(UserCardProgress.user_id == user_id)
            .where(UserCardProgress.repetitions > 0)
        )
        total_learned = res_learned.scalar() or 0

        res_today = await session.execute(
            select(func.count(CardViewLog.id))
            .where(CardViewLog.user_id == user_id)
            .where(CardViewLog.view_date == today)
        )
        read_today = res_today.scalar() or 0

        res_logs = await session.execute(
            select(CardViewLog.view_date)
            .where(CardViewLog.user_id == user_id)
            .distinct()
        )
        log_dates = {row[0] for row in res_logs.all()}

        res_exams = await session.execute(
            select(func.date(ExamAttempt.finished_at))
            .where(ExamAttempt.user_id == user_id)
            .where(ExamAttempt.finished_at != None)
            .distinct()
        )
        exam_dates = set()
        for row in res_exams.all():
            if row[0]:
                if isinstance(row[0], str):
                    exam_dates.add(date.fromisoformat(row[0]))
                else:
                    exam_dates.add(row[0])

        all_activity_dates = log_dates.union(exam_dates)
        streak = compute_streak(all_activity_dates, today)

        res_last_exams = await session.execute(
            select(ExamAttempt)
            .where(ExamAttempt.user_id == user_id)
            .order_by(desc(ExamAttempt.finished_at))
            .limit(3)
        )
        last_exams = res_last_exams.scalars().all()

    text = (
        f"📊 <b>Твой прогресс</b>\n\n"
        f"📖 Изучено карточек всего: <b>{total_learned}</b>\n"
        f"📅 Прочитано сегодня: <b>{read_today}</b>\n"
        f"🔥 Дней подряд (streak): <b>{streak}</b>\n\n"
    )

    if last_exams:
        text += "📝 <b>Последние экзамены:</b>\n"
        for ex in last_exams:
            status = "✅" if ex.passed else "❌"
            date_str = ex.finished_at.strftime("%d.%m") if ex.finished_at else ""
            text += f"{status} {ex.correct_count}/{ex.total_count} ({date_str})\n"
    else:
        text += "Экзаменов пока не было.\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await message.answer(text, reply_markup=kb)


async def cb_settings_logic(message: Message, user_id: int) -> None:
    user = await get_user(user_id)

    status = "включены" if user.notifications_enabled else "выключены"
    btn_text = "🔕 Отключить уведомления" if user.notifications_enabled else "🔔 Включить уведомления"
    
    text = (
        f"⚙️ <b>Настройки</b>\n\n"
        f"Ежедневные напоминания в 22:00: <b>{status}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, callback_data="settings_toggle_notify")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "settings_toggle_notify")
async def cb_toggle_notify(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    async with async_session() as session:
        res = await session.execute(select(User).where(User.id == user_id))
        user = res.scalar_one_or_none()
        if user:
            user.notifications_enabled = not user.notifications_enabled
            await session.commit()
    
    await callback.message.delete()
    await cb_settings_logic(callback.message, user_id)
    await callback.answer()


# ==========================================
# ДРУЗЬЯ: ЗАЯВКИ И ПРОСМОТР
# ==========================================

@router.callback_query(F.data == "friend_send_request")
async def cb_friend_send_request(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    
    async with async_session() as session:
        res = await session.execute(
            select(User).where(User.id != user_id).order_by(User.first_name)
        )
        users = res.scalars().all()

    if not users:
        await callback.message.edit_text("Пока нет других пользователей в боте.", reply_markup=get_back_kb("menu_main"))
        return

    buttons = []
    for u in users:
        name = u.first_name or "Без имени"
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"friend_send_to_{u.id}")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")])
    
    await callback.message.edit_text(
        "Выбери пользователя, которому хочешь отправить заявку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("friend_send_to_"))
async def cb_friend_send_to(callback: CallbackQuery) -> None:
    to_user_id = int(callback.data.split("_")[3])
    from_user_id = callback.from_user.id

    async with async_session() as session:
        res = await session.execute(
            select(ProgressShare).where(
                ProgressShare.from_user_id == from_user_id,
                ProgressShare.to_user_id == to_user_id
            )
        )
        existing = res.scalar_one_or_none()
        
        if existing:
            if existing.status == "pending":
                await callback.answer("Заявка уже отправлена.", show_alert=True)
                return
            elif existing.status == "approved":
                await callback.answer("Уже разрешено.", show_alert=True)
                return
            else:
                existing.status = "pending"
                req_id = existing.id
        else:
            new_req = ProgressShare(from_user_id=from_user_id, to_user_id=to_user_id, status="pending")
            session.add(new_req)
            await session.flush()
            req_id = new_req.id
        
        await session.commit()

    try:
        await callback.bot.send_message(
            to_user_id,
            f"👋 Пользователь <b>{callback.from_user.first_name}</b> хочет видеть твой прогресс.\nРазрешить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Разрешить", callback_data=f"friend_accept_{req_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"friend_reject_{req_id}")
                ]
            ])
        )
    except Exception:
        pass

    await callback.message.edit_text("✅ Заявка отправлена!", reply_markup=get_back_kb("menu_main"))
    await callback.answer()


@router.callback_query(F.data.startswith("friend_accept_"))
async def cb_friend_accept(callback: CallbackQuery) -> None:
    req_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        req = await session.get(ProgressShare, req_id)
        if req and req.to_user_id == callback.from_user.id:
            req.status = "approved"
            await session.commit()
            await callback.message.edit_text("✅ Вы разрешили просмотр вашего прогресса.")
        else:
            await callback.answer("Заявка не найдена.", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("friend_reject_"))
async def cb_friend_reject(callback: CallbackQuery) -> None:
    req_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        req = await session.get(ProgressShare, req_id)
        if req and req.to_user_id == callback.from_user.id:
            req.status = "rejected"
            await session.commit()
            await callback.message.edit_text("❌ Вы отклонили заявку.")
        else:
            await callback.answer("Заявка не найдена.", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("friend_view_"))
async def cb_friend_view(callback: CallbackQuery) -> None:
    friend_id = int(callback.data.split("_")[2])
    today = today_local()

    async with async_session() as session:
        res_perm = await session.execute(
            select(ProgressShare).where(
                ProgressShare.from_user_id == friend_id,
                ProgressShare.to_user_id == callback.from_user.id,
                ProgressShare.status == "approved"
            )
        )
        if not res_perm.scalar_one_or_none():
            await callback.answer("Нет разрешения.", show_alert=True)
            return

        friend = await session.get(User, friend_id)
        friend_name = friend.first_name if friend else "Друг"

        res_learned = await session.execute(
            select(func.count(UserCardProgress.id))
            .where(UserCardProgress.user_id == friend_id)
            .where(UserCardProgress.repetitions > 0)
        )
        total_learned = res_learned.scalar() or 0

        res_today = await session.execute(
            select(func.count(CardViewLog.id))
            .where(CardViewLog.user_id == friend_id)
            .where(CardViewLog.view_date == today)
        )
        read_today = res_today.scalar() or 0

    text = (
        f"📊 <b>Прогресс {friend_name}</b>\n\n"
        f"📖 Изучено: <b>{total_learned}</b>\n"
        f"📅 Сегодня: <b>{read_today}</b>\n"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_kb("menu_main"))
    await callback.answer()