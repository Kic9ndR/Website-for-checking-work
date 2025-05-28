import datetime
from sqlalchemy import DateTime, ForeignKey, String, func, Table, Column, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from typing import List, Optional

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(255), nullable=True) # Пароль может быть NULL для приглашенных
    full_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True) # Имя может быть не задано до завершения регистрации
    position: Mapped[str] = mapped_column(String(50), default="Ученик")  # Роль пользователя: Ученик, Проверяющий, Мастер 3D
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invitation_token: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True) # Токен для приглашения
    token_expires_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True) # Время истечения токена
    
    # Связь с текущим проектом
    current_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("works.id"), nullable=True)
    current_project: Mapped[Optional["Work"]] = relationship("Work", foreign_keys=[current_project_id])
    
    # Связь с завершенными работами
    completed_works: Mapped[List["CompletedWorks"]] = relationship(
        "CompletedWorks",
        back_populates="user"
    )
    # Связь с работами, которые пользователь проверяет
    inspected_works: Mapped[List["Work"]] = relationship(
        "Work",
        back_populates="inspector_user",
        foreign_keys="[Work.inspector]"
    )

    @property
    def is_authenticated(self):
        return True


class Work(Base):
    __tablename__ = "works"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(unique=True, nullable=False)
    work_link: Mapped[str] = mapped_column(unique=False, nullable=True)
    booklet: Mapped[str] = mapped_column(unique=False, nullable=True)
    inspector: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)  # Связь с пользователем, который проверяет работу
    corrections: Mapped[str] = mapped_column(unique=False, nullable=True)
    assigned_to: Mapped[bool] = mapped_column(unique=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Связь с завершенными работами
    completed_by_users: Mapped[list["CompletedWorks"]] = relationship(
        "CompletedWorks",
        back_populates="work"
    )
    # Связь с пользователем, который проверяет работу
    inspector_user: Mapped["User"] = relationship(
        "User",
        back_populates="inspected_works",
        foreign_keys=[inspector]
    )


class CompletedWorks(Base):
    __tablename__ = "completed_works"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(nullable=False, unique=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # Связь с пользователем
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"))  # Связь с работой
    completed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # Дата завершения

    # Связь с пользователем
    user: Mapped["User"] = relationship("User", back_populates="completed_works")

    # Связь с работой
    work: Mapped["Work"] = relationship("Work", back_populates="completed_by_users")


# Таблица для связи пользователей и завершенных проектов
user_completed_projects = Table(
    "user_completed_projects",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("work_id", Integer, ForeignKey("works.id")),
)


# 1	Сигнальная 12	https://yandex.ru	'-		"'-"	0	2024-09-13 11:11:01
# 2	Сигнальная 16	https://yandex.ru	'-		"'-"	0	2024-12-13 08:12:01
# 3	Сигнальная 20	https://yandex.ru	'-		"'-"	0	2024-10-13 21:01:01
# 4	Сигнальная 10	https://yandex.ru	'-		"'-"	0	2025-01-13 10:21:01

# 1	admin	$2b$12$XOyRmRjy5/gq4/z9tfst1evOLWOOrMvxc39/5zLAGvDtB8c6droIi	Никита	'-	2025-03-13 12:39:43
# 2	admin2	$2b$12$JyhKg0GshnJ6.IQT2tUDOusajjtoldtZht3xs/kd6omAT8IQ5OcGK	Никита (Двойник)	'-	2025-03-13 12:48:24