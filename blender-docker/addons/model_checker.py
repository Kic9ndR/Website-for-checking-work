import bpy
from bpy.types import Operator, Panel
from bpy.props import BoolProperty, StringProperty
import bmesh
import math
import zipfile
import os
import shutil
from PIL import Image
import re
import tempfile
from typing import Dict
import json

# ANSI-коды для цветного вывода
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# Глобальная переменная для хранения пути к извлечённой папке
EXTRACT_DIR = None

# Глобальный кэш для результатов анализа текстур
TEXTURE_CACHE = {}

# Глобальная переменная для хранения результатов проверки нейминга
NAMING_DETAILS = {
    'geometry': [],
    'materials': [],
    'textures': [],
    'invalid_chars': [],
    'duplicates': []  # Новый раздел для дубликатов
}

# Глобальная переменная для хранения деталей ошибок Geometry Data
GEOMETRY_DETAILS = {
    'archive_size': {'status': 'Not Checked', 'messages': []},
    'fbx_files': {'status': 'Not Checked', 'messages': []},
    'scene_content': {'status': 'Not Checked', 'messages': []},
    'ground_drop': {'status': 'Not Checked', 'messages': []},
    'geometry_cleanliness': {'status': 'Not Checked', 'messages': []},
    'triangulation': {'status': 'Not Checked', 'messages': []},
    'transforms': {'status': 'Not Checked', 'messages': []},
    'uv_maps': {'status': 'Not Checked', 'messages': []},
    'polygons': {'status': 'Not Checked', 'messages': []}
}

# Константы для Geometry Data
MAX_ARCHIVE_SIZE = 1 * 1024 * 1024 * 1024  # 1 ГБ в байтах
MAX_FBX_FILES = 21
MIN_FBX_FILES = 1
GROUND_FBX_PATTERN = r'.*_Ground\.fbx$'  # Маска для Ground FBX
OKS_FBX_PATTERN = r'^\d{4}_[A-Za-z0-9_]+_(0[1-9]|1[0-9]|20)\.fbx$'  # Маска для ОКС: [xxxx]_[address]_[01-20].fbx
POLY_LIMIT_MAIN = 150000  # Лимит полигонов для Main и MainGlass
POLY_LIMIT_GROUND = 180000  # Лимит полигонов для Ground, Flora, GroundEl
MERGE_DISTANCE = 0.025  # Расстояние для сшивания вершин
UV_PADDING = 8  # Отступ UV от краёв в пикселях (8 px)
TEXTURE_SIZE_DEFAULT = 2048  # Размер текстур по умолчанию (2048x2048)
TEXTURE_SIZE_GROUNDEL = 512  # Размер текстур для GroundEl (512x512)
MIN_GROUND_DROP = 1.0  # Минимальный опуск геометрии Ground (1 метр)
TRANSFORM_TOLERANCE = 0.000001  # Допустимая погрешность трансформаций (для масштаба)
BLENDER_ROTATION_BUG = -0.000008  # Погрешность поворота за каждую выгрузку (баг Blender)
BUG_TOLERANCE = 0.000001  # Допустимая погрешность для проверки кратности (на случай ошибок округления)
MAX_ROTATION_BUG_COUNT = 5  # Максимальное количество выгрузок (n <= 5)

# Добавляем кэш для результатов проверки
CHECK_CACHE = {}

def get_cache_key(archive_path: str) -> str:
    """Генерирует ключ кэша на основе пути к архиву и его размера"""
    size = os.path.getsize(archive_path)
    mtime = os.path.getmtime(archive_path)
    return f"{archive_path}_{size}_{mtime}"

def check_archive_with_cache(archive_path: str, output_path: str) -> Dict:
    """
    Проверяет архив с использованием кэша
    """
    cache_key = get_cache_key(archive_path)
    
    # Проверяем кэш
    if cache_key in CHECK_CACHE:
        cached_result = CHECK_CACHE[cache_key]
        # Проверяем, не изменился ли файл
        if os.path.exists(archive_path) and os.path.getmtime(archive_path) == cached_result.get('mtime'):
            # Сохраняем результаты в файл
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(cached_result['data'], f, ensure_ascii=False, indent=4)
            return cached_result['data']
    
    # Если нет в кэше или файл изменился, выполняем проверку
    results = check_archive(archive_path, output_path)
    
    # Сохраняем в кэш
    CHECK_CACHE[cache_key] = {
        'data': results,
        'mtime': os.path.getmtime(archive_path)
    }
    
    return results

def check_archive(archive_path: str, output_path: str) -> Dict:
    """
    Проверяет архив с FBX файлами
    """
    try:
        # Создаем временную директорию
        with tempfile.TemporaryDirectory() as temp_dir:
            # Извлекаем архив
            extracted_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extracted_dir, exist_ok=True)
            
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(extracted_dir)
            
            # Находим все FBX файлы
            fbx_files = []
            for root, _, files in os.walk(extracted_dir):
                for file in files:
                    if file.lower().endswith('.fbx'):
                        fbx_files.append(os.path.join(root, file))
            
            if not fbx_files:
                return {"error": "No FBX files found in archive"}
            
            # Создаем пустую сцену
            bpy.ops.wm.read_factory_settings(use_empty=True)
            
            # Импортируем FBX файлы
            for fbx_file in fbx_files:
                bpy.ops.import_scene.fbx(filepath=fbx_file)
            
            # Выполняем проверки
            results = {
                "geometry_data": {},
                "texture_material": {},
                "naming": {}
            }
            
            # Проверка геометрии
            results["geometry_data"] = check_geometry()
            
            # Проверка текстур и материалов
            results["texture_material"] = check_textures_and_materials()
            
            # Проверка нейминга
            results["naming"] = check_naming()
            
            # Сохраняем результаты
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=4)
            
            return results
            
    except Exception as e:
        return {"error": str(e)}

def check_geometry() -> Dict:
    """
    Проверяет геометрию модели
    """
    results = {
        "archive_size": {"status": "Not Checked", "messages": []},
        "fbx_files": {"status": "Not Checked", "messages": []},
        "scene_content": {"status": "Not Checked", "messages": []},
        "ground_drop": {"status": "Not Checked", "messages": []},
        "geometry_cleanliness": {"status": "Not Checked", "messages": []},
        "triangulation": {"status": "Not Checked", "messages": []},
        "transforms": {"status": "Not Checked", "messages": []},
        "uv_maps": {"status": "Not Checked", "messages": []},
        "polygons": {"status": "Not Checked", "messages": []}
    }
    
    # Проверка размера архива
    size_ok, size_msg = check_archive_size(archive_path)
    results["archive_size"]["status"] = "PASSED" if size_ok else "FAILED"
    results["archive_size"]["messages"].append(size_msg)
    
    # Проверка FBX файлов
    fbx_ok, fbx_files, fbx_msg = check_archive_contents(archive_path)
    results["fbx_files"]["status"] = "PASSED" if fbx_ok else "FAILED"
    results["fbx_files"]["messages"].append(fbx_msg)
    
    # Проверка содержимого сцены
    content_ok, content_issues = check_scene_contents()
    results["scene_content"]["status"] = "PASSED" if content_ok else "FAILED"
    results["scene_content"]["messages"].extend(content_issues)
    
    # Проверка опуска Ground
    ground_ok, ground_msg = check_ground_drop()
    results["ground_drop"]["status"] = "PASSED" if ground_ok else "FAILED"
    results["ground_drop"]["messages"].append(ground_msg)
    
    # Проверка чистоты геометрии
    clean_ok, clean_issues = check_geometry_cleanliness()
    results["geometry_cleanliness"]["status"] = "PASSED" if clean_ok else "FAILED"
    results["geometry_cleanliness"]["messages"].extend(clean_issues)
    
    # Проверка триангуляции
    triang_ok, triang_msg = check_triangulation()
    results["triangulation"]["status"] = "PASSED" if triang_ok else "FAILED"
    results["triangulation"]["messages"].append(triang_msg)
    
    # Проверка трансформаций
    trans_ok, trans_issues = check_transforms()
    results["transforms"]["status"] = "PASSED" if trans_ok else "FAILED"
    results["transforms"]["messages"].extend(trans_issues)
    
    # Проверка UV-развёртки
    uv_ok, uv_issues = check_uv_maps()
    results["uv_maps"]["status"] = "PASSED" if uv_ok else "FAILED"
    results["uv_maps"]["messages"].extend(uv_issues)
    
    # Подсчёт полигонов
    poly_counts = count_polygons()
    results["polygons"].update(poly_counts)
    
    return results

# Регистрация свойств сцены (обновлено для Geometry Data)
def register_scene_properties():
    # Geometry Data (заменили poly_count на geometry_data)
    bpy.types.Scene.geometry_data_enabled = BoolProperty(name="Geometry Data Enabled", default=False)
    bpy.types.Scene.geometry_data_checked = BoolProperty(name="Geometry Data Checked", default=False)
    bpy.types.Scene.oks_poly_count = StringProperty(default="N/A")
    bpy.types.Scene.ground_poly_count = StringProperty(default="N/A")
    bpy.types.Scene.other_poly_count = StringProperty(default="N/A")
    bpy.types.Scene.oks_status = StringProperty(default="Not Checked")
    bpy.types.Scene.ground_status = StringProperty(default="Not Checked")
    bpy.types.Scene.archive_status = StringProperty(default="Not Checked")
    bpy.types.Scene.fbx_files_status = StringProperty(default="Not Checked")
    bpy.types.Scene.fbx_content_status = StringProperty(default="Not Checked")
    bpy.types.Scene.ground_drop_status = StringProperty(default="Not Checked")
    bpy.types.Scene.geometry_clean_status = StringProperty(default="Not Checked")
    bpy.types.Scene.triangulation_status = StringProperty(default="Not Checked")
    bpy.types.Scene.transform_status = StringProperty(default="Not Checked")
    bpy.types.Scene.uv_status = StringProperty(default="Not Checked")

    # Texture and Material
    bpy.types.Scene.texture_material_enabled = BoolProperty(name="Texture and Material Enabled", default=False)
    bpy.types.Scene.texture_material_checked = BoolProperty(name="Texture and Material Checked", default=False)
    bpy.types.Scene.texture_format_status = StringProperty(default="Not Checked")
    bpy.types.Scene.alpha_channel_status = StringProperty(default="Not Checked")
    bpy.types.Scene.gray_scale_status = StringProperty(default="Not Checked")
    bpy.types.Scene.texture_size_status = StringProperty(default="Not Checked")
    bpy.types.Scene.glass_material_status = StringProperty(default="Not Checked")
    bpy.types.Scene.ground_material_status = StringProperty(default="Not Checked")
    bpy.types.Scene.ground_texel_status = StringProperty(default="Not Checked")

    # Geometry Naming
    bpy.types.Scene.geometry_naming_enabled = BoolProperty(name="Geometry Naming Enabled", default=False)
    bpy.types.Scene.geometry_naming_checked = BoolProperty(name="Geometry Naming Checked", default=False)
    bpy.types.Scene.geometry_naming_status = StringProperty(default="Not Checked")

    # Общие
    bpy.types.Scene.archive_path = StringProperty(name="Archive Path", description="Path to ZIP archive", default="", subtype='FILE_PATH')

