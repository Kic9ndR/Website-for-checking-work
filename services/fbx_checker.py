import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

class FBXChecker:
    def __init__(self, blender_path: str = "blender"):
        self.blender_path = blender_path
        self.temp_dir = None
        self.script_path = os.path.join(os.path.dirname(__file__), "..", "blender-docker", "addons", "model_checker.py")

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def check_archive(self, archive_path: str) -> Tuple[bool, Dict, str]:
        """
        Проверяет архив с FBX файлами
        
        Args:
            archive_path: Путь к ZIP архиву
            
        Returns:
            Tuple[bool, Dict, str]: (успех, результаты проверки, путь к логам)
        """
        try:
            # Создаем временную директорию для результатов
            output_dir = os.path.join(self.temp_dir, "results")
            os.makedirs(output_dir, exist_ok=True)
            
            # Путь для сохранения результатов
            output_path = os.path.join(output_dir, "check_results.json")
            
            # Запускаем Blender с нашим скриптом
            cmd = [
                self.blender_path,
                "--background",  # Запуск в фоновом режиме
                "--python", self.script_path,
                "--",  # Разделитель для аргументов скрипта
                archive_path,
                output_path
            ]
            
            # Запускаем процесс
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Ждем завершения
            stdout, stderr = process.communicate()
            
            # Проверяем результаты
            if process.returncode != 0:
                return False, {"error": stderr}, output_dir
            
            # Читаем результаты из JSON
            if os.path.exists(output_path):
                import json
                with open(output_path, 'r', encoding='utf-8') as f:
                    results = json.load(f)
                return True, results, output_dir
            
            return False, {"error": "No results file found"}, output_dir
            
        except Exception as e:
            return False, {"error": str(e)}, output_dir

    def cleanup(self):
        """Очищает временные файлы"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            self.temp_dir = None 