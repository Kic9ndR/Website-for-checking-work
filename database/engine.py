from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base

engine = create_async_engine("sqlite+aiosqlite:///checking_works.db", echo=True)
session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

async def get_session():
    async with session_maker() as session:
        yield session

async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)