def unregister_scene_properties():
    properties = [
        "geometry_data_enabled", "geometry_data_checked",  # Обновили с poly_count на geometry_data
        "oks_poly_count", "ground_poly_count", "other_poly_count",
        "oks_status", "ground_status", "archive_status", "fbx_files_status",
        "fbx_content_status", "ground_drop_status", "geometry_clean_status",
        "triangulation_status", "transform_status", "uv_status",
        "texture_material_enabled", "texture_material_checked",
        "texture_format_status", "alpha_channel_status", "gray_scale_status",
        "texture_size_status", "glass_material_status", "ground_material_status",
        "ground_texel_status", "geometry_naming_enabled", "geometry_naming_checked",
        "geometry_naming_status", "archive_path"
    ]
    for prop in properties:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

# Проверка размера архива (с округлением)
def check_archive_size(archive_path):
    if not os.path.exists(archive_path):
        return False, "Archive does not exist"
    size = os.path.getsize(archive_path)
    size_mb = round(size / (1024 * 1024))  # Округляем до целого числа мегабайт
    if size > MAX_ARCHIVE_SIZE:
        return False, f"Archive size {size_mb} MB exceeds 1 GB limit"
    return True, f"Archive size {size_mb} MB"

# Проверка состава архива
def check_archive_contents(archive_path):
    if not os.path.exists(archive_path):
        return False, [], "Archive does not exist"

    fbx_files = []
    try:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            for file in zip_ref.namelist():
                if file.lower().endswith('.fbx'):
                    fbx_files.append(file)

        # Проверка количества FBX файлов
        if len(fbx_files) < MIN_FBX_FILES or len(fbx_files) > MAX_FBX_FILES:
            return False, fbx_files, f"Found {len(fbx_files)} FBX files, expected 1 to 21"

        # Проверка наличия Ground FBX
        ground_fbx = [f for f in fbx_files if re.match(GROUND_FBX_PATTERN, os.path.basename(f), re.IGNORECASE)]
        if not ground_fbx:
            return False, fbx_files, "No Ground FBX file found"
        if len(ground_fbx) > 1:
            return False, fbx_files, f"Multiple Ground FBX files found: {ground_fbx}"

        # Проверка ОКС FBX файлов
        oks_fbx = [f for f in fbx_files if f not in ground_fbx]
        if len(oks_fbx) > 20:
            return False, fbx_files, f"Too many OKS FBX files: {len(oks_fbx)}, expected up to 20"

        for fbx in oks_fbx:
            if not re.match(OKS_FBX_PATTERN, os.path.basename(fbx)):
                return False, fbx_files, f"Invalid OKS FBX name: {fbx}, expected [xxxx]_[address]_[01-20].fbx"

        return True, fbx_files, "Archive contents are valid"
    except Exception as e:
        return False, fbx_files, f"Error checking archive contents: {e}"

# Проверка содержимого сцены (только меши и вшитые текстуры)
def check_scene_contents():
    invalid_objects = []
    for obj in bpy.data.objects:
        if obj.type not in ('MESH', 'EMPTY'):  # EMPTY может быть временным при импорте
            invalid_objects.append(f"{obj.name} (type: {obj.type})")
        elif obj.type == 'MESH':
            # Проверяем наличие иерархических связей
            if obj.parent or obj.children:
                invalid_objects.append(f"{obj.name} (has parent or children)")
            # Проверяем наличие анимации
            if obj.animation_data:
                invalid_objects.append(f"{obj.name} (has animation data)")

    # Проверяем наличие костей, звуков и других данных
    if bpy.data.armatures:
        invalid_objects.append("Armatures found")
    if bpy.data.cameras:
        invalid_objects.append("Cameras found")
    if bpy.data.lights:
        invalid_objects.append("Lights found")
    if bpy.data.sounds:
        invalid_objects.append("Sounds found")

    return len(invalid_objects) == 0, invalid_objects

# Проверка опуска геометрии Ground (упрощённый подход)
def check_ground_drop():
    ground_objects = [obj for obj in bpy.data.objects if obj.type == 'MESH' and "Ground" in obj.name]
    if not ground_objects:
        return True, "No Ground objects found"

    for obj in ground_objects:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()

        # Находим грань с наибольшей площадью
        largest_face = max(bm.faces, key=lambda face: face.calc_area(), default=None)
        if not largest_face:
            bpy.ops.object.mode_set(mode='OBJECT')
            return False, f"{obj.name}: No faces found"

        # Определяем граничные вершины этой грани
        boundary_verts = set(largest_face.verts)

        # Проверяем высоту относительно самой нижней точки границы
        z_coords = [v.co.z for v in boundary_verts]
        min_z = min(z_coords)
        max_z = max(z_coords)

        # Проверяем опуск относительно самой нижней точки всей геометрии
        all_z_coords = [v.co.z for v in bm.verts]
        global_min_z = min(all_z_coords)

        drop = max_z - global_min_z
        if drop < MIN_GROUND_DROP:
            bpy.ops.object.mode_set(mode='OBJECT')
            return False, f"{obj.name}: Ground drop {drop}m is less than {MIN_GROUND_DROP}m"

        bpy.ops.object.mode_set(mode='OBJECT')

    return True, "Ground drop check passed"

# Проверка геометрии на дубликаты, летающие точки, вырожденные элементы
def check_geometry_cleanliness():
    issues = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue

        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        # Проверка на летающие точки (вершины без рёбер)
        for vert in bm.verts:
            if not vert.link_edges:
                issues.append(f"{obj.name}: Floating vertex at {vert.co}")

        # Проверка на вырожденные элементы (рёбра с длиной 0)
        for edge in bm.edges:
            if edge.verts[0].co == edge.verts[1].co:
                issues.append(f"{obj.name}: Degenerate edge at {edge.verts[0].co}")

        # Проверка на дубликаты вершин и сшивание
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=MERGE_DISTANCE)
        bmesh.update_edit_mesh(obj.data)

        bpy.ops.object.mode_set(mode='OBJECT')

    return len(issues) == 0, issues

# Проверка триангуляции
def check_triangulation():
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for poly in obj.data.polygons:
            if len(poly.vertices) != 3:
                return False, f"{obj.name}: Non-triangulated polygon found (vertices: {len(poly.vertices)})"
    return True, "All geometry is triangulated"

# Проверка трансформаций (с учётом бага Blender и ограничением n <= 5)
def check_transforms():
    issues = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue

        # Проверяем вращение
        euler = obj.rotation_euler
        for axis, angle in zip(('X', 'Y', 'Z'), (euler.x, euler.y, euler.z)):
            # Если угол равен 0, это не ошибка
            if angle == 0:
                continue

            # Проверяем, является ли угол кратным BLENDER_ROTATION_BUG
            if angle < 0:  # Учитываем только отрицательные значения
                # Вычисляем, сколько раз BLENDER_ROTATION_BUG помещается в angle
                multiplier = angle / BLENDER_ROTATION_BUG
                # Проверяем, является ли multiplier целым числом (с учётом погрешности)
                if abs(multiplier - round(multiplier)) * abs(BLENDER_ROTATION_BUG) < BUG_TOLERANCE:
                    # Проверяем, что n (multiplier) не превышает MAX_ROTATION_BUG_COUNT
                    if round(multiplier) <= MAX_ROTATION_BUG_COUNT:
                        continue  # Если кратно и n <= 5, это баг Blender, пропускаем

            # Если угол не равен 0 и не подпадает под условие бага, это ошибка
            issues.append(f"{obj.name}: Rotation not reset (Euler {axis}: {angle})")

        # Проверяем масштаб
        if any(abs(scale - 1.0) > TRANSFORM_TOLERANCE for scale in obj.scale):
            issues.append(f"{obj.name}: Scale not reset ({obj.scale})")

    return len(issues) == 0, issues

# Проверка UV-развёртки с учётом размеров текстур
def check_uv_maps():
    issues = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue

        if not obj.data.uv_layers:
            issues.append(f"{obj.name}")
            continue

        # Определяем размер текстуры в зависимости от типа объекта
        texture_size = TEXTURE_SIZE_GROUNDEL if "GroundEl" in obj.name else TEXTURE_SIZE_DEFAULT
        uv_padding_normalized = UV_PADDING / texture_size  # Нормализованный отступ (8 пикселей в UV-пространстве)

        # Флаги для отслеживания типов проблем в текущем объекте
        has_udim_issue = False
        has_padding_issue = False

        uv_layer = obj.data.uv_layers.active
        for poly in obj.data.polygons:
            for loop_idx in poly.loop_indices:
                uv = uv_layer.data[loop_idx].uv
                # Проверка выхода за пределы UDIM (0-1)
                if uv.x < 0 or uv.x > 1 or uv.y < 0 or uv.y > 1:
                    has_udim_issue = True
                # Проверка отступа от краёв
                if uv.x < uv_padding_normalized or uv.x > (1 - uv_padding_normalized) or \
                   uv.y < uv_padding_normalized or uv.y > (1 - uv_padding_normalized):
                    has_padding_issue = True

        # Если есть проблемы, добавляем только имя объекта
        if has_udim_issue or has_padding_issue:
            issues.append(f"{obj.name}")

    return len(issues) == 0, issues

