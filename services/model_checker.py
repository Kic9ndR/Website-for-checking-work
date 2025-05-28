import os
import subprocess
import tempfile
import zipfile
import shutil
import json
from typing import Dict, List, Tuple
from pathlib import Path
from datetime import datetime

class ModelChecker:
    def __init__(self):
        self.blender_docker_dir = Path("blender-docker")
        self.input_dir = self.blender_docker_dir / "input"
        self.output_dir = self.blender_docker_dir / "output"
        self.temp_dir = self.blender_docker_dir / "temp"
        
        # Создаем директории, если их нет
        self.input_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)

    async def check_model(self, file_path: str):
        temp_input = None
        original_filename = Path(file_path).name
        file_extension = Path(file_path).suffix.lower()

        if file_extension not in ['.fbx', '.zip']:
             raise ValueError(f"Неподдерживаемое расширение файла: {file_extension}")

        try:
            if file_extension == '.zip':
                # Обрабатываем ZIP-архив
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    # Проверяем размер архива
                    total_size = sum(file.file_size for file in zip_ref.filelist)
                    if total_size > 100 * 1024 * 1024:  # 100MB
                        raise ValueError("Размер архива превышает 100MB")

                    # Извлекаем файлы во временную директорию
                    zip_ref.extractall(self.temp_dir)

                    # Ищем FBX файлы
                    fbx_files = []
                    for root, _, files in os.walk(self.temp_dir):
                        for file in files:
                            if file.lower().endswith('.fbx'):
                                fbx_files.append(os.path.join(root, file))

                    if not fbx_files:
                        raise ValueError("В архиве не найдены FBX файлы")

                    # Проверяем каждый FBX файл
                    all_results = []
                    for fbx_file in fbx_files:
                        temp_input_name = f"input_{os.urandom(8).hex()}.fbx"
                        temp_input = self.input_dir / temp_input_name
                        shutil.copy2(fbx_file, temp_input)
                        
                        docker_input_path = f"/input/{temp_input.name}"
                        docker_output_path = "/output/check_results.json"
                        
                        command_to_run = f'/app/check_model.sh "{docker_input_path}" "{docker_output_path}"'
                        print(f"Running docker command: {command_to_run}")
                        result = subprocess.run(
                            ["docker-compose", "run", "--rm", "blender", "sh", "-c", command_to_run],
                            cwd=str(self.blender_docker_dir),
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='replace'
                        )
                        
                        if result.returncode != 0:
                            error_output = result.stderr or result.stdout
                            error_details = ""
                            output_file = self.output_dir / "check_results.json"
                            if output_file.exists():
                                try:
                                    with open(output_file, 'r') as f:
                                        error_data = json.load(f)
                                    if error_data.get('status') == 'error' and error_data.get('message'):
                                        error_details = f"\nBlender Script Error: {error_data['message']}"
                                except Exception as json_e:
                                    error_details = f"\nCould not read error JSON: {json_e}"
                            
                            raise Exception(f"Ошибка при проверке модели (код {result.returncode}). Docker output:\n{error_output}{error_details}")
                        
                        output_file = self.output_dir / "check_results.json"
                        if not output_file.exists():
                            docker_stdout = result.stdout or ""
                            docker_stderr = result.stderr or ""
                            raise Exception(f"Файл с результатами ({output_file}) не был создан, хотя Docker вернул код 0. Docker stdout:\n{docker_stdout}\nDocker stderr:\n{docker_stderr}")
                            
                        with open(output_file, 'r', encoding='utf-8') as f:
                            file_results = json.load(f)
                            all_results.append(file_results)
                            
                        if output_file.exists():
                            output_file.unlink()
                            
                        if temp_input and temp_input.exists():
                            temp_input.unlink()
                    
                    # Формируем общие результаты
                    results = {
                        'status': 'success',
                        'check_date': datetime.now().isoformat(),
                        'blender_version': all_results[0]['blender_version'] if all_results else 'unknown',
                        'input_files': [os.path.basename(f) for f in fbx_files],
                        'total_files_checked': len(fbx_files),
                        'files_results': all_results
                    }
                    
                    return results
            else:
                # Обрабатываем одиночный FBX файл
                temp_input_name = f"input_{os.urandom(8).hex()}{file_extension}"
                temp_input = self.input_dir / temp_input_name
                shutil.copy2(file_path, temp_input)
                
                docker_input_path = f"/input/{temp_input.name}"
                docker_output_path = "/output/check_results.json"
                
                command_to_run = f'/app/check_model.sh "{docker_input_path}" "{docker_output_path}"'
                print(f"Running docker command: {command_to_run}")
                result = subprocess.run(
                    ["docker-compose", "run", "--rm", "blender", "sh", "-c", command_to_run],
                    cwd=str(self.blender_docker_dir),
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace'
                )
                
                if result.returncode != 0:
                    error_output = result.stderr or result.stdout
                    error_details = ""
                    output_file = self.output_dir / "check_results.json"
                    if output_file.exists():
                        try:
                            with open(output_file, 'r') as f:
                                error_data = json.load(f)
                            if error_data.get('status') == 'error' and error_data.get('message'):
                                error_details = f"\nBlender Script Error: {error_data['message']}"
                        except Exception as json_e:
                            error_details = f"\nCould not read error JSON: {json_e}"
                    
                    raise Exception(f"Ошибка при проверке модели (код {result.returncode}). Docker output:\n{error_output}{error_details}")
                
                output_file = self.output_dir / "check_results.json"
                if not output_file.exists():
                    docker_stdout = result.stdout or ""
                    docker_stderr = result.stderr or ""
                    raise Exception(f"Файл с результатами ({output_file}) не был создан, хотя Docker вернул код 0. Docker stdout:\n{docker_stdout}\nDocker stderr:\n{docker_stderr}")
                    
                with open(output_file, 'r', encoding='utf-8') as f:
                    check_results = json.load(f)
                    
                if output_file.exists():
                    output_file.unlink()
                    
                return check_results
                
        finally:
            if temp_input and temp_input.exists():
                try:
                    temp_input.unlink()
                    print(f"Deleted temp input: {temp_input}")
                except Exception as del_e:
                    print(f"Error deleting temp input {temp_input}: {del_e}")
            # Очищаем временную директорию
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                self.temp_dir.mkdir(exist_ok=True) 