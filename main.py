import sys
import asyncio
import subprocess
import os
import json
import tempfile
import io
import tempfile
import uvicorn
import shutil
import json
import time
import logging
import asyncio
import aiofiles
import uuid
import aiosmtplib
import datetime as dt
from datetime import timedelta
import traceback

from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, List, Optional
from fastapi import FastAPI, File, Request, Form, Depends, HTTPException, UploadFile, status, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from authx.exceptions import MissingTokenError, JWTDecodeError
from urllib.parse import quote, unquote
from fastapi.security import APIKeyHeader
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, func
from email_validator import validate_email, EmailNotValidError
from email.message import EmailMessage

from auth import get_db, security, get_current_user, require_role
from crud import create_user, verify_password, get_all_users, get_user_by_id, update_user_position, get_user_by_login, pwd_context
from templates import *
from database.orm_query import *
from database.engine import get_session, session_maker
from database.engine import create_db_and_tables
from common.schemas import UserRegister
from services.blender_service import BlenderService
from utils.filters import datetimeformat
from database.models import User, Work, CompletedWorks
from services.fbx_checker import FBXChecker

# Инициализация шаблонов с добавлением фильтра
templates = Jinja2Templates(directory="templates")
templates.env.filters["datetimeformat"] = datetimeformat

# Создание директории для загрузок, если её нет
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# # Настройки Email
# SMTP_HOSTNAME = os.getenv("SMTP_HOSTNAME") # "smtp.gmail.com"
# SMTP_PORT = os.getenv(int("SMTP_PORT")) # 587
# SMTP_USERNAME = os.getenv("SMTP_USERNAME") # "your_email@example.com"
# SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") # "your_password"
# SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME) # Адрес отправителя

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

# Добавление middleware для сессий
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "a_very_secret_key_for_session"), # Используйте переменную окружения
    session_cookie="session_cookie",
    max_age=86400  # Время жизни сессии в секундах (24 часа)
)

# Обработчик исключения для отсутствующего токена
@app.exception_handler(MissingTokenError)
async def missing_token_exception_handler(request: Request, exc: MissingTokenError):
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

# Обработчик исключения для истекшего токена
@app.exception_handler(JWTDecodeError)
async def jwt_decode_exception_handler(request: Request, exc: JWTDecodeError):
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

# Монтирование статических файлов
app.mount("/static", StaticFiles(directory="static"), name="static")
api_key_header = APIKeyHeader(name="X-API-Key")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

#####################################################################################################################
# Модель для валидации входящих данных
class TelegramMessage(BaseModel):
    title: str
    work_link: str
    booklet: str

#____________________________________________________________________________________________________________________
@app.post("/api/telegram/message")
async def save_telegram_message(
    message: TelegramMessage,
    session: AsyncSession = Depends(get_session)
):
    try:
        # Создаем запись в базе данных
        await orm_add_work(
            session, 
            message.title, 
            message.work_link,
            message.booklet,
        )
        return {"status": "Сообщение сохранено"}
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


#####################################################################################################################
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def main_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)  # Защита маршрута
):
    users = await orm_get_all_users(session)
    works = await orm_get_works(session)  # Получаем отсортированные работы
    return templates.TemplateResponse(
        "main.html",
        {
            "request": request,
            "users": users,
            "works": works,  # Передаем работы в шаблон
            "current_user": current_user
        }
    )

#####################################################################################################################
@app.get("/profile", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def profile(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    try:
        # Получаем текущую работу пользователя
        current_work = await orm_get_user_current_work(session, current_user.id)
        
        # Получаем завершенные проекты пользователя
        completed_works = await orm_get_user_completed_works(session, current_user.id)
        
        # Передаем данные в шаблон
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "current_user": current_user,
                "current_work": current_work,
                "completed_works": completed_works,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#####################################################################################################################
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "current_user": None})

#____________________________________________________________________________________________________________________
@app.post("/login")
async def auth(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    user = await orm_get_user_info(session, login)
    
    if user is None or not await verify_password(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный логин или пароль", "current_user": None},
            status_code=401
        )
    
    token = security.create_access_token(user.login)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=86400 # Устанавливаем 24 часа (24 * 60 * 60)
    )
    return response

#____________________________________________________________________________________________________________________
@app.get("/logout")
async def logout(request: Request):
    # Создаем ответ с перенаправлением на страницу входа
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    # Удаляем куки с токеном доступа
    response.delete_cookie("access_token")

    return response