# Рекурсивное извлечение содержимого архивов
def extract_archive_contents(archive_path, extract_to_dir):
    # Используем переданный extract_to_dir вместо глобальной EXTRACT_DIR
    if not os.path.exists(archive_path):
        print(f"Error: Archive path {archive_path} does not exist")
        return None, None, None

    # Очищаем и создаем директорию для извлечения
    archive_dir = os.path.dirname(archive_path)
    archive_name = os.path.splitext(os.path.basename(archive_path))[0]
    EXTRACT_DIR = os.path.join(archive_dir, f"Extracted_{archive_name}")
    if os.path.exists(EXTRACT_DIR):
        shutil.rmtree(EXTRACT_DIR)
    os.makedirs(EXTRACT_DIR)

    textures_dir = os.path.join(EXTRACT_DIR, "Textures")
    os.makedirs(textures_dir, exist_ok=True)
    extracted_fbx = []
    extracted_textures = []

    try:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(EXTRACT_DIR)
        print(f"Extracted archive {archive_path} to {EXTRACT_DIR}")

        for root, _, files in os.walk(EXTRACT_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                if file.lower().endswith('.fbx'):
                    extracted_fbx.append(file_path)
                elif file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tga', '.tiff')):  # Поддерживаем разные форматы текстур
                    new_path = os.path.join(textures_dir, file)
                    shutil.move(file_path, new_path)
                    extracted_textures.append(new_path)
                elif file.lower().endswith('.zip'):
                    sub_temp_dir, sub_fbx, sub_textures = extract_archive_contents(file_path)
                    if sub_fbx:
                        extracted_fbx.extend(sub_fbx)
                    if sub_textures:
                        extracted_textures.extend(sub_textures)

        print(f"Found {len(extracted_fbx)} FBX files and {len(extracted_textures)} textures in {EXTRACT_DIR}")
        print(f"Textures moved to {textures_dir}")
        return EXTRACT_DIR, extracted_fbx, extracted_textures
    except Exception as e:
        print(f"Error extracting archive: {e}")
        shutil.rmtree(EXTRACT_DIR, ignore_errors=True)
        return None, None, None

# Извлечение вшитых текстур из FBX (без конвертации в PNG)
def extract_embedded_textures(textures_dir):
    extracted_textures = []
    processed_names = set()  # Для избежания дублирования по имени
    for img in bpy.data.images:
        if img.source == 'FILE' and img.filepath and img.name not in processed_names:
            processed_names.add(img.name)
            # Используем оригинальное расширение файла
            original_extension = os.path.splitext(img.filepath)[1] or '.png'  # Если расширение отсутствует, используем .png
            texture_name = img.name + original_extension
            new_path = os.path.join(textures_dir, texture_name)
            print(f"Attempting to save embedded texture {img.name} to {new_path}")
            try:
                # Убеждаемся, что директория существует
                os.makedirs(textures_dir, exist_ok=True)
                # Распаковываем и сохраняем текстуру в её исходном формате
                if img.packed_file:
                    img.unpack(method='WRITE_LOCAL')  # Распаковываем вшитую текстуру
                # Копируем файл в его исходном формате
                shutil.copy(bpy.path.abspath(img.filepath), new_path)
                extracted_textures.append(new_path)
                print(f"Successfully extracted embedded texture {texture_name} to {new_path}")
            except Exception as e:
                print(f"Error saving texture {img.name} to {new_path}: {e}")
        else:
            print(f"Skipping texture {img.name}: already processed or invalid")
    return extracted_textures

# Импорт всех FBX-файлов
def import_fbx(fbx_files, textures_dir):
    # Очищаем сцену перед загрузкой новой модели
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    # Удаляем существующие материалы и текстуры
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)

    extracted_textures = []
    for fbx_path in fbx_files:
        try:
            bpy.ops.import_scene.fbx(filepath=fbx_path)
            print(f"Imported FBX: {fbx_path}")
            extracted_textures.extend(extract_embedded_textures(textures_dir))
        except Exception as e:
            print(f"Error importing FBX {fbx_path}: {e}")
    return extracted_textures

# Очистка неиспользуемых текстур из bpy.data.images
def clean_unused_textures():
    # Собираем текстуры, которые используются в сцене
    used_images = set()
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.data.materials:
            for mat in obj.data.materials:
                if mat and mat.node_tree:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            used_images.add(node.image.name)

    # Собираем список текстур для удаления
    images_to_remove = []
    for img in bpy.data.images:
        if img.name not in used_images:
            images_to_remove.append(img)

    # Удаляем текстуры в отдельном цикле
    for img in images_to_remove:
        if img.name in bpy.data.images:  # Проверяем, что текстура всё ещё существует
            print(f"Removing unused texture: {img.name}")
            bpy.data.images.remove(img)

# Анализ структуры текстуры с использованием кэша
def analyze_texture_structure(texture_path):
    """Анализирует структуру файла на наличие альфа-канала"""
    if texture_path in TEXTURE_CACHE:
        return TEXTURE_CACHE[texture_path]
    
    result = {
        'has_alpha_channel': False,
        'color_mode': 'Unknown',
        'bits_per_channel': 0,
        'size': (0, 0),
        'format': os.path.splitext(texture_path)[1].lower()  # Сохраняем формат файла
    }
    
    try:
        with Image.open(texture_path) as img:
            # Определяем режим и глубину цвета
            mode_info = {
                '1': (1, False),
                'L': (8, False),
                'LA': (16, True),
                'P': (8, False),
                'RGB': (24, False),
                'RGBA': (32, True),
                'CMYK': (32, False),
                'YCbCr': (24, False),
                'LAB': (24, False),
                'HSV': (24, False),
                'I': (32, False),
                'F': (32, False)
            }
            
            mode = img.mode
            bits, has_alpha = mode_info.get(mode, (0, False))
            
            # Дополнительная проверка для RGBA: если все значения альфа-канала 255, считаем его отсутствующим
            if mode == 'RGBA':
                alpha_data = img.split()[3]  # Получаем альфа-канал
                has_alpha = any(pixel < 255 for pixel in alpha_data.tobytes())
            
            result.update({
                'has_alpha_channel': has_alpha,
                'color_mode': mode,
                'bits_per_channel': img.bits if hasattr(img, 'bits') else bits,
                'size': img.size
            })
            
    except Exception as e:
        print(f"Error analyzing texture structure: {e}")
    
    TEXTURE_CACHE[texture_path] = result
    return result

# Анализ вшитых текстур
def analyze_embedded_textures():
    # Очищаем кэш перед анализом
    TEXTURE_CACHE.clear()
    
    texture_info = {}
    processed_names = set()  # Для отслеживания обработанных текстур
    duplicate_names = set()  # Для отслеживания дубликатов
    
    # Проверяем текстуры на дубликаты
    for img in bpy.data.images:
        if img.source == 'FILE' and img.filepath:
            base_name = re.sub(r'\.\d{3}$', '', img.name)  # Удаляем суффикс .001, .002 и т.д.
            if base_name in processed_names:
                # Если имя уже встречалось, это дубликат
                duplicate_names.add(img.name)
            else:
                processed_names.add(base_name)
    
    # Добавляем дубликаты в NAMING_DETAILS
    for dup_name in duplicate_names:
        NAMING_DETAILS['duplicates'].append([f"{dup_name}: Duplicate texture name detected"])
    
    # Анализируем все текстуры (не удаляем дубликаты)
    for img in bpy.data.images:
        if img.source == 'FILE' and img.filepath:
            temp_path = bpy.path.abspath(img.filepath) if os.path.exists(bpy.path.abspath(img.filepath)) else None
            if temp_path:
                info = analyze_texture_structure(temp_path)
                texture_info[img.name] = info
                print(f"Texture {img.name}: bit_depth={info['bits_per_channel']}, has_alpha={info['has_alpha_channel']}, size={info['size']}, color_mode={info['color_mode']}, format={info['format']}")
            else:
                print(f"Warning: Texture {img.name} has no valid filepath")
    
    return texture_info

# Проверка на недопустимые символы (только латиница, цифры и _)
def check_invalid_characters(name, category):
    global NAMING_DETAILS
    # Разрешены только латиница, цифры и _
    if not re.match(r'^[A-Za-z0-9_]+$', name.split('.png')[0] if name.endswith('.png') else name):
        # Формируем имя с выделением пробела
        modified_name = name.replace(' ', '[space]')
        # Собираем недопустимые символы (кроме пробела, который уже выделен)
        invalid_chars = ''.join(set(char for char in name if not char.isalnum() and char != '_' and char != ' '))
        # Формируем компактное сообщение
        error_msg = [f"{modified_name}: Invalid chars ({invalid_chars if invalid_chars else 'space'})"]
        NAMING_DETAILS['invalid_chars'].append(error_msg)
        return False
    return True

# Проверка нейминга геометрий, материалов и текстур
def validate_naming_all(texture_info):
    global NAMING_DETAILS
    NAMING_DETAILS = {'geometry': [], 'materials': [], 'textures': [], 'invalid_chars': [], 'duplicates': []}  # Очищаем предыдущие результаты
    
    # Проверка на дубликаты материалов
    processed_materials = set()
    duplicate_materials = set()
    for mat in bpy.data.materials:
        base_name = re.sub(r'\.\d{3}$', '', mat.name)  # Удаляем суффикс .001, .002 и т.д.
        if base_name in processed_materials:
            duplicate_materials.add(mat.name)
        else:
            processed_materials.add(base_name)
    
    # Добавляем дубликаты материалов в NAMING_DETAILS
    for dup_name in duplicate_materials:
        NAMING_DETAILS['duplicates'].append([f"{dup_name}: Duplicate material name detected"])
    
    # Проверка нейминга геометрий
    valid_geometry_patterns = [
        r'^SM_[A-Za-z0-9_]+_[0-9]+_Main$',  # Геометрия ОКС
        r'^SM_[A-Za-z0-9_]+_[0-9]+_MainGlass$',  # Полупрозрачные детали ОКС
        r'^SM_[A-Za-z0-9_]+_Ground$',  # Благоустройство
        r'^SM_[A-Za-z0-9_]+_GroundEl$',  # Элементы благоустройства
        r'^SM_[A-Za-z0-9_]+_GroundElGlass$',  # Полупрозрачные детали GroundEl
        r'^SM_[A-Za-z0-9_]+_Flora$'  # Растительность
    ]
    # Проверка нейминга материалов
    valid_material_patterns = [
        r'^M_[A-Za-z0-9_]+_[0-9]+_Main_[0-9]+$',  # Материалы ОКС
        r'^M_Glass_0[1-7]$',  # Материалы полупрозрачных деталей ОКС
        r'^M_[A-Za-z0-9_]+_Ground_[0-9]+$',  # Материалы благоустройства
        r'^M_[A-Za-z0-9_]+_GroundEl_[0-9]+$',  # Материалы элементов благоустройства
        r'^M_[A-Za-z0-9_]+_Flora_[0-9]+$'  # Материалы растительности
    ]
    naming_passed = True
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    
    if not mesh_objects:
        NAMING_DETAILS['geometry'].append(["No mesh objects found in the scene."])
    else:
        for obj in mesh_objects:
            is_valid = False
            for pattern in valid_geometry_patterns:
                if re.match(pattern, obj.name):
                    is_valid = True
                    NAMING_DETAILS['geometry'].append([f"{obj.name}: PASSED"])
                    break
            if not is_valid:
                naming_passed = False
                NAMING_DETAILS['geometry'].append([f"{obj.name}: FAILED", "- expected: SM_[street name]_[building number]_Main, SM_[street name]_[building number]_MainGlass, SM_[street name]_Ground, SM_[street name]_GroundEl, SM_[street name]_GroundElGlass, SM_[street name]_Flora"])
            # Проверка на недопустимые символы
            if not check_invalid_characters(obj.name, "Object"):
                naming_passed = False

    # Проверка нейминга материалов
    for mat in bpy.data.materials:
        is_valid = False
        for pattern in valid_material_patterns:
            if re.match(pattern, mat.name):
                is_valid = True
                NAMING_DETAILS['materials'].append([f"{mat.name}: PASSED"])
                break
        if not is_valid:
            naming_passed = False
            NAMING_DETAILS['materials'].append([f"{mat.name}: FAILED", "- expected: M_[street name]_[building number]_Main_[slot number], M_Glass_0[1-7], M_[street name]_Ground_[slot number], M_[street name]_GroundEl_[slot number], M_[street name]_Flora_[slot number]"])
        # Проверка на недопустимые символы
        if not check_invalid_characters(mat.name, "Material"):
            naming_passed = False
        
