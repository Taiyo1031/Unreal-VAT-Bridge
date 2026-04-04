import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Collection, Object, PropertyGroup

from . import constants


def _mesh_object_poll(_self, obj):
    return obj is not None and obj.type == "MESH"


class UVB_PG_settings(PropertyGroup):
    export_mode: EnumProperty(
        name="Export Mode",
        items=constants.EXPORT_MODE_ITEMS,
        default=constants.EXPORT_MODE_VERTEX,
    )
    output_directory: StringProperty(
        name="Output Directory",
        subtype="DIR_PATH",
        default="//VAT_Exports",
    )
    show_output_json: BoolProperty(name="Show Exported Metadata JSON", default=False)
    last_exported_json_text: StringProperty(
        name="Exported Metadata JSON",
        description="Last exported JSON payload",
        default="",
        options={"TEXTEDIT_UPDATE"},
    )

    start_frame: IntProperty(name="Start Frame", default=1)
    end_frame: IntProperty(name="End Frame", default=250)
    reference_frame: IntProperty(name="Reference Frame", default=1)
    frame_drop_step: IntProperty(
        name="FPS Dropping",
        default=1,
        min=1,
        soft_max=120,
    )
    uv_map_index: IntProperty(
        name="UV Index",
        default=0,
        min=0,
        max=3,
    )

    vertex_use_collection: BoolProperty(name="Use Collection", default=False)
    vertex_source_object: PointerProperty(
        name="Source Object",
        type=Object,
        poll=_mesh_object_poll,
    )
    vertex_source_collection: PointerProperty(name="Source Collection", type=Collection)

    rbd_source_collection: PointerProperty(name="Source Collection", type=Collection)
    rbd_skip_scale: BoolProperty(
        name="Skip Scale",
        description="Skip scale export when enabled",
        default=False,
    )

    texture_format: EnumProperty(
        name="Texture Format",
        items=constants.TEXTURE_FORMAT_ITEMS,
        default="EXR",
    )
    pixel_type: EnumProperty(
        name="Pixel Type",
        items=constants.PIXEL_TYPE_ITEMS,
        default="HALF_FLOAT",
    )
    compression: EnumProperty(
        name="Compression",
        items=constants.COMPRESSION_ITEMS,
        default="ZIP",
    )
    attribute_output_mode: EnumProperty(
        name="Attribute Output",
        items=constants.ATTRIBUTE_OUTPUT_ITEMS,
        default="SEPARATE_FILES",
    )

    export_json_metadata: BoolProperty(name="Export Metadata JSON", default=False)
    export_mesh_file: BoolProperty(name="Export Mesh File", default=False)

    texture_prefix: StringProperty(name="Texture Prefix", default="T_")
    texture_naming_pattern: StringProperty(
        name="Texture Naming Pattern",
        default="T_{asset_name}_{attribute}",
    )
    keep_temporary_scene: BoolProperty(name="Keep Temporary Scene", default=True)


CLASSES = (UVB_PG_settings,)


def register():
    if hasattr(bpy.types.Scene, "unreal_vat_bridge"):
        try:
            del bpy.types.Scene.unreal_vat_bridge
        except Exception:
            pass
    for cls in CLASSES:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)
    bpy.types.Scene.unreal_vat_bridge = PointerProperty(type=UVB_PG_settings)


def unregister():
    if hasattr(bpy.types.Scene, "unreal_vat_bridge"):
        del bpy.types.Scene.unreal_vat_bridge
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
