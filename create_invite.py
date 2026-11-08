import asyncio
import secrets
from models import init_db, async_session, InviteCode

async def main():
    await init_db()
    code = secrets.token_urlsafe(6)  # сгенерирует что-то типа "xR2_pQ"
    async with async_session() as session:
        invite = InviteCode(code=code, created_by=0)
        session.add(invite)
        await session.commit()
    print(f"\nИнвайт-код создан!\nСсылка: https://t.me/TripEnglishBot?start={code}\n")

if __name__ == "__main__":
    asyncio.run(main())