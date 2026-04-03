bl_info = {
    "name": "Unreal VAT Bridge",
    "author": "Taiyo",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Unreal VAT Bridge",
    "description": "UI-first Blender-to-Unreal VAT export bridge.",
    "category": "Import-Export",
}

import importlib

from . import operators, properties, ui

if "bpy" in locals():
    properties = importlib.reload(properties)
    operators = importlib.reload(operators)
    ui = importlib.reload(ui)


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()
