import bpy
import os

# Очищаем сцену
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Создаем простой куб
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))

# Экспортируем в FBX
bpy.ops.export_scene.fbx(filepath='/data/test.fbx') 