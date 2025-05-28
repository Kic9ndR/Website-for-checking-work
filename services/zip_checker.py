import os
import zipfile
from typing import Dict, Any, List
import tempfile
import shutil

class ZipArchiveChecker:
    def __init__(self, archive_path: str):
        self.archive_path = archive_path
        self.temp_dir = None
        # Результат проверки архива. Содержит общий статус, сообщение и детали по каждому этапу.
        self.results = {
            "status": "PASSED",
            "message": "Архив успешно проверен",
            "details": {}
        }

    def run_checks(self) -> Dict[str, Any]:
        try:
            # 1. Проверка существования архива. Если файл не найден — ошибка.
            if not os.path.exists(self.archive_path):
                self._add_error("file_not_found", "Архив не найден")
                return self.results

            # 2. Проверка, что файл действительно ZIP-архив. Если нет — ошибка.
            if not zipfile.is_zipfile(self.archive_path):
                self._add_error("not_zip", "Файл не является ZIP-архивом")
                return self.results

            # 3. Создание временной директории для извлечения содержимого архива.
            self.temp_dir = tempfile.mkdtemp(prefix="extracted_")

            # 4. Открытие архива и последовательная проверка его содержимого:
            with zipfile.ZipFile(self.archive_path, 'r') as zip_ref:
                # 4.1. Проверка структуры архива: наличие FBX и текстур.
                self._check_archive_structure(zip_ref)

                # 4.2. Проверка FBX-файлов: количество, размер, имена.
                self._check_fbx_files(zip_ref)

                # 4.3. Проверка текстур: наличие, размер, формат.
                self._check_textures(zip_ref)

                # 4.4. Проверка общего размера архива.
                self._check_archive_size(zip_ref)

            return self.results

        except Exception as e:
            # Если возникла непредвиденная ошибка — добавляем её в результат.
            self._add_error("unexpected_error", f"Неожиданная ошибка: {str(e)}")
            return self.results

        finally:
            # После проверки всегда удаляем временную директорию.
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _check_archive_structure(self, zip_ref: zipfile.ZipFile):
        """Проверяет структуру архива: наличие FBX-файлов и текстур. Результат отражается в details."""
        has_fbx = False
        has_textures = False

        for file_info in zip_ref.infolist():
            if file_info.filename.endswith('.fbx'):
                has_fbx = True
            elif any(file_info.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tga', '.tiff']):
                has_textures = True

        if not has_fbx:
            # Если FBX-файлы не найдены — ошибка.
            self._add_error("no_fbx", "В архиве не найдено FBX-файлов")
        if not has_textures:
            # Если текстуры не найдены — предупреждение.
            self._add_warning("no_textures", "В архиве не найдено текстур")

    def _check_fbx_files(self, zip_ref: zipfile.ZipFile):
        """Проверяет FBX-файлы: количество, размер, имена. Информация и предупреждения отражаются в details."""
        fbx_files = [f for f in zip_ref.namelist() if f.lower().endswith('.fbx')]
        
        if not fbx_files:
            return

        # Информация о найденных FBX-файлах.
        self._add_info("fbx_files", {
            "status": "PASSED",
            "details": f"Найдено FBX-файлов: {len(fbx_files)}",
            "files": fbx_files
        })

        # Проверка размера каждого FBX-файла (если больше 100 МБ — предупреждение).
        for fbx_file in fbx_files:
            file_info = zip_ref.getinfo(fbx_file)
            if file_info.file_size > 100 * 1024 * 1024:  # 100MB
                self._add_warning(f"fbx_size_{fbx_file}", f"FBX-файл {fbx_file} превышает 100MB")

    def _check_textures(self, zip_ref: zipfile.ZipFile):
        """Проверяет текстуры: наличие, размер, формат. Информация и предупреждения отражаются в details."""
        texture_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.tga', '.tiff']
        texture_files = [f for f in zip_ref.namelist() 
                        if any(f.lower().endswith(ext) for ext in texture_extensions)]

        if not texture_files:
            return

        # Информация о найденных текстурах.
        self._add_info("textures", {
            "status": "PASSED",
            "details": f"Найдено текстур: {len(texture_files)}",
            "files": texture_files
        })

        # Проверка размера каждой текстуры (если больше 10 МБ — предупреждение).
        for texture_file in texture_files:
            file_info = zip_ref.getinfo(texture_file)
            if file_info.file_size > 10 * 1024 * 1024:  # 10MB
                self._add_warning(f"texture_size_{texture_file}", 
                                f"Текстура {texture_file} превышает 10MB")

    def _check_archive_size(self, zip_ref: zipfile.ZipFile):
        """Проверяет общий размер архива. Если больше 500 МБ — предупреждение."""
        total_size = sum(file_info.file_size for file_info in zip_ref.infolist())
        if total_size > 500 * 1024 * 1024:  # 500MB
            self._add_warning("archive_size", "Общий размер архива превышает 500MB")

    def _add_error(self, check_name: str, message: str):
        """Добавляет ошибку в результаты (details). Ошибка влияет на общий статус архива."""
        self.results["status"] = "FAILED"
        self.results["message"] = "Обнаружены ошибки в архиве"
        self.results["details"][check_name] = {
            "status": "FAILED",
            "details": message
        }

    def _add_warning(self, check_name: str, message: str):
        """Добавляет предупреждение в результаты (details). Предупреждение не блокирует проверку, но меняет статус на WARNING."""
        if self.results["status"] == "PASSED":
            self.results["status"] = "WARNING"
            self.results["message"] = "Обнаружены предупреждения в архиве"
        self.results["details"][check_name] = {
            "status": "WARNING",
            "details": message
        }

    def _add_info(self, check_name: str, data: Dict[str, Any]):
        """Добавляет информационное сообщение в результаты (details)."""
        self.results["details"][check_name] = data

    def is_valid(self) -> bool:
        """Проверяет, прошел ли архив все проверки (нет ошибок, только предупреждения или всё чисто)."""
        return self.results["status"] in ["PASSED", "WARNING"] 