# Оператор для импорта модели
class CHECK_OT_ImportModel(Operator):
    bl_idname = "check.import_model"
    bl_label = "Import Model"
    
    def execute(self, context):
        scene = context.scene
        archive_path = scene.archive_path
        
        if not archive_path:
            self.report({'ERROR'}, "Please specify a valid archive path")
            return {'CANCELLED'}
        
        print("Starting Model Import...")
        
        # Извлекаем содержимое архива
        temp_dir, extracted_fbx, extracted_textures = extract_archive_contents(archive_path)
        if extracted_fbx:
            textures_dir = os.path.join(temp_dir, "Textures")
            import_fbx(extracted_fbx, textures_dir)
            self.report({'INFO'}, f"Model imported successfully from {archive_path}")
        else:
            self.report({'ERROR'}, "No FBX files found in the archive")
            return {'CANCELLED'}
        
        print("Model Import Completed")
        return {'FINISHED'}

# Оператор проверки геометрии (новый раздел Geometry Data)
class CHECK_OT_GeometryData(Operator):
    bl_idname = "check.geometry_data"
    bl_label = "Check Geometry Data"

    def execute(self, context):
        scene = context.scene
        global GEOMETRY_DETAILS
        GEOMETRY_DETAILS = {
            'archive_size': {'status': 'Not Checked', 'messages': []},
            'fbx_files': {'status': 'Not Checked', 'messages': []},
            'scene_content': {'status': 'Not Checked', 'messages': []},
            'ground_drop': {'status': 'Not Checked', 'messages': []},
            'geometry_cleanliness': {'status': 'Not Checked', 'messages': []},
            'triangulation': {'status': 'Not Checked', 'messages': []},
            'transforms': {'status': 'Not Checked', 'messages': []},
            'uv_maps': {'status': 'Not Checked', 'messages': []},
            'polygons': {'status': 'Not Checked', 'messages': []}
        }
        print("Starting Geometry Data Check...")

        # Проверка размера архива
        archive_passed, archive_message = check_archive_size(scene.archive_path)
        scene.archive_status = "PASSED" if archive_passed else "FAILED"
        GEOMETRY_DETAILS['archive_size']['status'] = scene.archive_status
        GEOMETRY_DETAILS['archive_size']['messages'].append(archive_message)
        print(f"Archive Check: {GREEN if archive_passed else RED}{scene.archive_status}{RESET} - {archive_message}")

        # Проверка состава архива
        fbx_passed, fbx_files, fbx_message = check_archive_contents(scene.archive_path)
        scene.fbx_files_status = "PASSED" if fbx_passed else "FAILED"
        GEOMETRY_DETAILS['fbx_files']['status'] = scene.fbx_files_status
        GEOMETRY_DETAILS['fbx_files']['messages'].append(fbx_message)
        print(f"FBX Files Check: {GREEN if fbx_passed else RED}{scene.fbx_files_status}{RESET} - {fbx_message}")

        # Проверка содержимого сцены
        content_passed, content_issues = check_scene_contents()
        scene.fbx_content_status = "PASSED" if content_passed else "FAILED"
        GEOMETRY_DETAILS['scene_content']['status'] = scene.fbx_content_status
        if content_passed:
            GEOMETRY_DETAILS['scene_content']['messages'].append("No invalid objects found")
        else:
            GEOMETRY_DETAILS['scene_content']['messages'].extend(content_issues)
        print(f"Scene Content Check: {GREEN if content_passed else RED}{scene.fbx_content_status}{RESET}")
        for issue in content_issues:
            print(f"  - {issue}")

        # Проверка опуска Ground
        ground_drop_passed, ground_drop_message = check_ground_drop()
        scene.ground_drop_status = "PASSED" if ground_drop_passed else "FAILED"
        GEOMETRY_DETAILS['ground_drop']['status'] = scene.ground_drop_status
        GEOMETRY_DETAILS['ground_drop']['messages'].append(ground_drop_message)
        print(f"Ground Drop Check: {GREEN if ground_drop_passed else RED}{scene.ground_drop_status}{RESET} - {ground_drop_message}")

        # Проверка чистоты геометрии
        geometry_clean_passed, geometry_issues = check_geometry_cleanliness()
        scene.geometry_clean_status = "PASSED" if geometry_clean_passed else "FAILED"
        GEOMETRY_DETAILS['geometry_cleanliness']['status'] = scene.geometry_clean_status
        if geometry_clean_passed:
            GEOMETRY_DETAILS['geometry_cleanliness']['messages'].append("No geometry issues found")
        else:
            GEOMETRY_DETAILS['geometry_cleanliness']['messages'].extend(geometry_issues)
        print(f"Geometry Cleanliness Check: {GREEN if geometry_clean_passed else RED}{scene.geometry_clean_status}{RESET}")
        for issue in geometry_issues:
            print(f"  - {issue}")

        # Проверка триангуляции
        triangulation_passed, triangulation_message = check_triangulation()
        scene.triangulation_status = "PASSED" if triangulation_passed else "FAILED"
        GEOMETRY_DETAILS['triangulation']['status'] = scene.triangulation_status
        GEOMETRY_DETAILS['triangulation']['messages'].append(triangulation_message)
        print(f"Triangulation Check: {GREEN if triangulation_passed else RED}{scene.triangulation_status}{RESET} - {triangulation_message}")

        # Проверка трансформаций
        transform_passed, transform_issues = check_transforms()
        scene.transform_status = "PASSED" if transform_passed else "FAILED"
        GEOMETRY_DETAILS['transforms']['status'] = scene.transform_status
        if transform_passed:
            GEOMETRY_DETAILS['transforms']['messages'].append("All transforms are reset")
        else:
            GEOMETRY_DETAILS['transforms']['messages'].extend(transform_issues)
        print(f"Transform Check: {GREEN if transform_passed else RED}{scene.transform_status}{RESET}")
        for issue in transform_issues:
            print(f"  - {issue}")

        # Проверка UV-развёртки
        uv_ok, uv_issues = check_uv_maps()
        geometry_results['uv_maps'] = {
            'status': 'PASSED' if uv_ok else 'FAILED',
            'messages': uv_issues
        }

        # Polygons Count
        oks_count = ground_count = other_count = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                bm = bmesh.from_edit_mesh(obj.data)
                bm.faces.ensure_lookup_table()
                tri_count = len([face for face in bm.faces if len(face.verts) == 3])
                bpy.ops.object.mode_set(mode='OBJECT')
                if 'Main' in obj.name or 'MainGlass' in obj.name:
                    oks_count += tri_count
                elif any(s in obj.name for s in ['Ground', 'Flora', 'GroundEl']):
                    ground_count += tri_count
                else:
                    other_count += tri_count
        poly_status = 'PASSED' if oks_count <= POLY_LIMIT_MAIN and ground_count <= POLY_LIMIT_GROUND else 'FAILED'
        poly_messages = [f"OKS polygon count: {oks_count}/{POLY_LIMIT_MAIN}", f"Ground polygon count: {ground_count}/{POLY_LIMIT_GROUND}"]
        if other_count > 0:
            poly_messages.append(f"Other polygon count: {other_count}")
        geometry_results['polygons'] = {'status': poly_status, 'messages': poly_messages}

        results['geometry_data'] = geometry_results
        
        # ---- Texture & Material Checks ----
        # Сначала извлекаем встроенные текстуры, если они есть
        embedded_textures_dir = os.path.join(os.path.dirname(output_path), "embedded_textures")
        if not os.path.exists(embedded_textures_dir):
            os.makedirs(embedded_textures_dir, exist_ok=True)
        extract_embedded_textures(embedded_textures_dir) # Сохраняем в подпапку
        
        # Анализируем все текстуры (встроенные и внешние, если есть)
        texture_analysis_results = analyze_embedded_textures() # Используем функцию для анализа встроенных
        results['texture_material'] = texture_analysis_results
        
         # ---- Naming Checks ----
        validate_naming_all(texture_analysis_results) # Используем результаты анализа текстур
        results['naming'] = NAMING_DETAILS # Используем глобальную переменную, которую заполняет validate_naming_all
       
        print("Проверки завершены.")

        return {'FINISHED'}

