from sqlalchemy import select, update, delete
import sqlalchemy
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import *

#______________________________________________________________________________________________________________________
async def orm_add_user(session: AsyncSession, login: str, password: str):
    user = User(login=login, password=password)
    session.add(user)
    try:
        await session.commit()
    except sqlalchemy.exc.IntegrityError as e:  # Конкретное исключение для дубликатов:
        await session.rollback()
        raise ValueError("User already exists") from e

#______________________________________________________________________________________________________________________
async def orm_get_user_info(
        session: AsyncSession,
        login: str,
):
    query = select(User).where(User.login == login)
    result = await session.execute(query)
    return result.scalar()

#______________________________________________________________________________________________________________________
async def orm_get_all_users(session: AsyncSession):
    query = select(User)
    result = await session.execute(query)
    return result.scalars().all()

#__________________________ Проекты в работе __________________________________________________________________________
async def orm_get_user_current_work(session: AsyncSession, user_id: int):
    """
    Получает текущую работу, которую проверяет пользователь.
    """
    try:
        # Получаем пользователя с его текущими работами (inspected_works)
        query = select(User).options(selectinload(User.inspected_works)).where(User.id == user_id)
        result = await session.execute(query)
        user = result.scalar()
        
        if user and user.inspected_works:
            # Возвращаем первую работу, которую проверяет пользователь
            # (если пользователь проверяет только одну работу)
            return user.inspected_works[0]
        return None
    except SQLAlchemyError as e:
        raise ValueError(f"Ошибка при получении текущей работы: {str(e)}")

#_______________________________ Выполенные проекты ___________________________________________________________________
async def orm_get_user_completed_projects(session: AsyncSession, user_id: int):
    """
    Получает список завершенных проектов пользователя по его ID.
    """
    try:
        # Получаем пользователя с завершенными проектами
        query = select(User).options(selectinload(User.completed_projects)).where(User.id == user_id)
        result = await session.execute(query)
        user = result.scalar()
        
        if user and user.completed_projects:
            return user.completed_projects
        return []
    except SQLAlchemyError as e:
        raise ValueError(f"Ошибка при получении завершенных проектов: {str(e)}")

#######################################################################################################################
#######################################################################################################################
async def orm_get_works(
        session: AsyncSession,
):
    query = select(Work).order_by(Work.created_at.desc())
    result = await session.execute(query)
    return result.scalars().all()

#______________________________________________________________________________________________________________________
async def orm_get_works_count(session: AsyncSession):
    query = select(func.count(Work.id))
    result = await session.execute(query)
    return result.scalar()

#______________________________________________________________________________________________________________________
async def orm_add_work(
    session: AsyncSession,
    title: str,
    work_link: str,
    booklet: str,
):
    query = select(Work).where(Work.title == title)
    result = await session.execute(query)
    if result.first() is None:
        session.add(
            Work(
                title=title,
                work_link=work_link,
                booklet=booklet
            )
        )
    await session.commit()
#______________________________________________________________________________________________________________________
async def orm_delete_work(session: AsyncSession, work_id: str):
    query = delete(Work).where(Work.id == work_id)
    await session.execute(query)
    await session.commit()

#______________________________________________________________________________________________________________________
async def orm_get_work(session: AsyncSession, work_id: int):
    query = select(Work).where(Work.id == work_id)
    result = await session.execute(query)
    return result.scalar()

#______________________________________________________________________________________________________________________
async def orm_add_inspector(session: AsyncSession, work_id: int, inspector_id: int):
    query = (
        update(Work)
        .where(Work.id == work_id)
        .values(inspector=inspector_id)
    )
    await session.execute(query)
    await session.commit()

#______________________________________________________________________________________________________________________
async def orm_assigned_to(session: AsyncSession, work_id: int, assigned_to: bool):
    """
    Изменения в ячейке назначена ли работа
    Есть проверяющий (True) Нет проверяющего (False)
    """
    query = (
        update(Work)
        .where(Work.id == work_id)
        .values(assigned_to=assigned_to)
    )
    await session.execute(query)
    await session.commit()

#______________________________________________________________________________________________________________________
async def orm_add_user_completed_project(session: AsyncSession, user_id: int, work_id: int):
    """
    Добавляет запись в таблицу user_completed_projects.
    """
    try:
        # Создаем запись в таблице user_completed_projects
        stmt = user_completed_projects.insert().values(user_id=user_id, work_id=work_id)
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        raise ValueError(f"Ошибка при добавлении записи: {str(e)}")
    
#______________________________________________________________________________________________________________________
async def orm_remove_user_completed_project(session: AsyncSession, user_id: int, work_id: int):
    """
    Удаляет запись из таблицы user_completed_projects.
    """
    try:
        # Удаляем запись из таблицы user_completed_projects
        stmt = delete(user_completed_projects).where(
            (user_completed_projects.c.user_id == user_id) &
            (user_completed_projects.c.work_id == work_id)
        )
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        raise ValueError(f"Ошибка при удалении записи: {str(e)}")
    

#######################################################################################################################
async def orm_add_completed_work(session: AsyncSession, user_id: int, work_id: int, title: str):
    """
    Добавляет запись о завершенной работе в таблицу completed_works.
    """
    try:
        completed_work = CompletedWorks(user_id=user_id, work_id=work_id, title=title)
        session.add(completed_work)
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        raise ValueError(f"Ошибка при добавлении завершенной работы: {str(e)}")
    
#______________________________________________________________________________________________________________________
async def orm_get_user_completed_works(session: AsyncSession, user_id: int):
    """
    Получает список завершенных работ пользователя.
    """
    try:
        query = select(CompletedWorks).where(CompletedWorks.user_id == user_id).options(selectinload(CompletedWorks.work))
        result = await session.execute(query)
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise ValueError(f"Ошибка при получении завершенных работ: {str(e)}")
    