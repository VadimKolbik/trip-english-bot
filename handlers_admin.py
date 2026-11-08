from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func, desc
from datetime import date
import secrets

from config import config
from models import (
    async_session, Category, Card, InviteCode, User, 
    UserCardProgress, CardViewLog, ExamAttempt
)
from logic import today_local, compute_streak

router = Router(name="admin")

# ==========================================
# СОСТОЯНИЯ (FSM)
# ==========================================

class AddCategory(StatesGroup):
    title = State()

class AddCard(StatesGroup):
    category_id = State()
    english = State()
    russian = State()
    example = State()

class SendMessage(StatesGroup):
    user_id = State()
    text = State()

# ==========================================
# ХЕЛПЕРЫ И КЛАВИАТУРЫ
# ==========================================

def is_admin(user_id: int) -> bool:
    return user_id in config.superadmin_ids

def get_admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить категорию", callback_data="admin_add_cat")],
        [InlineKeyboardButton(text="➕ Добавить карточку", callback_data="admin_add_card")],
        [InlineKeyboardButton(text="🔗 Создать инвайт-код", callback_data="admin_gen_invite")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Прогресс всех", callback_data="admin_all_progress")], # <--- НОВОЕ
        [InlineKeyboardButton(text="✉️ Написать пользователю", callback_data="admin_send_msg")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu_main")]
    ])

def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_admin")]
    ])

# ==========================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# ==========================================

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    
    await message.answer(
        "🛠 <b>Панель администратора</b>\nВыбери действие:",
        reply_markup=get_admin_menu_kb()
    )

@router.callback_query(F.data == "menu_admin")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>Панель администратора</b>\nВыбери действие:",
        reply_markup=get_admin_menu_kb()
    )
    await callback.answer()

# ==========================================
# ДОБАВЛЕНИЕ КАТЕГОРИИ
# ==========================================

@router.callback_query(F.data == "admin_add_cat")
async def cb_add_cat(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return
    await state.set_state(AddCategory.title)
    await callback.message.edit_text(
        "Введи название новой категории (например: <i>🍔 В ресторане</i>):",
        reply_markup=get_cancel_kb()
    )
    await callback.answer()

@router.message(AddCategory.title)
async def process_add_cat(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if len(title) > 100:
        await message.answer("Слишком длинное название. Максимум 100 символов.")
        return

    async with async_session() as session:
        res = await session.execute(select(Category).where(Category.title == title))
        if res.scalar_one_or_none():
            await message.answer("Такая категория уже существует. Введи другое название.")
            return
            
        cat = Category(title=title, sort_order=0)
        session.add(cat)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Категория <b>{title}</b> успешно добавлена!",
        reply_markup=get_admin_menu_kb()
    )

# ==========================================
# ДОБАВЛЕНИЕ КАРТОЧКИ
# ==========================================

@router.callback_query(F.data == "admin_add_card")
async def cb_add_card(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return

    async with async_session() as session:
        res = await session.execute(select(Category).order_by(Category.sort_order))
        categories = res.scalars().all()

    if not categories:
        await callback.message.edit_text("Сначала создай хотя бы одну категорию!", reply_markup=get_admin_menu_kb())
        return

    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(text=cat.title, callback_data=f"adm_cat_{cat.id}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="menu_admin")])
    
    await state.set_state(AddCard.category_id)
    await callback.message.edit_text(
        "Выбери категорию для новой карточки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@router.callback_query(AddCard.category_id, F.data.startswith("adm_cat_"))
async def process_card_cat(callback: CallbackQuery, state: FSMContext) -> None:
    cat_id = int(callback.data.split("_")[2])
    await state.update_data(category_id=cat_id)
    
    await state.set_state(AddCard.english)
    await callback.message.edit_text(
        "Введи текст на <b>английском</b> (слово или фразу):",
        reply_markup=get_cancel_kb()
    )
    await callback.answer()

@router.message(AddCard.english)
async def process_card_en(message: Message, state: FSMContext) -> None:
    await state.update_data(english=message.text.strip())
    await state.set_state(AddCard.russian)
    await message.answer("Введи перевод на <b>русском</b>:")

@router.message(AddCard.russian)
async def process_card_ru(message: Message, state: FSMContext) -> None:
    await state.update_data(russian=message.text.strip())
    await state.set_state(AddCard.example)
    await message.answer(
        "Добавь <b>пример</b> на английском (или отправь <code>-</code>, чтобы пропустить):"
    )

@router.message(AddCard.example)
async def process_card_example(message: Message, state: FSMContext) -> None:
    example = message.text.strip()
    if example == "-":
        example = None

    data = await state.get_data()
    cat_id = data["category_id"]
    english = data["english"]
    russian = data["russian"]

    async with async_session() as session:
        card = Card(
            category_id=cat_id,
            english_text=english,
            russian_text=russian,
            example=example
        )
        session.add(card)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Карточка добавлена!\n🇬🇧 {english}\n🇷🇺 {russian}",
        reply_markup=get_admin_menu_kb()
    )

# ==========================================
# ИНВАЙТ-КОД
# ==========================================

@router.callback_query(F.data == "admin_gen_invite")
async def cb_gen_invite(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return

    code = secrets.token_urlsafe(6)
    async with async_session() as session:
        invite = InviteCode(code=code, created_by=callback.from_user.id)
        session.add(invite)
        await session.commit()

    bot_info = await callback.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={code}"

    await callback.message.edit_text(
        f"✅ Инвайт-код создан:\n\n<code>{link}</code>\n\nСкопируй и отправь другу.",
        reply_markup=get_admin_menu_kb()
    )
    await callback.answer()

# ==========================================
# СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# ==========================================

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return

    async with async_session() as session:
        res = await session.execute(select(User).order_by(User.registered_at))
        users = res.scalars().all()

    if not users:
        await callback.message.edit_text("В боте пока нет пользователей.", reply_markup=get_admin_menu_kb())
        return

    text = "👥 <b>Список пользователей:</b>\n\n"
    for u in users:
        username = f"@{u.username}" if u.username else "без username"
        name = u.first_name or "Без имени"
        text += f"• <b>{name}</b> ({username}) — <code>{u.id}</code>\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")]
        ])
    )
    await callback.answer()

