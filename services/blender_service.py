import os
import subprocess
import json
from typing import Dict, Any
import tempfile
import shutil
import time
from contextlib import contextmanager
import logging
from pathlib import Path
import uuid # Для уникальных имен JSON
import asyncio
import aiofiles

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BlenderService:
    def __init__(self):
        self.docker_image = "blender-docker-blender:latest"
        # Путь к скрипту внутри контейнера
        self.script_path_in_container = "/app/check_model.sh" 
        # Абсолютный путь к директории blender-docker на хосте
        self.blender_docker_dir_host = Path(__file__).parent.parent / "blender-docker"
        # Путь к addons внутри контейнера (монтируется из blender-docker/addons)
        self.addons_path_in_container = "/app/addons"
        # Путь к скрипту model_checker.py внутри контейнера
        # self.checker_script_path_in_container = f"{self.addons_path_in_container}/model_checker.py" # Не используется напрямую, вызывается через sh
        
        # Директория на хосте для временных JSON результатов и логов Docker
        self.host_output_dir = self.blender_docker_dir_host / "output2"
        self.host_output_dir.mkdir(exist_ok=True) # Убедимся, что директория существует на хосте
        # Путь к этой директории внутри контейнера
        self.container_output_dir = "/output"

    @contextmanager
    def temp_file(self, suffix: str):
        """Контекстный менеджер для работы с временными файлами"""
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            yield temp
        finally:
            temp.close()
            self._safe_remove(temp.name)

    def _safe_remove(self, file_path: str, max_attempts: int = 3, delay: float = 2.0):
        """Безопасное удаление файла с несколькими попытками"""
        if not file_path or not os.path.exists(file_path):
            return

        for attempt in range(max_attempts):
            try:
                os.unlink(file_path)
                return
            except Exception as e:
                if attempt < max_attempts - 1:
                    logger.warning(f"Попытка {attempt + 1}/{max_attempts}: не удалось удалить {file_path}: {str(e)}")
                    time.sleep(delay)
                else:
                    logger.warning(f"Не удалось удалить файл {file_path} после {max_attempts} попыток")

    async def check_model(self, host_input_path_str: str) -> Dict[str, Any]:
        """
        Запускает проверку модели (FBX или ZIP) через Docker.
        
        Args:
            host_input_path_str: Абсолютный путь к входному файлу (FBX или ZIP) на хост-машине.

        Returns:
            Словарь с результатами проверки.
            
        Raises:
            Exception: Если произошла ошибка во время выполнения.
        """
        host_input_path = Path(host_input_path_str).resolve() # Убедимся, что путь абсолютный
        input_filename = host_input_path.name
        host_input_dir = host_input_path.parent
        
        # Уникальное имя для JSON файла результатов на хосте
        unique_id = uuid.uuid4()
        host_json_output_filename = f"results_{unique_id}.json"
        host_json_output_path = self.host_output_dir / host_json_output_filename
        # Путь к этому JSON файлу внутри контейнера
        container_json_output_path = f"{self.container_output_dir}/{host_json_output_filename}"
        
        # Путь к лог-файлу Docker на хосте
        host_docker_log_filename = f"docker_run_{unique_id}.log"
        host_docker_log_path = self.host_output_dir / host_docker_log_filename
        # Путь к лог-файлу Blender внутри контейнера (пишется скриптом check_model.sh)
        container_blender_log_path = f"{self.container_output_dir}/blender_checker.log"

        # Путь к входному файлу внутри контейнера
        container_input_dir = "/data"
        container_input_path = f"{container_input_dir}/{input_filename}"
        
        # Путь к директории addons на хосте
        host_addons_dir = self.blender_docker_dir_host / "addons"

        try:
            logger.info(f"Начало проверки файла: {host_input_path}")
            logger.info(f"Директория для вывода хоста: {self.host_output_dir}")
            logger.info(f"Файл JSON результатов (хост): {host_json_output_path}")
            logger.info(f"Файл логов Docker (хост): {host_docker_log_path}")
            logger.info(f"Монтируемая директория с входным файлом (хост): {host_input_dir}")
            logger.info(f"Монтируемая директория addons (хост): {host_addons_dir}")

            # Удаляем старые файлы результатов/логов с таким же ID (маловероятно, но на всякий случай)
            if host_json_output_path.exists(): host_json_output_path.unlink()
            if host_docker_log_path.exists(): host_docker_log_path.unlink()

            # Проверяем существование входного файла
            if not host_input_path.exists():
                raise FileNotFoundError(f"Входной файл не найден: {host_input_path}")
            if not host_addons_dir.exists():
                raise FileNotFoundError(f"Директория addons не найдена: {host_addons_dir}")

            # Проверяем существование Docker
            try:
                # Асинхронная проверка версии Docker
                docker_version_proc = await asyncio.create_subprocess_exec(
                    'docker', '--version',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await docker_version_proc.communicate()
                if docker_version_proc.returncode != 0:
                    raise Exception(f"Docker не найден или не отвечает: {stderr.decode()}")
                logger.info(f"Docker версия: {stdout.decode().strip()}")
            except FileNotFoundError as e:
                logger.error(f"Команда 'docker' не найдена. Проверьте переменную PATH. Ошибка: {e}")
                raise Exception(f"Ошибка при проверке Docker: Команда 'docker' не найдена. Проверьте переменную PATH.")
            except NotImplementedError as e:
                # fallback на синхронный вызов при отсутствии поддержки asyncio subprocess
                logger.warning(f"asyncio subprocess не поддерживается, осуществляю fallback на subprocess.run: {e}")
                try:
                    result = subprocess.run(['docker', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if result.returncode != 0:
                        raise Exception(f"Docker не найден или не отвечает: {result.stderr}")
                    logger.info(f"Docker версия: {result.stdout.strip()}")
                except FileNotFoundError as e2:
                    logger.error(f"Команда 'docker' не найдена при fallback. Ошибка: {e2}")
                    raise Exception(f"Ошибка при проверке Docker: Команда 'docker' не найдена. Проверьте переменную PATH.")
                except Exception as e2:
                    logger.error(f"Ошибка при fallback проверки Docker: {e2}", exc_info=True)
                    error_type_name = type(e2).__name__
                    error_str = str(e2)
                    detailed_error = f"{error_type_name}: {error_str}" if error_str else error_type_name
                    raise Exception(f"Ошибка при проверке Docker: {detailed_error}")
            except Exception as e:
                logger.error(f"Не удалось выполнить 'docker --version'. Ошибка: {e}", exc_info=True)
                error_type_name = type(e).__name__
                error_str = str(e)
                detailed_error = f"{error_type_name}: {error_str}" if error_str else error_type_name
                raise Exception(f"Ошибка при проверке Docker: {detailed_error}")

            # Создаем команду для Docker
            # Монтируем:
            # 1. Директорию с входным файлом -> /data (read-only)
            # 2. Директорию addons -> /app/addons (read-only)
            # 3. Директорию для вывода (JSON, логи) -> /output (read-write)
            docker_cmd = [
                "docker", "run", "--rm",
                "-v", f"{host_input_dir}:{container_input_dir}:ro",
                "-v", f"{host_addons_dir}:{self.addons_path_in_container}:ro",
                "-v", f"{self.host_output_dir}:{self.container_output_dir}:rw",
                self.docker_image,
                self.script_path_in_container, # Путь к check_model.sh внутри контейнера
                container_input_path,          # Путь к входному файлу внутри контейнера
                container_json_output_path     # Путь к выходному JSON внутри контейнера
            ]
            
            logger.info(f"Запуск Docker команды: {' '.join(docker_cmd)}")

            # Запускаем Docker контейнер асинхронно и пишем его вывод в лог
            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Асинхронно читаем stdout и stderr и пишем в лог-файл Docker
            async with aiofiles.open(host_docker_log_path, mode='w', encoding='utf-8') as log_file:
                async def log_stream(stream, prefix):
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        decoded_line = line.decode('utf-8', errors='replace').strip()
                        await log_file.write(f"{prefix}: {decoded_line}\n")
                        logger.debug(f"Docker {prefix}: {decoded_line}") # Опционально: дублируем в основной лог
                
                await asyncio.gather(
                    log_stream(process.stdout, "stdout"),
                    log_stream(process.stderr, "stderr")
                )

            # Ждем завершения процесса
            return_code = await process.wait()
            logger.info(f"Docker контейнер завершился с кодом {return_code}")

            # Логируем содержимое лога Blender из контейнера (если он есть)
            # Путь к логу Blender на хосте = self.host_output_dir / имя_файла_из_контейнера
            host_blender_log_path = self.host_output_dir / Path(container_blender_log_path).name
            if host_blender_log_path.exists():
                try:
                    async with aiofiles.open(host_blender_log_path, 'r', encoding='utf-8') as bf:
                        blender_log_content = await bf.read()
                    logger.info(f"--- Содержимое лога Blender ({host_blender_log_path}) ---")
                    logger.info(blender_log_content)
                    logger.info("--- Конец лога Blender ---")
                except Exception as e_log:
                    logger.warning(f"Не удалось прочитать лог Blender ({host_blender_log_path}): {e_log}")
            else:
                logger.warning(f"Лог Blender не найден по пути: {host_blender_log_path}")

            # Проверяем код возврата Docker
            if return_code != 0:
                 # Читаем лог Docker для деталей ошибки
                 async with aiofiles.open(host_docker_log_path, 'r', encoding='utf-8') as lf:
                     docker_log_content = await lf.read()
                 raise Exception(f"Ошибка при выполнении Docker контейнера (код {return_code}). См. лог Docker: {host_docker_log_path}\n{docker_log_content}")

            # Проверяем существование файла результатов JSON на хосте
            if not host_json_output_path.exists():
                async with aiofiles.open(host_docker_log_path, 'r', encoding='utf-8') as lf:
                     docker_log_content = await lf.read()
                raise Exception(f"Файл результатов JSON не был создан: {host_json_output_path}. См. лог Docker: {host_docker_log_path}\n{docker_log_content}")

            # Читаем результаты из JSON
            try:
                async with aiofiles.open(host_json_output_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    results = json.loads(content)
                logger.info(f"Результаты успешно прочитаны из {host_json_output_path}")
                # logger.debug(f"Результаты проверки: {json.dumps(results, ensure_ascii=False, indent=2)}") # Опционально
                return results
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка декодирования JSON из файла {host_json_output_path}: {e}")
                # Пытаемся прочитать содержимое файла как текст
                try:
                     async with aiofiles.open(host_json_output_path, 'r', encoding='utf-8') as f_text:
                         raw_content = await f_text.read()
                     logger.error(f"Содержимое файла {host_json_output_path}:\n{raw_content}")
                except Exception as e_read_raw:
                     logger.error(f"Не удалось прочитать содержимое файла {host_json_output_path} как текст: {e_read_raw}")
                raise Exception(f"Ошибка чтения JSON результатов из {host_json_output_path}")
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при чтении JSON результатов: {e}", exc_info=True)
                raise Exception(f"Ошибка чтения JSON результатов из {host_json_output_path}")

        except Exception as e:
            logger.error(f"Ошибка в BlenderService.check_model для файла {host_input_path_str}: {str(e)}", exc_info=True)
            # Дополнительно логируем содержимое логов Docker/Blender, если они есть и ошибка не связана с их чтением
            if 'host_docker_log_path' in locals() and host_docker_log_path.exists():
                 try:
                      async with aiofiles.open(host_docker_log_path, 'r', encoding='utf-8') as lf_err:
                           docker_log_err = await lf_err.read()
                      logger.error(f"--- Лог Docker ({host_docker_log_path}) при ошибке ---\n{docker_log_err}")
                 except Exception as e_log_read_err:
                      logger.error(f"Не удалось прочитать лог Docker ({host_docker_log_path}) при обработке ошибки: {e_log_read_err}")
            
            if 'host_blender_log_path' in locals() and host_blender_log_path.exists():
                 try:
                      async with aiofiles.open(host_blender_log_path, 'r', encoding='utf-8') as bf_err:
                           blender_log_err = await bf_err.read()
                      logger.error(f"--- Лог Blender ({host_blender_log_path}) при ошибке ---\n{blender_log_err}")
                 except Exception as e_log_read_err:
                      logger.error(f"Не удалось прочитать лог Blender ({host_blender_log_path}) при обработке ошибки: {e_log_read_err}")
                      
            raise # Перевыбрасываем оригинальное исключение
            
        finally:
            # Опционально: удаляем JSON и лог Docker после успешного чтения?
            # Пока оставляем их в blender-docker/output для отладки.
            # if host_json_output_path.exists():
            #     try: host_json_output_path.unlink()
            #     except Exception as e: logger.warning(f"Не удалось удалить {host_json_output_path}: {e}")
            # if host_docker_log_path.exists():
            #     try: host_docker_log_path.unlink()
            #     except Exception as e: logger.warning(f"Не удалось удалить {host_docker_log_path}: {e}")
            pass 