# Оператор для отображения деталей проверки геометрии
class CHECK_OT_ShowGeometryDetails(Operator):
    bl_idname = "check.show_geometry_details"
    bl_label = "Show Geometry Details"

    def execute(self, context):
        self.report({'INFO'}, "See the details in the popup window")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Geometry Data Check Details:")

        # Блок с ошибками (Failed Checks)
        layout.label(text="Failed Checks:", icon='ERROR')
        failed_box = layout.box()
        failed_checks_found = False

        # Archive Size
        if GEOMETRY_DETAILS['archive_size']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Archive Size Check:")
            for detail in GEOMETRY_DETAILS['archive_size']['messages']:
                box.label(text=detail)

        # FBX Files
        if GEOMETRY_DETAILS['fbx_files']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="FBX Files Check:")
            for detail in GEOMETRY_DETAILS['fbx_files']['messages']:
                box.label(text=detail)

        # Scene Content
        if GEOMETRY_DETAILS['scene_content']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Scene Content Check:")
            for detail in GEOMETRY_DETAILS['scene_content']['messages']:
                box.label(text=detail)

        # Ground Drop
        if GEOMETRY_DETAILS['ground_drop']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Ground Drop Check:")
            for detail in GEOMETRY_DETAILS['ground_drop']['messages']:
                box.label(text=detail)

        # Geometry Cleanliness
        if GEOMETRY_DETAILS['geometry_cleanliness']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Geometry Cleanliness Check:")
            for detail in GEOMETRY_DETAILS['geometry_cleanliness']['messages']:
                box.label(text=detail)

        # Triangulation
        if GEOMETRY_DETAILS['triangulation']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Triangulation Check:")
            for detail in GEOMETRY_DETAILS['triangulation']['messages']:
                box.label(text=detail)

        # Transforms
        if GEOMETRY_DETAILS['transforms']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Transforms Check:")
            for detail in GEOMETRY_DETAILS['transforms']['messages']:
                box.label(text=detail)

        # UV Maps
        if GEOMETRY_DETAILS['uv_maps']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="UV Maps Check:")
            for detail in GEOMETRY_DETAILS['uv_maps']['messages']:
                box.label(text=detail)

        # Polygons
        if GEOMETRY_DETAILS['polygons']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Polygon Count Check:")
            for detail in GEOMETRY_DETAILS['polygons']['messages']:
                box.label(text=detail)

        if not failed_checks_found:
            failed_box.label(text="No failed checks.")

        # Блок с прошедшими проверками (Passed Checks)
        layout.label(text="Passed Checks:", icon='CHECKMARK')
        passed_box = layout.box()
        passed_checks_found = False

        # Archive Size
        if GEOMETRY_DETAILS['archive_size']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Archive Size Check:")
            for detail in GEOMETRY_DETAILS['archive_size']['messages']:
                box.label(text=detail)

        # FBX Files
        if GEOMETRY_DETAILS['fbx_files']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="FBX Files Check:")
            for detail in GEOMETRY_DETAILS['fbx_files']['messages']:
                box.label(text=detail)

        # Scene Content
        if GEOMETRY_DETAILS['scene_content']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Scene Content Check:")
            for detail in GEOMETRY_DETAILS['scene_content']['messages']:
                box.label(text=detail)

        # Ground Drop
        if GEOMETRY_DETAILS['ground_drop']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Ground Drop Check:")
            for detail in GEOMETRY_DETAILS['ground_drop']['messages']:
                box.label(text=detail)

        # Geometry Cleanliness
        if GEOMETRY_DETAILS['geometry_cleanliness']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Geometry Cleanliness Check:")
            for detail in GEOMETRY_DETAILS['geometry_cleanliness']['messages']:
                box.label(text=detail)

        # Triangulation
        if GEOMETRY_DETAILS['triangulation']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Triangulation Check:")
            for detail in GEOMETRY_DETAILS['triangulation']['messages']:
                box.label(text=detail)

        # Transforms
        if GEOMETRY_DETAILS['transforms']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Transforms Check:")
            for detail in GEOMETRY_DETAILS['transforms']['messages']:
                box.label(text=detail)

        # UV Maps
        if GEOMETRY_DETAILS['uv_maps']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="UV Maps Check:")
            for detail in GEOMETRY_DETAILS['uv_maps']['messages']:
                box.label(text=detail)

        # Polygons
        if GEOMETRY_DETAILS['polygons']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Polygon Count Check:")
            for detail in GEOMETRY_DETAILS['polygons']['messages']:
                box.label(text=detail)

        if not passed_checks_found:
            passed_box.label(text="No passed checks.")

# Обновленный оператор проверки текстур и материалов
class CHECK_OT_TextureAndMaterial(Operator):
    bl_idname = "check.texture_and_material"
    bl_label = "Check Texture and Material"
    
    def execute(self, context):
        scene = context.scene
        global TEXTURE_MATERIAL_DETAILS
        TEXTURE_MATERIAL_DETAILS = {
            'texture_format': {'status': 'Not Checked', 'messages': []},
            'alpha_channel': {'status': 'Not Checked', 'messages': []},
            'texture_size': {'status': 'Not Checked', 'messages': []},
            'glass_material': {'status': 'Not Checked', 'messages': []},
            'ground_material': {'status': 'Not Checked', 'messages': []}
        }
        TEXTURE_CACHE.clear()  # Очищаем кэш перед новым анализом
        
        objects = bpy.context.selected_objects
        has_textures = False
        has_glass_objects = False
        has_ground_objects = False

        print("Starting Texture and Material Check...")

        # Инициализация статусов
        scene.texture_format_status = "Not Checked"
        scene.alpha_channel_status = "Not Checked"
        scene.gray_scale_status = "Not Checked"
        scene.texture_size_status = "Not Checked"
        scene.glass_material_status = "Not Checked"
        scene.ground_material_status = "Not Checked"
        scene.ground_texel_status = "Not Checked"
        scene.texture_material_checked = False

        # Очищаем неиспользуемые текстуры перед проверкой
        clean_unused_textures()

        # Работаем с текущими объектами в сцене
        objects = bpy.context.scene.objects
        texture_info = analyze_embedded_textures()

        # Проверка 1: Формат текстур (должен быть PNG)
        texture_format_passed = True
        texture_format_issues = []
        for obj in objects:
            if obj.type == 'MESH' and obj.data.materials:
                for mat in obj.data.materials:
                    if mat and mat.node_tree:
                        for node in mat.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                has_textures = True
                                if not node.image.filepath.lower().endswith('.png'):
                                    texture_format_passed = False
                                    issue = f"Texture {node.image.name} is not PNG"
                                    texture_format_issues.append(issue)
                                    print(f"Failed: {issue}")
        if has_textures:
            if texture_format_passed:
                TEXTURE_MATERIAL_DETAILS['texture_format']['messages'].append("All textures are in PNG format")
            else:
                TEXTURE_MATERIAL_DETAILS['texture_format']['messages'].extend(texture_format_issues)
        else:
            TEXTURE_MATERIAL_DETAILS['texture_format']['messages'].append("No textures found")
        scene.texture_format_status = "PASSED" if texture_format_passed and has_textures else "FAILED" if has_textures else "ABSENT"
        TEXTURE_MATERIAL_DETAILS['texture_format']['status'] = scene.texture_format_status

        # Проверка 2: Наличие альфа-канала в структуре файла
        alpha_channel_passed = True
        alpha_channel_issues = []
        if has_textures and texture_info:
            for texture_name, info in texture_info.items():
                if info['has_alpha_channel']:
                    alpha_channel_passed = False
                    issue = f"{texture_name} has alpha channel (mode: {info['color_mode']}, bits: {info['bits_per_channel']})"
                    alpha_channel_issues.append(issue)
                    print(f"FAIL: {issue}")
                else:
                    print(f"PASSED: {texture_name} does not have alpha channel (mode: {info['color_mode']}, bits: {info['bits_per_channel']})")
        if has_textures:
            if alpha_channel_passed:
                TEXTURE_MATERIAL_DETAILS['alpha_channel']['messages'].append("No textures with alpha channel found")
            else:
                TEXTURE_MATERIAL_DETAILS['alpha_channel']['messages'].extend(alpha_channel_issues)
        else:
            TEXTURE_MATERIAL_DETAILS['alpha_channel']['messages'].append("No textures found")
        scene.alpha_channel_status = "PASSED" if alpha_channel_passed else "FAILED" if has_textures else "ABSENT"
        TEXTURE_MATERIAL_DETAILS['alpha_channel']['status'] = scene.alpha_channel_status

        # Проверка 3: Размер текстур
        texture_size_passed = True
        texture_size_issues = []
        if has_textures and texture_info:
            for texture_name, info in texture_info.items():
                expected_size = (2048, 2048) if "GroundEl" not in texture_name else (512, 512)
                if info['size'] != expected_size:
                    texture_size_passed = False
                    issue = f"{texture_name} size is {info['size']}, expected {expected_size}"
                    texture_size_issues.append(issue)
                    print(f"Failed: {issue}")
                else:
                    print(f"Passed: {texture_name} size is {info['size']}, expected {expected_size}")
            if texture_size_passed:
                TEXTURE_MATERIAL_DETAILS['texture_size']['messages'].append("All textures have correct size")
            else:
                TEXTURE_MATERIAL_DETAILS['texture_size']['messages'].extend(texture_size_issues)
        else:
            TEXTURE_MATERIAL_DETAILS['texture_size']['messages'].append("No textures found")
        scene.texture_size_status = "PASSED" if texture_size_passed else "FAILED" if has_textures else "ABSENT"
        TEXTURE_MATERIAL_DETAILS['texture_size']['status'] = scene.texture_size_status

        # Проверка 4: Стеклянные материалы
        glass_material_passed = True
        glass_material_issues = []
        for obj in objects:
            if obj.type == 'MESH' and any(glass in obj.name for glass in ['MainGlass', 'GroundGlass', 'GroundElGlass']):
                has_glass_objects = True
                if obj.data.materials:
                    if len(obj.data.materials) > 7:
                        glass_material_passed = False
                        issue = f"{obj.name} has {len(obj.data.materials)} materials (max 7 allowed)"
                        glass_material_issues.append(issue)
                        print(f"Failed: {issue}")
                    for mat in obj.data.materials:
                        if mat and mat.node_tree:
                            for node in mat.node_tree.nodes:
                                if node.type == 'TEX_IMAGE' and node.image:
                                    glass_material_passed = False
                                    issue = f"{obj.name} has texture {node.image.name} (textures not allowed for glass)"
                                    glass_material_issues.append(issue)
                                    print(f"Failed: {issue}")
        if has_glass_objects:
            if glass_material_passed:
                TEXTURE_MATERIAL_DETAILS['glass_material']['messages'].append("Glass materials are valid")
            else:
                TEXTURE_MATERIAL_DETAILS['glass_material']['messages'].extend(glass_material_issues)
        else:
            TEXTURE_MATERIAL_DETAILS['glass_material']['messages'].append("No glass objects found")
        scene.glass_material_status = "PASSED" if glass_material_passed and has_glass_objects else "FAILED" if has_glass_objects else "ABSENT"
        TEXTURE_MATERIAL_DETAILS['glass_material']['status'] = scene.glass_material_status

        # Проверка 5: Материалы Ground
        ground_material_passed = True
        ground_material_issues = []
        for obj in objects:
            if obj.type == 'MESH' and "Ground" in obj.name and "GroundEl" not in obj.name:
                has_ground_objects = True
                if obj.data.materials and len(obj.data.materials) > 20:
                    ground_material_passed = False
                    issue = f"{obj.name} has {len(obj.data.materials)} materials (max 20 allowed)"
                    ground_material_issues.append(issue)
                    print(f"Failed: {issue}")
        if has_ground_objects:
            if ground_material_passed:
                TEXTURE_MATERIAL_DETAILS['ground_material']['messages'].append("Ground materials are valid")
            else:
                TEXTURE_MATERIAL_DETAILS['ground_material']['messages'].extend(ground_material_issues)
        else:
            TEXTURE_MATERIAL_DETAILS['ground_material']['messages'].append("No ground objects found")
        scene.ground_material_status = "PASSED" if ground_material_passed and has_ground_objects else "FAILED" if has_ground_objects else "ABSENT"
        TEXTURE_MATERIAL_DETAILS['ground_material']['status'] = scene.ground_material_status

        scene.texture_material_checked = True
        print("Texture and Material Check Completed")
        return {'FINISHED'}