# ==========================================
# ПРОГРЕСС ВСЕХ (ТОЛЬКО ДЛЯ АДМИНА)
# ==========================================

@router.callback_query(F.data == "admin_all_progress")
async def cb_admin_all_progress(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return

    async with async_session() as session:
        res = await session.execute(select(User).order_by(User.first_name))
        users = res.scalars().all()

    if not users:
        await callback.message.edit_text("В боте пока нет пользователей.", reply_markup=get_admin_menu_kb())
        return

    buttons = []
    for u in users:
        name = u.first_name or "Без имени"
        buttons.append([InlineKeyboardButton(text=f"📊 {name}", callback_data=f"adm_view_prog_{u.id}")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")])

    await callback.message.edit_text(
        "Выбери пользователя, чтобы посмотреть его прогресс:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("adm_view_prog_"))
async def cb_admin_view_user_progress(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return

    target_user_id = int(callback.data.split("_")[3])
    today = today_local()

    async with async_session() as session:
        user = await session.get(User, target_user_id)
        if not user:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return
        
        user_name = user.first_name or "Без имени"

        # 1. Всего изучено
        res_learned = await session.execute(
            select(func.count(UserCardProgress.id))
            .where(UserCardProgress.user_id == target_user_id)
            .where(UserCardProgress.repetitions > 0)
        )
        total_learned = res_learned.scalar() or 0

        # 2. Прочитано сегодня
        res_today = await session.execute(
            select(func.count(CardViewLog.id))
            .where(CardViewLog.user_id == target_user_id)
            .where(CardViewLog.view_date == today)
        )
        read_today = res_today.scalar() or 0

        # 3. Streak
        res_logs = await session.execute(
            select(CardViewLog.view_date)
            .where(CardViewLog.user_id == target_user_id)
            .distinct()
        )
        log_dates = {row[0] for row in res_logs.all()}

        res_exams = await session.execute(
            select(func.date(ExamAttempt.finished_at))
            .where(ExamAttempt.user_id == target_user_id)
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

        # 4. Последние 3 экзамена
        res_last_exams = await session.execute(
            select(ExamAttempt)
            .where(ExamAttempt.user_id == target_user_id)
            .order_by(desc(ExamAttempt.finished_at))
            .limit(3)
        )
        last_exams = res_last_exams.scalars().all()

    text = (
        f"📊 <b>Прогресс пользователя {user_name}</b>\n\n"
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

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_all_progress")]
        ])
    )
    await callback.answer()

# ==========================================
# ОТПРАВКА СООБЩЕНИЯ ПОЛЬЗОВАТЕЛЮ
# ==========================================

@router.callback_query(F.data == "admin_send_msg")
async def cb_admin_send_msg(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return

    async with async_session() as session:
        res = await session.execute(select(User).where(User.id != callback.from_user.id).order_by(User.first_name))
        users = res.scalars().all()

    if not users:
        await callback.message.edit_text("Нет других пользователей для отправки.", reply_markup=get_admin_menu_kb())
        return

    buttons = []
    for u in users:
        name = u.first_name or "Без имени"
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"adm_msg_to_{u.id}")])
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="menu_admin")])

    await state.set_state(SendMessage.user_id)
    await callback.message.edit_text(
        "Выбери пользователя, которому хочешь отправить сообщение:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@router.callback_query(SendMessage.user_id, F.data.startswith("adm_msg_to_"))
async def cb_admin_choose_recipient(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = int(callback.data.split("_")[3])
    await state.update_data(target_user_id=user_id)
    await state.set_state(SendMessage.text)
    
    await callback.message.edit_text(
        "Введи текст сообщения, которое хочешь отправить от имени бота:",
        reply_markup=get_cancel_kb()
    )
    await callback.answer()

@router.message(SendMessage.text)
async def process_send_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    text = message.text

    try:
        await message.bot.send_message(target_user_id, text)
        status = "✅ Сообщение успешно отправлено!"
    except Exception as e:
        status = f"❌ Ошибка при отправке: {e}"

    await state.clear()
    await message.answer(status, reply_markup=get_admin_menu_kb())