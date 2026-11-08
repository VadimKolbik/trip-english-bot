import asyncio
from sqlalchemy import select
from models import init_db, async_session, Category, Card

# Данные для наполнения: Категория -> Список карточек (англ, рус, пример)
DATA = {
    "✈️ Аэропорт и перелет": [
        ("Where is the check-in desk?", "Где стойка регистрации?", "Excuse me, where is the check-in desk for flight SU123?"),
        ("boarding pass", "посадочный талон", "Please have your passport and boarding pass ready."),
        ("luggage / baggage", "багаж", "I have two pieces of luggage."),
        ("hand luggage", "ручная кладь", "Your hand luggage must fit in the overhead bin."),
        ("delayed", "задержан (о рейсе)", "The flight to Rome is delayed by two hours."),
        ("cancelled", "отменен", "Our flight was cancelled due to bad weather."),
        ("passport control", "паспортный контроль", "You need to go through passport control first."),
        ("security check", "проверка безопасности", "Please take off your shoes for the security check."),
        ("departure gate", "выход на посадку", "Boarding starts at 10 AM at departure gate 4."),
        ("aisle seat", "место у прохода", "I would prefer an aisle seat, please."),
        ("window seat", "место у окна", "Can I have a window seat?"),
        ("customs", "таможня", "Do I need to declare anything at customs?"),
    ],
    "🏨 Отель": [
        ("I have a reservation.", "У меня есть бронирование.", "Hello, I have a reservation under the name Ivanov."),
        ("check in / check out", "заселиться / выселиться", "What time can we check in?"),
        ("room key", "ключ от номера", "I lost my room key, could you help me?"),
        ("Wi-Fi password", "пароль от Wi-Fi", "Could you tell me the Wi-Fi password?"),
        ("breakfast included", "завтрак включен", "Is breakfast included in the price?"),
        ("single room / double room", "одноместный / двухместный номер", "We booked a double room with a sea view."),
        ("air conditioning", "кондиционер", "The air conditioning in our room is not working."),
        ("towels", "полотенца", "Could we have some extra towels, please?"),
        ("wake-up call", "звонок-будильник", "I would like to request a wake-up call at 7 AM."),
        ("how much is it per night?", "сколько стоит за ночь?", "How much is it per night for this room?"),
    ],
    "🍽 Ресторан и кафе": [
        ("table for two, please", "столик на двоих, пожалуйста", "Good evening, a table for two, please."),
        ("menu, please", "меню, пожалуйста", "Could we see the menu, please?"),
        ("What do you recommend?", "Что вы посоветуете?", "I don't know what to choose. What do you recommend?"),
        ("I am allergic to...", "У меня аллергия на...", "I am allergic to nuts, does this dish contain any?"),
        ("vegetarian dish", "вегетарианское блюдо", "Do you have any vegetarian dishes?"),
        ("the bill, please", "счет, пожалуйста", "We are ready to leave. The bill, please."),
        ("Can I pay by card?", "Можно оплатить картой?", "Can I pay by credit card or only cash?"),
        ("tap water", "вода из-под крана", "Could we have a jug of tap water, please."),
        ("tip", "чаевые", "Is the tip included in the bill?"),
        ("takeaway / to go", "с собой", "I would like a coffee to go, please."),
    ],
    "🚕 Транспорт и навигация": [
        ("How do I get to...?", "Как добраться до...?", "Excuse me, how do I get to the Colosseum?"),
        ("Where is the nearest metro station?", "Где ближайшая станция метро?", "Where is the nearest metro station around here?"),
        ("ticket", "билет", "Where can I buy a bus ticket?"),
        ("one-way / return ticket", "билет в одну сторону / туда-обратно", "I need a return ticket to the airport."),
        ("Is it far from here?", "Это далеко отсюда?", "Is the train station far from here?"),
        ("turn left / turn right", "поверните налево / направо", "Go straight and then turn left at the traffic lights."),
        ("stop", "остановка", "Which bus stop is for the city center?"),
        ("taxi rank", "стоянка такси", "Where is the nearest taxi rank?"),
        ("How much does it cost to...?", "Сколько стоит доехать до...?", "How much does it cost to get to the hotel?"),
        ("traffic jam", "пробка", "We are stuck in a traffic jam, I will be late."),
    ],
    "🛍 Покупки": [
        ("How much does this cost?", "Сколько это стоит?", "Excuse me, how much does this T-shirt cost?"),
        ("Do you have this in a different size?", "У вас есть это другого размера?", "I like this dress, do you have it in a different size?"),
        ("Can I try it on?", "Можно это примерить?", "Can I try on these shoes, please?"),
        ("fitting room", "примерочная", "Where is the fitting room?"),
        ("discount", "скидка", "Is there a discount on this item?"),
        ("receipt", "чек", "Can I have a receipt, please?"),
        ("I am just looking.", "Я просто смотрю.", "No thanks, I am just looking."),
        ("open / closed", "открыто / закрыто", "What time does the shop close?"),
    ],
    "🚨 Экстренные ситуации": [
        ("Help!", "Помогите!", "Help! Call the police!"),
        ("I need a doctor.", "Мне нужен врач.", "I feel very sick, I need a doctor."),
        ("Call an ambulance.", "Вызовите скорую.", "Please call an ambulance immediately."),
        ("Where is the nearest pharmacy?", "Где ближайшая аптека?", "Where is the nearest pharmacy? I need painkillers."),
        ("I lost my passport.", "Я потерял паспорт.", "I can't find my passport anywhere, I lost it."),
        ("I lost my wallet.", "Я потерял кошелек.", "My wallet was stolen, I need to block my cards."),
        ("police station", "полицейский участок", "Could you show me the way to the police station?"),
        ("I don't understand.", "Я не понимаю.", "Sorry, I don't understand. Do you speak Russian?"),
        ("Could you speak slower, please?", "Не могли бы вы говорить помедленнее?", "Could you speak slower, please?"),
        ("Could you write it down?", "Не могли бы вы это записать?", "I didn't catch that. Could you write it down?"),
    ]
}

async def main():
    print("⏳ Инициализация базы данных...")
    await init_db()
    
    async with async_session() as session:
        # Проверяем, есть ли уже данные, чтобы не дублировать
        res = await session.execute(select(Category).where(Category.is_system == False))
        if res.scalars().first():
            print("⚠️ База уже наполнена. Если хотите перезалить, удалите файл data/bot.db и запустите снова.")
            return

        print("📦 Добавляем категории и карточки...")
        count = 0
        for cat_title, cards in DATA.items():
            # Создаем категорию
            category = Category(title=cat_title, sort_order=1)
            session.add(category)
            await session.flush() # Получаем ID категории
            
            # Создаем карточки
            for en, ru, *example in cards:
                ex = example[0] if example else None
                card = Card(
                    category_id=category.id,
                    english_text=en,
                    russian_text=ru,
                    example=ex
                )
                session.add(card)
                count += 1
                
        await session.commit()
        print(f"✅ Успешно добавлено {len(DATA)} категорий и {count} карточек!")

if __name__ == "__main__":
    asyncio.run(main())