# Оператор для отображения деталей проверки нейминга
class CHECK_OT_ShowNamingDetails(Operator):
    bl_idname = "check.show_naming_details"
    bl_label = "Show Naming Details"
    
    def execute(self, context):
        self.report({'INFO'}, "See the details in the popup window")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="Naming Check Details:")

        # Блок с ошибками (Failed Checks)
        layout.label(text="Failed Checks:", icon='ERROR')
        failed_box = layout.box()
        failed_checks_found = False

        # Geometry (ошибки)
        if any("FAILED" in detail[0] for detail in NAMING_DETAILS['geometry']):
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Geometry Naming:")
            for detail in NAMING_DETAILS['geometry']:
                if "FAILED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        # Materials (ошибки)
        if any("FAILED" in detail[0] for detail in NAMING_DETAILS['materials']):
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Material Naming:")
            for detail in NAMING_DETAILS['materials']:
                if "FAILED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        # Textures (ошибки)
        if any("FAILED" in detail[0] for detail in NAMING_DETAILS['textures']):
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Texture Naming:")
            for detail in NAMING_DETAILS['textures']:
                if "FAILED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        # Invalid Characters
        if NAMING_DETAILS['invalid_chars']:
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Invalid Characters Found:")
            for detail in NAMING_DETAILS['invalid_chars']:
                for line in detail:
                    box.label(text=line)

        # Duplicates
        if NAMING_DETAILS['duplicates']:
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Duplicate Items Found:")
            for detail in NAMING_DETAILS['duplicates']:
                for line in detail:
                    box.label(text=line)

        if not failed_checks_found:
            failed_box.label(text="No failed checks.")

        # Блок с прошедшими проверками (Passed Checks)
        layout.label(text="Passed Checks:", icon='CHECKMARK')
        passed_box = layout.box()
        passed_checks_found = False

        # Geometry (пройденные проверки)
        if any("PASSED" in detail[0] for detail in NAMING_DETAILS['geometry']):
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Geometry Naming:")
            for detail in NAMING_DETAILS['geometry']:
                if "PASSED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        # Materials (пройденные проверки)
        if any("PASSED" in detail[0] for detail in NAMING_DETAILS['materials']):
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Material Naming:")
            for detail in NAMING_DETAILS['materials']:
                if "PASSED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        # Textures (пройденные проверки)
        if any("PASSED" in detail[0] for detail in NAMING_DETAILS['textures']):
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Texture Naming:")
            for detail in NAMING_DETAILS['textures']:
                if "PASSED" in detail[0]:
                    for line in detail:
                        box.label(text=line)

        if not passed_checks_found:
            passed_box.label(text="No passed checks.")

# Проверка нейминга геометрий, материалов и текстур
class CHECK_OT_GeometryNaming(Operator):
    bl_idname = "check.geometry_naming"
    bl_label = "Check Geometry Naming"
    
    def execute(self, context):
        scene = context.scene
        print("Starting Geometry Naming Check...")
        
        # Очищаем неиспользуемые текстуры перед проверкой
        clean_unused_textures()
        
        # Выполняем проверку нейминга
        texture_info = analyze_embedded_textures() if bpy.data.images else {}
        naming_passed = validate_naming_all(texture_info)
        scene.geometry_naming_status = "PASSED" if naming_passed else "FAILED"
        scene.geometry_naming_checked = True
        
        print("Geometry Naming Check Completed")
        return {'FINISHED'}

# Глобальная переменная для хранения деталей ошибок Texture and Material
TEXTURE_MATERIAL_DETAILS = {
    'texture_format': {'status': 'Not Checked', 'messages': []},
    'alpha_channel': {'status': 'Not Checked', 'messages': []},
    'texture_size': {'status': 'Not Checked', 'messages': []},
    'glass_material': {'status': 'Not Checked', 'messages': []},
    'ground_material': {'status': 'Not Checked', 'messages': []}
}