#####################################################################################################################
@app.get("/works", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def works_form(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    works = await orm_get_works(session)
    
    # Получаем сообщение из cookies
    message = request.cookies.get("message")
    if message:
        message = unquote(message)  # Декодируем сообщение
    
    return templates.TemplateResponse(
        "works.html",
        {
            "request": request,
            "works": works,
            "current_user": current_user,
            "message": message  # Передаем сообщение в шаблон
        }
    )

#####################################################################################################################
@app.get("/works/upload_fbx", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def upload_fbx_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Отображает форму загрузки FBX файлов (GET) 
    """
    print("--- PRINT: Entered upload_fbx_page (GET) ---")
    # Проверяем, есть ли результат последней проверки в сессии
    last_result_path = request.session.get('last_check_result_path')
    if last_result_path and os.path.exists(last_result_path):
        print(f"PRINT: Found existing result path in session AND file exists, redirecting to results: {last_result_path}")
        return RedirectResponse(url=f"/works/check_results", status_code=status.HTTP_303_SEE_OTHER)
    elif last_result_path: # Если путь в сессии есть, но файла нет
        print(f"PRINT: Found stale result path in session, but file does not exist: {last_result_path}. Clearing session key.")
        del request.session['last_check_result_path']

    # Render the upload form if no redirect happened
    print("--- PRINT: Rendering upload_fbx_page ---")
    return templates.TemplateResponse(
        "upload.html",
        {"request": request, "current_user": current_user}
    )

# --------------------------- Новая функция для обработки POST-запроса ---------------------------------
@app.post("/works/upload_fbx", dependencies=[Depends(security.access_token_required)])
async def handle_upload_fbx(
    request: Request, # Восстанавливаем request
    file: UploadFile = File(...), # file после request
    current_user: User = Depends(get_current_user) # current_user после file
): 
    print("--- PRINT: Entered handle_upload_fbx (POST) --- ") # Заменено на print
    if not file or not file.filename.endswith('.zip'):
        return JSONResponse(
            status_code=400,
            content={"detail": "Только ZIP архивы разрешены"}
        )

    print("PRINT: Generating unique filename...") # Заменено на print
    unique_filename = f"result_{uuid.uuid4()}.json"
    result_json_path = UPLOAD_DIR / unique_filename
    temp_file_path = None

    print("PRINT: Attempting to create temp file...") # Заменено на print
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_file:
            print("PRINT: Reading file content...") # Заменено на print
            content = await file.read()
            print("PRINT: Writing content to temp file...") # Заменено на print
            temp_file.write(content)
            temp_file_path = temp_file.name
            print(f"PRINT: Saved uploaded file temporarily to: {temp_file_path}") # Заменено на print
    except Exception as e_readwrite:
        print(f"ERROR: Error reading/writing uploaded file: {e_readwrite}") # Заменено на print
        # traceback.print_exc() # Раскомментировать для детальной ошибки
        return HTMLResponse(content="<h1>Ошибка обработки файла</h1><p>Не удалось прочитать или сохранить загруженный файл.</p>", status_code=500)

    print(f"PRINT: Expecting result file at: {result_json_path}") # Заменено на print

    loop = asyncio.get_running_loop()
    print(f"PRINT: About to call run_in_executor for {temp_file_path}") # Заменено на print
    try:
        await loop.run_in_executor(
            None,
            run_blender_check_docker_sync,
            temp_file_path,
            str(result_json_path)
        )
        print(f"PRINT: run_in_executor finished successfully for {temp_file_path}") # Заменено на print
    except Exception as executor_error:
        print(f"ERROR: ERROR during run_in_executor: {executor_error}") # Заменено на print
        # traceback.print_exc() # Раскомментировать для детальной ошибки
        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path); print(f"PRINT: Cleaned up temp archive on executor error: {temp_file_path}") # Заменено на print
            except: pass
        return HTMLResponse(content=f"<h1>Ошибка выполнения проверки</h1><p>{executor_error}</p>", status_code=500)

    file_exists = os.path.exists(result_json_path)
    print(f"PRINT: Checking existence right after executor: {result_json_path} - Exists: {file_exists}") # Заменено на print
    if not file_exists:
        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path); print(f"PRINT: Cleaned up temp archive (result not found): {temp_file_path}") # Заменено на print
            except: pass
        print(f"ERROR: Result file NOT FOUND at {result_json_path} after executor finished without error.") # Заменено на print 
        return HTMLResponse(content="<h1>Ошибка проверки</h1><p>Файл результата не был создан, хотя проверка завершилась без явной ошибки.</p>", status_code=500)

    # Проверяем содержимое файла результатов
    try:
        with open(result_json_path, 'r', encoding='utf-8') as f:
            results = json.load(f)
        print(f"PRINT: Successfully read results file. Content keys: {results.keys()}") # Добавлено логирование
        print(f"PRINT: Content of results['error'] if exists: {results.get('error', 'Key \"error\" not found')}") # Добавлено логирование

        # Проверяем наличие ошибки о отсутствии FBX файлов
        if "error" in results and "В данном архиве нет FBX файлов" in results["error"]:
            # Удаляем временные файлы
            if temp_file_path and os.path.exists(temp_file_path):
                try: os.remove(temp_file_path)
                except: pass
            if os.path.exists(result_json_path):
                try: os.remove(result_json_path)
                except: pass
            # Возвращаем сообщение об ошибке
            return HTMLResponse(
                content="<h1>Ошибка проверки</h1><p>В данном архиве нет FBX файлов. Загрузите другой архив</p>",
                status_code=400
            )
    except Exception as e:
        print(f"ERROR: Error reading results file: {e}")
        return HTMLResponse(content="<h1>Ошибка чтения результатов</h1><p>Не удалось прочитать результаты проверки.</p>", status_code=500)

    request.session['last_check_result_path'] = str(result_json_path)
    print(f"PRINT: Saved result path to session: {result_json_path}") # Заменено на print
    print(f"PRINT: Session content after save: {request.session}") # Заменено на print

    print("PRINT: Preparing to redirect...") # Заменено на print
    return RedirectResponse(url=f"/works/check_results", status_code=status.HTTP_303_SEE_OTHER)

# ----------------------------- Синхронная функция для Docker -----------------------------
def run_blender_check_docker_sync(input_zip_path, output_json_path):
    input_zip_path = os.path.abspath(input_zip_path)
    output_json_path = os.path.abspath(output_json_path)
    print(f"PRINT [Sync Func] Starting check for Input: {input_zip_path}, Output: {output_json_path}") # Заменено на print
    # Используем input_dir для входного файла
    input_dir = os.path.dirname(input_zip_path) 
    # Используем UPLOAD_DIR для выходного JSON
    output_dir = os.path.abspath(UPLOAD_DIR)
    docker_image = "blender-docker_blender"
    # Пути внутри контейнера: /input для архива, /output для JSON
    container_input = f"/input/{os.path.basename(input_zip_path)}"
    container_output = f"/output/{os.path.basename(output_json_path)}"
    checker_script = "/app/addons/model_checker.py"
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{input_dir}:/input", # Монтируем папку с архивом
        "-v", f"{output_dir}:/output", # Монтируем папку uploads для вывода
        docker_image,
        "blender", "--background", "--python", checker_script, "--",
        container_input, container_output
    ]
    print(f"PRINT: Running Docker command: {' '.join(cmd)}") # Заменено на print
    try:
        # Добавляем таймаут (например, 5 минут = 300 секунд)
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    except subprocess.TimeoutExpired:
        print("ERROR: Docker command timed out after 300 seconds.") # Заменено на print
        raise RuntimeError("Проверка модели заняла слишком много времени.")
    if result.stdout:
        print(f"PRINT: Docker stdout:\n{result.stdout}") # Заменено на print
    if result.stderr:
        print(f"WARNING: Docker stderr:\n{result.stderr}") # Заменено на print

    # --- ДОБАВЛЕНО ЛОГИРОВАНИЕ --- 
    print(f"PRINT [Sync Func] Docker command finished with code {result.returncode}") # Заменено на print

    if result.returncode != 0:
        error_message = f"Docker command failed with code {result.returncode}. Stderr: {result.stderr}"
        print(f"ERROR: {error_message}") # Заменено на print
        # Логируем stdout/stderr при ошибке
        print(f"PRINT: Docker stdout (error case):\n{result.stdout}") # Заменено на print
        print(f"WARNING: Docker stderr (error case):\n{result.stderr}") # Заменено на print
        raise RuntimeError(error_message) # Caught by check_fbx_docker's except
    
    # Проверяем существование файла ПЕРЕД возвратом
    print(f"PRINT [Sync Func] Checking existence BEFORE return: {output_json_path}") # Заменено на print
    # ---> THIS CHECK <--- 
    if not os.path.exists(output_json_path):
        error_message = f"Output JSON file not found after Docker execution: {output_json_path}. Docker stdout: {result.stdout or '[empty]'}. Docker stderr: {result.stderr or '[empty]'}"
        # Логируем stdout/stderr даже если returncode был 0, но файла нет
        print(f"PRINT: Docker stdout (file not found case): {result.stdout}") # Заменено на print
        print(f"WARNING: Docker stderr (file not found case): {result.stderr}") # Заменено на print
        print(f"ERROR: {error_message}") # Заменено на print
        raise FileNotFoundError(error_message) # Caught by check_fbx_docker's except

    print(f"PRINT: Successfully created result file: {output_json_path}") # Заменено на print
    return output_json_path
# --- Конец синхронной функции ---

###################################################################################################
# --- Новый GET эндпоинт для отображения результатов --- 
@app.get("/works/check_results", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def show_check_results(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    result_json_path = request.session.get('last_check_result_path')
    if not result_json_path:
        print("WARNING: Result path not found in session") # Заменено на print
        return HTMLResponse(content="<h1>Результаты не найдены</h1><p>Проверка не была завершена или результаты не сохранены.</p>", status_code=404)

    try:
        # Проверяем существование файла результатов
        if not os.path.exists(result_json_path):
            print(f"WARNING: Result file not found: {result_json_path}") # Заменено на print
            return HTMLResponse(content="<h1>Результаты не найдены</h1><p>Файл результатов не найден.</p>", status_code=404)

        # Читаем результаты из файла
        print(f"PRINT: Reading results from: {result_json_path}") # Заменено на print
        with open(result_json_path, 'r') as f:
            results = json.load(f)

        # Удаляем файл и ключ сессии после успешного чтения (как было задумано)
        # А также удаляем папку textures
        try:
            os.remove(result_json_path)
            print(f"PRINT: Removed result JSON file after reading: {result_json_path}") # Заменено на print
            if 'last_check_result_path' in request.session:
                del request.session['last_check_result_path']
                print("PRINT: Removed result path from session.") # Заменено на print

            # Удаляем папку uploads/textures
            textures_dir_path = UPLOAD_DIR / "textures"
            print(f"PRINT: Checking for textures directory: {textures_dir_path}") # Заменено на print
            if os.path.exists(textures_dir_path) and os.path.isdir(textures_dir_path):
                print(f"PRINT: Attempting to remove textures directory: {textures_dir_path}") # Заменено на print
                try:
                    shutil.rmtree(textures_dir_path)
                    print(f"PRINT: Successfully removed textures directory: {textures_dir_path}") # Заменено на print
                except Exception as e_rmdir:
                    print(f"ERROR: Could not remove textures directory {textures_dir_path}: {e_rmdir}") # Заменено на print
            else:
                 print(f"PRINT: Textures directory not found or not a directory: {textures_dir_path}") # Заменено на print

        except OSError as e_remove:
            print(f"WARNING: Could not remove result JSON file {result_json_path}: {e_remove}") # Заменено на print

        # Отображаем результаты
        return templates.TemplateResponse(
            "check_results.html",
            {
                "request": request,
                "results": results,
                "current_user": current_user,
            }
        )
    except Exception as e:
        print(f"ERROR: Error reading results: {e}")
        return HTMLResponse(content="<h1>Ошибка чтения результатов</h1><p>Не удалось прочитать результаты.</p>", status_code=500)


# ------------------- Эндпоинты для создания новой работы -------------------
@app.get("/works/new", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def new_work_form(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "create_work.html",
        {"request": request, "current_user": current_user}
    )

@app.post("/works/new", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def create_new_work(
    request: Request,
    title: str = Form(...),
    work_link: str = Form(""),
    booklet: str = Form(""),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        await orm_add_work(session, title, work_link, booklet)
        return RedirectResponse(url="/works", status_code=303)
    except Exception as e:
        return templates.TemplateResponse(
            "create_work.html",
            {"request": request, "current_user": current_user, "error": str(e)},
            status_code=400
        )

# ------------------- Существующий эндпоинт для просмотра работы -------------------
@app.get("/works/{work_id}", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def works_list(
    work_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    work = await orm_get_work(session, work_id)
    if not work:
        raise HTTPException(status_code=404, detail="Work not found")
    return templates.TemplateResponse(
        "work_id.html", 
            {
            "request": request, 
            "work": work, 
            "current_user": current_user,
            }
        )

#____________________________________________________________________________________________________________________
@app.get("/works/{work_id}/decline", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def decline_work(
    work_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        work = await orm_get_work(session, work_id)
        if not work:
            raise HTTPException(status_code=404, detail="Работа не найдена")

        # Проверяем, что текущий пользователь является назначенным на работу
        if work.inspector != current_user.id:
            print(current_user.id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Работу проверяет другой сотрудник"
            )
        
        # Удаляем запись из таблицы user_completed_projects
        await orm_remove_user_completed_project(session, current_user.id, work_id)

        # Сбрасываем assigned_to в False
        await orm_assigned_to(session, work_id, False)

        # Удаляем проверяющего с работы
        await orm_add_inspector(session, work_id, None)

        # Перенаправляем на страницу работы
        return RedirectResponse(url=f"/works/{work_id}", status_code=303)

    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

#______________________GET ЗАПРОС__________________________________________________________________________________________
@app.get("/works/{work_id}/take", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def take_work_form(
    work_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # Получаем работу из БД
        work = await orm_get_work(session, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Работа не найдена"
            )

        # Устанавливаем assigned_to в True
        await orm_assigned_to(session, work_id, True)

        # Добавляем проверяющего на работу
        await orm_add_inspector(session, work_id, current_user.id)

        # Отображаем страницу для загрузки скриншотов
        return templates.TemplateResponse(
            "take_work.html",
            {
                "request": request,
                "work": work,
                "current_user": current_user,
            }
        )

    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка базы данных: {str(e)}"
        )
    
#____________________________POST ЗАПРОС____________________________________________________________________________________
@app.post("/works/{work_id}/take", response_class=HTMLResponse, dependencies=[Depends(security.access_token_required)])
async def take_work(
    work_id: int,
    request: Request,
    screenshots: list[UploadFile] = File(...),
    comments: list[str] = Form([]),  # Комментарии
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        work = await orm_get_work(session, work_id)
        if not work:
            raise HTTPException(status_code=404, detail="Работа не найдена")

        if work.assigned_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Работа уже назначена другому пользователю"
            )

        # Сохраняем файлы и комментарии
        for i, (screenshot, comment) in enumerate(zip(screenshots, comments), start=1):
            file_path = f"uploads/work_{work_id}_user_{current_user.id}_{i}.png"
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(screenshot.file, buffer)

            # Сохраняем комментарий
            print(f"Скриншот {i}: {comment}")  # Пример обработки комментария


    except MissingTokenError as e:
        # Обработка исключения MissingTokenError
        RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        # Обработка всех остальных исключений
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Произошла ошибка: {str(e)}"
        )

###################################################################################################
# Маршруты для приглашения сотрудников - ПЕРЕМЕЩЕНО ВЫШЕ ДЛЯ ПРАВИЛЬНОГО МАТЧИНГА
###################################################################################################

@app.get("/employees/invite", response_class=HTMLResponse)
@require_role("Мастер 3D") # Только Мастер может приглашать
async def invite_employee_form(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    # Проверяем, является ли текущий пользователь объектом User (а не RedirectResponse)
    if not isinstance(current_user, User):
        return current_user # Возвращаем редирект, если пользователь не авторизован
        
    return templates.TemplateResponse(
        "register.html", # Используем существующий шаблон для формы
        {
            "request": request,
            "current_user": current_user,
            "error": None # Передаем None, чтобы при первом показе не было ошибки
        }
    )

@app.post("/employees/invite")
@require_role("Мастер 3D") # Только Мастер может приглашать
async def invite_employee_submit(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    email: str = Form(...)
):
    # Проверяем авторизацию Мастера
    if not isinstance(current_user, User):
        return current_user
    
    error_message = None
    # Валидация email
    try:
        validation = validate_email(email, check_deliverability=False) # Проверка доставки может быть долгой
        email = validation.normalized
    except EmailNotValidError as e:
        error_message = f"Некорректный email: {e}"
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "current_user": current_user, "error": error_message},
            status_code=400
        )

    # Проверка, не существует ли уже пользователь с таким email
    existing_user = await get_user_by_login(session, email)
    if existing_user:
        error_message = "Пользователь с таким email уже существует или приглашен."
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "current_user": current_user, "error": error_message},
            status_code=400
        )

    # Генерация токена и времени истечения
    token = uuid.uuid4().hex
    # Используем aware datetime с dt
    expires_at = dt.datetime.now(dt.timezone.utc) + timedelta(hours=48)

    # Создание пользователя в БД
    new_user = User(
        login=email,
        invitation_token=token,
        token_expires_at=expires_at,
        position="Ученик" # По умолчанию - Ученик
        # password и full_name остаются None
    )
    session.add(new_user)
    try:
        await session.commit()
        await session.refresh(new_user)
    except IntegrityError: # На случай очень редкой коллизии токенов
        await session.rollback()
        error_message = "Произошла ошибка при создании приглашения. Попробуйте еще раз."
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "current_user": current_user, "error": error_message},
            status_code=500
        )
    except Exception as e:
        await session.rollback()
        logging.error(f"Error creating invitation for {email}: {e}")
        error_message = "Внутренняя ошибка сервера при создании приглашения."
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "current_user": current_user, "error": error_message},
            status_code=500
        )

    # Отправка email
    email_sent = await send_invitation_email(email, token, request)

    if not email_sent:
        # Ошибка отправки email (можно добавить логику отката создания пользователя или попытки повторной отправки)
        # Пока просто сообщаем Мастеру
        response = RedirectResponse(url="/employees", status_code=303) # Редирект на список сотрудников
        response.set_cookie("message", f"Приглашение для {email} создано, но не удалось отправить email.".encode("utf-8"))
        return response

    # Успешная отправка
    response = RedirectResponse(url="/employees", status_code=303) # Редирект на список сотрудников
    response.set_cookie("message", f"Приглашение успешно отправлено на {email}".encode("utf-8"))
    return response

# --- Старые маршруты /employees --- 

###################################################################################################
@app.get("/employees", response_class=HTMLResponse)
@require_role("Проверяющий") # Указана роль
async def get_employees_list(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    async with session_maker() as session:
        users = await get_all_users(session)
        return templates.TemplateResponse(
            "employees.html",
            {
                "request": request,
                "users": users,
                "current_user": current_user
            }
        )

#____________________________________________________________________________________________________________
@app.get("/employees/{user_id}", response_class=HTMLResponse)
@require_role("Мастер 3D") # Указана роль
async def get_employee_details(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user)
):
    async with session_maker() as session:
        # Получаем основную информацию о пользователе
        user = await get_user_by_id(session, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        
        # Получаем количество завершенных работ
        completed_works_count = await session.execute(
            select(func.count()).select_from(CompletedWorks).where(CompletedWorks.user_id == user_id)
        )
        completed_works_count = completed_works_count.scalar() or 0
        
        # Получаем количество проверяемых работ
        inspected_works_count = await session.execute(
            select(func.count()).select_from(Work).where(Work.inspector == user_id)
        )
        inspected_works_count = inspected_works_count.scalar() or 0
        
        # Получаем текущий проект
        current_project = await session.execute(
            select(Work).where(Work.id == user.current_project_id)
        )
        current_project = current_project.scalar()
        current_project_title = current_project.title if current_project else "-"
        
        return templates.TemplateResponse(
            "employee_details.html",
            {
                "request": request,
                "employee": user,
                "current_user": current_user,
                "completed_works_count": completed_works_count,
                "inspected_works_count": inspected_works_count,
                "current_project_title": current_project_title
            }
        )

#____________________________________________________________________________________________________________
@app.post("/employees/{user_id}/update-position")
@require_role("Мастер 3D") # Указана роль
async def update_employee_position(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_user)
):
    try:
        data = await request.json()
        new_position = data.get("new_position")
        
        if not new_position or new_position not in ["Ученик", "Проверяющий", "Мастер 3D"]:
            raise HTTPException(status_code=400, detail="Недопустимая роль")
        
        async with session_maker() as session:
            updated_user = await update_user_position(session, user_id, new_position)
            if not updated_user:
                raise HTTPException(status_code=404, detail="Сотрудник не найден")
            
            return {"message": "Должность успешно обновлена"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

###################################################################################################
async def get_work(db: AsyncSession, work_id: int) -> Optional[Work]:
    """Получить работу по ID из базы данных"""
    result = await db.execute(
        select(Work).where(Work.id == work_id)
    )
    return result.scalar_one_or_none()


###################################################################################################
# Маршруты для установки пароля по приглашению
###################################################################################################

@app.get("/set-password", response_class=HTMLResponse)
async def set_password_form(
    request: Request,
    token: str = Query(...), # Получаем токен из URL
    session: AsyncSession = Depends(get_db)
):
    error_message = None
    # Ищем пользователя по токену
    user = await session.scalar(
        select(User).where(User.invitation_token == token)
    )

    # Проверяем токен
    if not user:
        error_message = "Ссылка для установки пароля недействительна или устарела (пользователь не найден)."
    # Используем dt для сравнения
    elif user.token_expires_at is None or user.token_expires_at < dt.datetime.now(dt.timezone.utc):
        error_message = "Срок действия ссылки для установки пароля истек."
    elif user.password is not None: # Если пароль уже установлен
        error_message = "Пароль для этого пользователя уже установлен. Воспользуйтесь формой входа."

    if error_message:
        # Можно сделать отдельный шаблон для ошибок или редирект на логин с сообщением
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error": error_message},
            status_code=400
        )

    # Токен валиден, показываем форму
    return templates.TemplateResponse(
        "set_password.html", 
        {"request": request, "token": token}
    )

@app.post("/set-password", response_class=HTMLResponse)
async def set_password_submit(
    request: Request,
    session: AsyncSession = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...) # Добавлено поле подтверждения
):
    error_message = None
    
    # Проверка совпадения паролей
    if password != confirm_password:
        error_message = "Пароли не совпадают."
        # Показываем форму снова с ошибкой
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "token": token, "error": error_message},
            status_code=400
        )
        
    # Валидация длины пароля (можно добавить сложность)
    if len(password) < 8:
        error_message = "Пароль должен быть не менее 8 символов."
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "token": token, "error": error_message},
            status_code=400
        )

    # Ищем пользователя по токену снова
    user = await session.scalar(
        select(User).where(User.invitation_token == token)
    )

    # Проверяем токен снова с dt
    if not user or user.token_expires_at is None or user.token_expires_at < dt.datetime.now(dt.timezone.utc) or user.password is not None:
        # Если токен стал невалидным между GET и POST
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error": "Ссылка стала недействительной. Пожалуйста, запросите приглашение снова."},
            status_code=400
        )

    # Хешируем пароль
    hashed_password = pwd_context.hash(password)

    # Обновляем пользователя
    user.password = hashed_password
    user.invitation_token = None
    user.token_expires_at = None
    # Можно добавить запрос полного имени на этой же форме или оставить на потом
    # user.full_name = ??? 
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        logging.error(f"Error setting password for token {token}: {e}")
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "token": token, "error": "Не удалось сохранить пароль. Попробуйте еще раз."},
            status_code=500
        )

    # Успех, редирект на логин
    response = RedirectResponse(url="/login", status_code=303)
    response.set_cookie("message", "Пароль успешно установлен! Теперь вы можете войти.".encode("utf-8"))
    return response

###################################################################################################
# Функция отправки email
async def send_invitation_email(recipient_email: str, token: str, request: Request):
    # Проверяем наличие всех необходимых настроек SMTP
    if not all([SMTP_HOSTNAME, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL]):
        logging.error("SMTP settings are not fully configured. Please check environment variables.")
        return False
        
    # ... (код формирования ссылки и сообщения) ...
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    confirmation_url = f"{base_url}/set-password?token={token}"
    
    subject = "Приглашение на регистрацию"
    body = f"""
    Здравствуйте!

    Вас пригласили зарегистрироваться на платформе Проверки Работ.
    Пожалуйста, перейдите по ссылке ниже, чтобы создать пароль и завершить регистрацию:
    {confirmation_url}

    Ссылка действительна в течение 48 часов.

    С уважением,
    Команда Платформы
    """

    message = EmailMessage()
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        logging.info(f"Attempting to send email to {recipient_email} via {SMTP_HOSTNAME}:{SMTP_PORT}")
        await aiosmtplib.send(
            message,
            hostname=SMTP_HOSTNAME,
            port=SMTP_PORT,
            username=SMTP_USERNAME,
            password=SMTP_PASSWORD,
            use_tls=True # Обычно требуется TLS
        )
        logging.info(f"Invitation email sent successfully to {recipient_email}")
        return True
    except Exception as e:
        # Логируем полную ошибку с трассировкой
        logging.error(f"Failed to send invitation email to {recipient_email}: {e}")
        logging.error(traceback.format_exc()) # Добавляем трассировку в лог
        return False

###################################################################################################
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

    # uvicorn main:app --reload
