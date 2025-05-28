from passlib.context import CryptContext
from database.models import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from common.schemas import UserRegister
from database.orm_query import orm_add_user

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
#######################################################################################################################
async def get_user_by_login(session: AsyncSession, login: str):
    result = await session.execute(select(User).where(User.login == login))
    return result.scalar()

#######################################################################################################################
async def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

#######################################################################################################################
async def create_user(db: AsyncSession, user_data: UserRegister):
    try:
        hashed_password = pwd_context.hash(user_data.password)
        await orm_add_user(db, user_data.login, hashed_password)
        return
    except ValueError as e:
        raise ValueError("User already exists") from e

async def get_all_users(session: AsyncSession):
    result = await session.execute(select(User))
    return result.scalars().all()

async def get_user_by_id(session: AsyncSession, user_id: int):
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar()

async def update_user_position(session: AsyncSession, user_id: int, new_position: str):
    user = await get_user_by_id(session, user_id)
    if user:
        user.position = new_position
        await session.commit()
        return user
    return None