# Оператор для отображения деталей проверки текстур и материалов
class CHECK_OT_ShowTextureMaterialDetails(Operator):
    bl_idname = "check.show_texture_material_details"
    bl_label = "Show Texture and Material Details"

    def execute(self, context):
        self.report({'INFO'}, "See the details in the popup window")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Texture and Material Check Details:")

        # Блок с ошибками (Failed Checks)
        layout.label(text="Failed Checks:", icon='ERROR')
        failed_box = layout.box()
        failed_checks_found = False

        # Texture Format
        if TEXTURE_MATERIAL_DETAILS['texture_format']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Texture Format Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['texture_format']['messages']:
                box.label(text=detail)

        # Alpha Channel
        if TEXTURE_MATERIAL_DETAILS['alpha_channel']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Alpha Channel Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['alpha_channel']['messages']:
                box.label(text=detail)

        # Texture Size
        if TEXTURE_MATERIAL_DETAILS['texture_size']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Texture Size Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['texture_size']['messages']:
                box.label(text=detail)

        # Glass Material
        if TEXTURE_MATERIAL_DETAILS['glass_material']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Glass Material Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['glass_material']['messages']:
                box.label(text=detail)

        # Ground Material
        if TEXTURE_MATERIAL_DETAILS['ground_material']['status'] == "FAILED":
            failed_checks_found = True
            box = failed_box.box()
            box.label(text="Ground Material Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['ground_material']['messages']:
                box.label(text=detail)

        if not failed_checks_found:
            failed_box.label(text="No failed checks.")

        # Блок с прошедшими проверками (Passed Checks)
        layout.label(text="Passed Checks:", icon='CHECKMARK')
        passed_box = layout.box()
        passed_checks_found = False

        # Texture Format
        if TEXTURE_MATERIAL_DETAILS['texture_format']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Texture Format Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['texture_format']['messages']:
                box.label(text=detail)

        # Alpha Channel
        if TEXTURE_MATERIAL_DETAILS['alpha_channel']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Alpha Channel Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['alpha_channel']['messages']:
                box.label(text=detail)

        # Texture Size
        if TEXTURE_MATERIAL_DETAILS['texture_size']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Texture Size Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['texture_size']['messages']:
                box.label(text=detail)

        # Glass Material
        if TEXTURE_MATERIAL_DETAILS['glass_material']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Glass Material Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['glass_material']['messages']:
                box.label(text=detail)

        # Ground Material
        if TEXTURE_MATERIAL_DETAILS['ground_material']['status'] == "PASSED":
            passed_checks_found = True
            box = passed_box.box()
            box.label(text="Ground Material Check:")
            for detail in TEXTURE_MATERIAL_DETAILS['ground_material']['messages']:
                box.label(text=detail)

        if not passed_checks_found:
            passed_box.label(text="No passed checks.")

# Очистка временной папки и сцены
def cleanup_temp_dir():
    global EXTRACT_DIR
    # Очистка сцены
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    # Удаляем текстуры
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    # Удаляем материалы
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    # Удаляем меши
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    # Удаляем временную папку
    if EXTRACT_DIR and os.path.exists(EXTRACT_DIR):
        shutil.rmtree(EXTRACT_DIR, ignore_errors=True)
        print(f"Cleaned up directory: {EXTRACT_DIR}")
        EXTRACT_DIR = None
    print("Scene cleared")

# Оператор для запуска всех проверок
class CHECK_OT_RunSelectedChecks(Operator):
    bl_idname = "check.run_selected_checks"
    bl_label = "Run Selected Checks"
    
    def execute(self, context):
        scene = context.scene
        
        if scene.geometry_data_enabled:
            bpy.ops.check.geometry_data()
        if scene.texture_material_enabled:
            bpy.ops.check.texture_and_material()
        if scene.geometry_naming_enabled:
            bpy.ops.check.geometry_naming()
        
        print("All selected checks completed")
        return {'FINISHED'}

# Оператор для выбора всех проверок
class CHECK_OT_SelectAll(Operator):
    bl_idname = "check.select_all"
    bl_label = "Select All"
    
    def execute(self, context):
        scene = context.scene
        scene.geometry_data_enabled = True
        scene.texture_material_enabled = True
        scene.geometry_naming_enabled = True
        print("All checks selected")
        return {'FINISHED'}

# Оператор для очистки выбора
class CHECK_OT_ClearSelection(Operator):
    bl_idname = "check.clear_selection"
    bl_label = "Clear Selection"
    
    def execute(self, context):
        scene = context.scene
        scene.geometry_data_enabled = False
        scene.texture_material_enabled = False
        scene.geometry_naming_enabled = False
        print("Selection cleared")
        return {'FINISHED'}

# Оператор для сброса результатов
class CHECK_OT_ResetCheckResults(Operator):
    bl_idname = "check.reset_check_results"
    bl_label = "Reset Check Results"
    
    def execute(self, context):
        scene = context.scene
        
        scene.geometry_data_checked = False
        scene.texture_material_checked = False
        scene.geometry_naming_checked = False
        scene.oks_poly_count = "N/A"
        scene.ground_poly_count = "N/A"
        scene.other_poly_count = "N/A"
        scene.oks_status = "Not Checked"
        scene.ground_status = "Not Checked"
        scene.archive_status = "Not Checked"
        scene.fbx_files_status = "Not Checked"
        scene.fbx_content_status = "Not Checked"
        scene.ground_drop_status = "Not Checked"
        scene.geometry_clean_status = "Not Checked"
        scene.triangulation_status = "Not Checked"
        scene.transform_status = "Not Checked"
        scene.uv_status = "Not Checked"
        scene.geometry_naming_status = "Not Checked"
        scene.texture_format_status = "Not Checked"
        scene.alpha_channel_status = "Not Checked"
        scene.gray_scale_status = "Not Checked"
        scene.texture_size_status = "Not Checked"
        scene.glass_material_status = "Not Checked"
        scene.ground_material_status = "Not Checked"
        scene.ground_texel_status = "Not Checked"
        
        print("Check results reset successfully!")
        return {'FINISHED'}

# Оператор для удаления временных файлов
class CHECK_OT_CleanupFiles(Operator):
    bl_idname = "check.cleanup_files"
    bl_label = "Cleanup Temporary Files"
    
    def execute(self, context):
        cleanup_temp_dir()
        print("Temporary files and scene cleaned up")
        return {'FINISHED'}

# Оператор для очистки и снятия регистрации
class CHECK_OT_ClearAndUnregister(Operator):
    bl_idname = "check.clear_and_unregister"
    bl_label = "Clear and Unregister"
    
    def execute(self, context):
        cleanup_temp_dir()
        unregister()
        unregister_scene_properties()
        print("Addon cleared and unregistered successfully!")
        return {'FINISHED'}

# Панель (обновлена для Geometry Data и добавлена кнопка Details в Texture and Material)
class CHECK_PT_Panel(Panel):
    bl_label = "Model Validation"
    bl_idname = "CHECK_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Model Checker'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        layout.label(text="Model Validation", icon='FILE_3D')
        layout.prop(scene, "archive_path", text="Archive Path")
        
        # Добавляем кнопку импорта модели
        layout.operator("check.import_model", text="Import Model", icon='IMPORT')
        
        box = layout.box()
        row = box.row()
        row.prop(scene, "geometry_data_enabled", text="Manage Selection", icon='CHECKBOX_HLT' if scene.geometry_data_enabled else 'CHECKBOX_DEHLT')
        row = box.row()
        row.operator("check.select_all", text="Select All", icon='CHECKBOX_HLT')
        row.operator("check.clear_selection", text="Clear Selection", icon='CHECKBOX_DEHLT')
        
        layout.separator()
        layout.label(text="Checks", icon='PLAY')
        
        # Раздел Geometry Data
        box = layout.box()
        row = box.row()
        row.prop(scene, "geometry_data_enabled", text="Geometry Data", icon='CHECKBOX_HLT' if scene.geometry_data_enabled else 'CHECKBOX_DEHLT')
        split = row.split(factor=0.6)
        split.operator("check.geometry_data", text="Check", icon='PLAY') if scene.geometry_data_enabled else split.label(text="Disabled", icon='CANCEL')
        if scene.geometry_data_checked:
            # Archive Size
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Archive Size:")
            split.label(text=scene.archive_status, icon='SOLO_ON' if scene.archive_status == 'PASSED' else 'SOLO_OFF' if scene.archive_status == 'FAILED' else 'QUESTION')

            # FBX Files
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="FBX Files:")
            split.label(text=scene.fbx_files_status, icon='SOLO_ON' if scene.fbx_files_status == 'PASSED' else 'SOLO_OFF' if scene.fbx_files_status == 'FAILED' else 'QUESTION')

            # Scene Content
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Scene Content:")
            split.label(text=scene.fbx_content_status, icon='SOLO_ON' if scene.fbx_content_status == 'PASSED' else 'SOLO_OFF' if scene.fbx_content_status == 'FAILED' else 'QUESTION')

            # Ground Drop
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Ground Drop:")
            split.label(text=scene.ground_drop_status, icon='SOLO_ON' if scene.ground_drop_status == 'PASSED' else 'SOLO_OFF' if scene.ground_drop_status == 'FAILED' else 'QUESTION')

            # Geometry Cleanliness
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Geometry Cleanliness:")
            split.label(text=scene.geometry_clean_status, icon='SOLO_ON' if scene.geometry_clean_status == 'PASSED' else 'SOLO_OFF' if scene.geometry_clean_status == 'FAILED' else 'QUESTION')

            # Triangulation
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Triangulation:")
            split.label(text=scene.triangulation_status, icon='SOLO_ON' if scene.triangulation_status == 'PASSED' else 'SOLO_OFF' if scene.triangulation_status == 'FAILED' else 'QUESTION')

            # Transforms
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Transforms:")
            split.label(text=scene.transform_status, icon='SOLO_ON' if scene.transform_status == 'PASSED' else 'SOLO_OFF' if scene.transform_status == 'FAILED' else 'QUESTION')

            # UV Maps
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="UV Maps:")
            split.label(text=scene.uv_status, icon='SOLO_ON' if scene.uv_status == 'PASSED' else 'SOLO_OFF' if scene.uv_status == 'FAILED' else 'QUESTION')

            # Polygon Counts
            if scene.oks_poly_count != "N/A":
                row = box.row()
                split = row.split(factor=0.7)
                split.label(text=f"OKS: {scene.oks_poly_count} / {POLY_LIMIT_MAIN}")
                split.label(text=scene.oks_status, icon='SOLO_ON' if scene.oks_status == 'PASSED' else 'SOLO_OFF' if scene.oks_status == 'FAILED' else 'QUESTION')

            if scene.ground_poly_count != "N/A":
                row = box.row()
                split = row.split(factor=0.7)
                split.label(text=f"Ground: {scene.ground_poly_count} / {POLY_LIMIT_GROUND}")
                split.label(text=scene.ground_status, icon='SOLO_ON' if scene.ground_status == 'PASSED' else 'SOLO_OFF' if scene.ground_status == 'FAILED' else 'QUESTION')

            if scene.other_poly_count != "N/A":
                row = box.row()
                split = row.split(factor=0.7)
                split.label(text=f"Other: {scene.other_poly_count}")
                split.label(text="", icon='BLANK1')

            # Кнопка для отображения деталей
            row = box.row()
            row.operator("check.show_geometry_details", text="Details", icon='INFO')

        # Раздел Texture and Material
        box = layout.box()
        row = box.row()
        row.prop(scene, "texture_material_enabled", text="Texture and Material", icon='CHECKBOX_HLT' if scene.texture_material_enabled else 'CHECKBOX_DEHLT')
        split = row.split(factor=0.6)
        split.operator("check.texture_and_material", text="Check", icon='PLAY') if scene.texture_material_enabled else split.label(text="Disabled", icon='CANCEL')
        if scene.texture_material_checked:
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Texture Format:")
            split.label(text=scene.texture_format_status, icon='SOLO_ON' if scene.texture_format_status == 'PASSED' else 'SOLO_OFF' if scene.texture_format_status == 'FAILED' else 'QUESTION')
            
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Alpha Channel:")
            split.label(text=scene.alpha_channel_status, icon='SOLO_ON' if scene.alpha_channel_status == 'PASSED' else 'SOLO_OFF' if scene.alpha_channel_status == 'FAILED' else 'QUESTION')
            
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Texture Size:")
            split.label(text=scene.texture_size_status, icon='SOLO_ON' if scene.texture_size_status == 'PASSED' else 'SOLO_OFF' if scene.texture_size_status == 'FAILED' else 'QUESTION')
            
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Glass Material:")
            split.label(text=scene.glass_material_status, icon='SOLO_ON' if scene.glass_material_status == 'PASSED' else 'SOLO_OFF' if scene.glass_material_status == 'FAILED' else 'QUESTION')
            
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Ground Material:")
            split.label(text=scene.ground_material_status, icon='SOLO_ON' if scene.ground_material_status == 'PASSED' else 'SOLO_OFF' if scene.ground_material_status == 'FAILED' else 'QUESTION')

            # Кнопка для отображения деталей
            row = box.row()
            row.operator("check.show_texture_material_details", text="Details", icon='INFO')

        # Раздел Geometry Naming
        box = layout.box()
        row = box.row()
        row.prop(scene, "geometry_naming_enabled", text="Geometry Naming", icon='CHECKBOX_HLT' if scene.geometry_naming_enabled else 'CHECKBOX_DEHLT')
        split = row.split(factor=0.6)
        split.operator("check.geometry_naming", text="Check", icon='PLAY') if scene.geometry_naming_enabled else split.label(text="Disabled", icon='CANCEL')
        if scene.geometry_naming_checked:
            row = box.row()
            split = row.split(factor=0.7)
            split.label(text="Geometry Naming:")
            split.label(text=scene.geometry_naming_status, icon='SOLO_ON' if scene.geometry_naming_status == 'PASSED' else 'SOLO_OFF' if scene.geometry_naming_status == 'FAILED' else 'QUESTION')
            if scene.geometry_naming_status != "Not Checked":
                row = box.row()
                row.operator("check.show_naming_details", text="Details", icon='INFO')

        layout.separator()
        layout.operator("check.run_selected_checks", text="Run Selected Checks", icon='PLAY')
        layout.operator("check.reset_check_results", text="Reset Check Results", icon='X')
        layout.operator("check.cleanup_files", text="Cleanup Files", icon='TRASH')
        layout.operator("check.clear_and_unregister", text="Clear and Unregister", icon='CANCEL')
        
# Регистрация классов
classes = (
    CHECK_OT_GeometryData, CHECK_OT_TextureAndMaterial, CHECK_OT_GeometryNaming,
    CHECK_OT_ShowNamingDetails, CHECK_OT_RunSelectedChecks,
    CHECK_OT_SelectAll, CHECK_OT_ClearSelection, CHECK_OT_ResetCheckResults,
    CHECK_OT_CleanupFiles, CHECK_OT_ClearAndUnregister, CHECK_OT_ImportModel,
    CHECK_OT_ShowGeometryDetails, CHECK_OT_ShowTextureMaterialDetails, CHECK_PT_Panel
)

def register():
    register_scene_properties()
    bpy.utils.register_class(CHECK_PT_Panel)
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    unregister_scene_properties()

# Helper function for Texture Format Check
def check_texture_format():
    issues = []
    passed = True
    has_textures = False
    for img in bpy.data.images:
        if img.source == 'FILE' and img.filepath:
            has_textures = True
            if not img.filepath.lower().endswith('.png'):
                passed = False
                issues.append(f"Texture {img.name} is not PNG")
    if not has_textures:
        return True, ["No textures found to check format."] # Consider PASS if no textures?
    return passed, issues

# Helper function for Alpha Channel Check
def check_alpha_channel():
    issues = []
    passed = True
    has_textures = False
    texture_info = analyze_embedded_textures() # Re-analyze or pass info
    if not texture_info:
        return True, ["No textures found to check alpha channel."]
    for texture_name, info in texture_info.items():
        has_textures = True
        if info.get('has_alpha_channel', False):
            passed = False
            issues.append(f"{texture_name} has alpha channel (mode: {info.get('color_mode', 'N/A')}, bits: {info.get('bits_per_channel', 'N/A')})")
    return passed, issues

# Helper function for Texture Size Check
def check_texture_size():
    issues = []
    passed = True
    has_textures = False
    texture_info = analyze_embedded_textures()
    if not texture_info:
        return True, ["No textures found to check size."]
    for texture_name, info in texture_info.items():
        has_textures = True
        expected_size = (TEXTURE_SIZE_DEFAULT, TEXTURE_SIZE_DEFAULT) if "GroundEl" not in texture_name else (TEXTURE_SIZE_GROUNDEL, TEXTURE_SIZE_GROUNDEL)
        if info.get('size', (0,0)) != expected_size:
            passed = False
            issues.append(f"{texture_name} size is {info.get('size', 'N/A')}, expected {expected_size}")
    return passed, issues

# Helper function for Glass Material Check
def check_glass_material():
    issues = []
    passed = True
    has_glass_objects = False
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and any(glass in obj.name for glass in ['MainGlass', 'GroundGlass', 'GroundElGlass']):
            has_glass_objects = True
            if obj.data.materials:
                if len(obj.data.materials) > 7:
                    passed = False
                    issues.append(f"{obj.name} has {len(obj.data.materials)} materials (max 7 allowed)")
                for mat in obj.data.materials:
                    if mat and mat.node_tree:
                        for node in mat.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                passed = False
                                issues.append(f"{obj.name} material {mat.name} has texture {node.image.name} (textures not allowed for glass)")
    if not has_glass_objects:
        return True, ["No glass objects found."]
    return passed, issues

