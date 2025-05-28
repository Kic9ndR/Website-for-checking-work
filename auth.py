import os
from datetime import timedelta
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from authx import AuthX, AuthXConfig
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User
from crud import get_user_by_login
from secrets import compare_digest
from functools import wraps
from typing import Callable

from database.engine import session_maker


config = AuthXConfig()
config.JWT_SECRET_KEY = os.getenv("SECRET_KEY")
config.JWT_ACCESS_COOKIE_NAME = "access_token"
config.JWT_TOKEN_LOCATION = ["cookies"]
config.JWT_ALGORITHM = "HS256"  # Добавить алгоритм
config.JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)


config.JWT_COOKIE_CSRF_PROTECT = False  # Отключаем CSRF

security = AuthX(config=config)

async def get_db():
    async with session_maker() as session:
        yield session


def decode_access_token(token: str):
    try:
        payload = jwt.decode(
            token,
            os.getenv("SECRET_KEY"),
            algorithms=["HS256"]
        )
        return payload.get("sub")
    except JWTError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e
    

def get_token_from_request(request: Request):
    # Проверяем cookies
    token = request.cookies.get("access_token")
    if token:
        return token
    
    # Проверяем заголовки
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    
    # Если токен не найден
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    token = request.cookies.get("access_token")
    if not token:
        # Перенаправляем на страницу входа с сообщением
        response = RedirectResponse(url="/login", status_code=303)
        response.set_cookie("message", "Для начала работы необходимо авторизоваться".encode("utf-8"))
        return response
    
    try:
        user_id = decode_access_token(token)
    except JWTError as e:
        # Перенаправляем на страницу входа с сообщением
        response = RedirectResponse(url="/login", status_code=303)
        response.set_cookie("message", "Недействительный токен. Пожалуйста, войдите снова.".encode("utf-8"))
        return response
    
    user = await get_user_by_login(db, user_id)
    if not user:
        # Перенаправляем на страницу входа с сообщением
        response = RedirectResponse(url="/login", status_code=303)
        response.set_cookie("message", "Пользователь не найден. Пожалуйста, войдите снова.".encode("utf-8"))
        return response
    
    return user

async def validate_csrf_token(token: str, request: Request) -> bool:
    session_token = request.session.get("csrf_token")
    return compare_digest(token, session_token)

def require_role(role: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_user = kwargs.get('current_user')
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Требуется авторизация"
                )
            
            if role == "Ученик":
                if current_user.position not in ["Ученик", "Проверяющий", "Мастер 3D"]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Недостаточно прав"
                    )
            elif role == "Проверяющий":
                if current_user.position not in ["Проверяющий", "Мастер 3D"]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Недостаточно прав"
                    )
            elif role == "Мастер 3D":
                if current_user.position != "Мастер 3D":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Недостаточно прав"
                    )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

def require_master_role(func):
    return require_role("Мастер 3D")(func)

def require_inspector_role(func):
    return require_role("Проверяющий")(func)

def require_student_role(func):
    return require_role("Ученик")(func)