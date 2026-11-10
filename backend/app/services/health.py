"""상태 확인 업무 로직 (API-025)."""

from app.data import db


async def check_database() -> bool:
    return await db.ping()