# Helper function for Ground Material Check
def check_ground_material():
    issues = []
    passed = True
    has_ground_objects = False
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and "Ground" in obj.name and "GroundEl" not in obj.name:
            has_ground_objects = True
            if obj.data.materials and len(obj.data.materials) > 20:
                passed = False
                issues.append(f"{obj.name} has {len(obj.data.materials)} materials (max 20 allowed)")
    if not has_ground_objects:
        return True, ["No ground objects found."]
    return passed, issues

# Точка входа для запуска из командной строки
if __name__ == "__main__":
    import sys
    import json
    import argparse
    import traceback

    print("Запуск model_checker.py из командной строки...")
    print(f"Аргументы: {sys.argv}")

    parser = argparse.ArgumentParser(description="Blender Model Checker CLI")
    
    # Ищем аргументы ПОСЛЕ '--'
    try:
        idx = sys.argv.index("--")
        args_to_parse = sys.argv[idx+1:]
    except ValueError:
        args_to_parse = [] # Если '--' нет, парсим все (или ничего, если нет доп. аргументов)

    print(f"Аргументы для парсинга: {args_to_parse}")

    parser.add_argument("input_path", help="Path to the input FBX file or ZIP archive")
    parser.add_argument("output_path", help="Path to the output JSON results file")
    
    try:
        args = parser.parse_args(args_to_parse)
        input_path = args.input_path
        output_path = args.output_path
        print(f"Input path: {input_path}")
        print(f"Output path: {output_path}")

        results = {}
        fbx_files_list = [] # Инициализируем как пустой список
        textures_list = [] # Инициализируем как пустой список
        is_zip = False
        extracted_dir_for_script = None

        try:
            # Проверяем, существует ли входной путь
            if not os.path.exists(input_path):
                 raise FileNotFoundError(f"Input path does not exist: {input_path}")

            # 1. Обработка входного файла (ZIP или FBX)
            if input_path.lower().endswith('.zip'):
                is_zip = True
                print(f"Обработка ZIP архива: {input_path}")
                # Создаем временную директорию для распаковки рядом с output_path
                extracted_dir_for_script = os.path.join(os.path.dirname(output_path), "extracted_model")
                
                extraction = extract_archive_contents(input_path, extracted_dir_for_script)
                if extraction is None or not extraction[1]:  # Проверяем на ошибку извлечения или отсутствие FBX
                    raise ValueError("Error during archive extraction or no FBX files found.")
                _, fbx_files_list, textures_list = extraction

                print(f"Распаковано {len(fbx_files_list)} FBX файлов в {extracted_dir_for_script}")
                 
            elif input_path.lower().endswith('.fbx'):
                 print(f"Обработка FBX файла: {input_path}")
                 fbx_files_list = [input_path] # FBX файл - это и есть список из одного элемента
                 is_zip = False
            else:
                 raise ValueError(f"Unsupported file type: {input_path}. Only .fbx and .zip are supported.")

            # 2. Импорт FBX в Blender
            print("Очистка сцены и импорт FBX...")
            textures_dir = os.path.join(os.path.dirname(output_path), "textures")
            if not os.path.exists(textures_dir):
                os.makedirs(textures_dir, exist_ok=True)
            import_fbx(fbx_files_list, textures_dir)
            print("Импорт завершен.")

            # 3. Запуск проверок
            print("Запуск проверок...")
            
            # ---- Geometry Data Checks ----
            geometry_results = {}
            # Archive Size
            size_ok, size_msg = check_archive_size(input_path)
            geometry_results['archive_size'] = {
                'status': 'PASSED' if size_ok else 'FAILED',
                'messages': [size_msg]
            }
            # FBX Files (Archive Contents)
            if is_zip:
                contents_ok, _, contents_msg = check_archive_contents(input_path)
            else:
                base_name = os.path.basename(input_path)
                is_ground = re.match(GROUND_FBX_PATTERN, base_name, re.IGNORECASE)
                is_oks = re.match(OKS_FBX_PATTERN, base_name)
                contents_ok = bool(is_ground or is_oks)
                contents_msg = f"Valid FBX name: {base_name}" if contents_ok else f"Invalid FBX name: {base_name}. Expected OKS or Ground pattern."
            geometry_results['fbx_files'] = {
                'status': 'PASSED' if contents_ok else 'FAILED',
                'messages': [contents_msg]
            }
            # Scene Content
            scene_ok, scene_issues = check_scene_contents()
            geometry_results['scene_content'] = {
                'status': 'PASSED' if scene_ok else 'FAILED',
                'messages': scene_issues
            }
            # Ground Drop
            ground_ok, ground_msg = check_ground_drop()
            geometry_results['ground_drop'] = {
                'status': 'PASSED' if ground_ok else 'FAILED',
                'messages': [ground_msg]
            }
            # Geometry Cleanliness
            clean_ok, clean_issues = check_geometry_cleanliness()
            geometry_results['geometry_cleanliness'] = {
                'status': 'PASSED' if clean_ok else 'FAILED',
                'messages': clean_issues
            }
            # Triangulation
            triang_ok, triang_msg = check_triangulation()
            geometry_results['triangulation'] = {
                'status': 'PASSED' if triang_ok else 'FAILED',
                'messages': [triang_msg]
            }
            # Transforms
            trans_ok, trans_issues = check_transforms()
            geometry_results['transforms'] = {
                'status': 'PASSED' if trans_ok else 'FAILED',
                'messages': trans_issues
            }
            # UV Maps
            uv_ok, uv_issues = check_uv_maps()
            geometry_results['uv_maps'] = {
                'status': 'PASSED' if uv_ok else 'FAILED',
                'messages': uv_issues
            }
            # Polygons Count
            oks_count = ground_count = other_count = 0
            for obj in bpy.data.objects:
                if obj.type == 'MESH':
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.mode_set(mode='EDIT')
                    bm = bmesh.from_edit_mesh(obj.data)
                    bm.faces.ensure_lookup_table()
                    tri_count = len([face for face in bm.faces if len(face.verts) == 3])
                    bpy.ops.object.mode_set(mode='OBJECT')
                    if 'Main' in obj.name or 'MainGlass' in obj.name:
                        oks_count += tri_count
                    elif any(s in obj.name for s in ['Ground', 'Flora', 'GroundEl']):
                        ground_count += tri_count
                    else:
                        other_count += tri_count
            poly_status = 'PASSED' if oks_count <= POLY_LIMIT_MAIN and ground_count <= POLY_LIMIT_GROUND else 'FAILED'
            poly_messages = [f"OKS polygon count: {oks_count}/{POLY_LIMIT_MAIN}", f"Ground polygon count: {ground_count}/{POLY_LIMIT_GROUND}"]
            if other_count > 0:
                poly_messages.append(f"Other polygon count: {other_count}")
            geometry_results['polygons'] = {'status': poly_status, 'messages': poly_messages}

            results['geometry_data'] = geometry_results
            
            # ---- Texture & Material Checks ----
            texture_material_results = {}
            
            format_ok, format_issues = check_texture_format()
            texture_material_results['texture_format'] = {
                'status': 'PASSED' if format_ok else 'FAILED',
                'messages': format_issues
            }
            
            alpha_ok, alpha_issues = check_alpha_channel()
            texture_material_results['alpha_channel'] = {
                'status': 'PASSED' if alpha_ok else 'FAILED',
                'messages': alpha_issues
            }
            
            size_ok, size_issues = check_texture_size()
            texture_material_results['texture_size'] = {
                'status': 'PASSED' if size_ok else 'FAILED',
                'messages': size_issues
            }
            
            glass_ok, glass_issues = check_glass_material()
            texture_material_results['glass_material'] = {
                'status': 'PASSED' if glass_ok else 'FAILED',
                'messages': glass_issues
            }
            
            ground_ok, ground_issues = check_ground_material()
            texture_material_results['ground_material'] = {
                'status': 'PASSED' if ground_ok else 'FAILED',
                'messages': ground_issues
            }
            
            results['texture_material'] = texture_material_results
            
             # ---- Naming Checks ----
            texture_analysis_results = analyze_embedded_textures() # Needed for naming checks that require texture info?
            validate_naming_all(texture_analysis_results) # Populates global NAMING_DETAILS
            results['geometry_data'] = geometry_results
            results['texture_material'] = texture_material_results
            results['naming'] = NAMING_DETAILS # Assign the populated global dict
           
            print("Проверки завершены.")

        except Exception as e:
            print(f"ОШИБКА ВО ВРЕМЯ ВЫПОЛНЕНИЯ ПРОВЕРОК: {e}")
            traceback.print_exc()
            results = {"error": str(e), "traceback": traceback.format_exc()}

        finally:
            # 4. Запись результатов в JSON
            try:
                print(f"Запись результатов в {output_path}...")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
                print("Результаты успешно записаны.")
            except Exception as e_write:
                print(f"ОШИБКА ПРИ ЗАПИСИ JSON: {e_write}")
                if not os.path.exists(output_path):
                     try:
                         with open(output_path, 'w', encoding='utf-8') as f_err:
                             error_res = results.get("error", "Unknown error during execution")
                             tb_res = results.get("traceback", "No traceback available")
                             json.dump({"final_error": f"Failed to write results. Original error: {error_res}. Write error: {str(e_write)}", "traceback": tb_res}, f_err, ensure_ascii=False, indent=4)
                     except Exception as e_final_write:
                          print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось записать даже сообщение об ошибке в {output_path}: {e_final_write}")

            # Очистка временной директории распаковки, если она была создана
            if extracted_dir_for_script and os.path.exists(extracted_dir_for_script):
                 try:
                     print(f"Очистка временной директории {extracted_dir_for_script}")
                     shutil.rmtree(extracted_dir_for_script)
                 except Exception as e_clean:
                     print(f"Предупреждение: не удалось очистить временную директорию {extracted_dir_for_script}: {e_clean}")

    except Exception as e_argparse:
         print(f"ОШИБКА ПАРСИНГА АРГУМЕНТОВ: {e_argparse}")
         parser.print_help()
         output_path_on_error = "/output/error_parsing_args.json"
         if 'args' in locals() and hasattr(args, 'output_path'):
              output_path_on_error = args.output_path
         elif len(args_to_parse) >= 2:
              output_path_on_error = args_to_parse[1]
         try:
             os.makedirs(os.path.dirname(output_path_on_error), exist_ok=True)
             with open(output_path_on_error, 'w', encoding='utf-8') as f_err:
                 json.dump({"error": "Argument parsing failed", "details": str(e_argparse)}, f_err, ensure_ascii=False, indent=4)
             print(f"Ошибка парсинга записана в {output_path_on_error}")
         except Exception as e_write_arg_error:
             print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось записать ошибку парсинга аргументов в {output_path_on_error}: {e_write_arg_error}")
         sys.exit(1)

    print("Скрипт model_checker.py завершил работу.")
    # НЕ используйте bpy.ops.wm.quit_blender() здесь, если скрипт запущен с -b,
    # Blender сам завершится после выполнения скрипта.