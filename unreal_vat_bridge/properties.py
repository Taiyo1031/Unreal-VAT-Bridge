import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Collection, Object, PropertyGroup

from . import constants


def _mesh_object_poll(_self, obj):
    return obj is not None and obj.type == "MESH"


class UVB_PG_settings(PropertyGroup):
    export_mode: EnumProperty(
        name="Export Mode",
        description="Choose the VAT workflow: Vertex or RBD",
        items=constants.EXPORT_MODE_ITEMS,
        default=constants.EXPORT_MODE_VERTEX,
    )
    use_abc_import: BoolProperty(
        name="Use ABC Import",
        description=(
            "Export source objects to a temporary Alembic file and re-import them into the "
            "compute scene. Imported objects use MESH_SEQUENCE_CACHE so GN evaluates them "
            "frame-by-frame correctly. Disable to use the legacy direct object-copy path"
        ),
        default=True,
    )
    output_directory: StringProperty(
        name="Output Folder",
        description="Folder where exported textures, metadata, and meshes are written",
        subtype="DIR_PATH",
        default="//VAT_Exports",
    )
    last_exported_json_text: StringProperty(
        name="Exported Metadata JSON",
        description="Last exported JSON payload",
        default="",
        options={"TEXTEDIT_UPDATE"},
    )

    start_frame: IntProperty(
        name="Start Frame",
        description="First frame included in the VAT export",
        default=1,
    )
    end_frame: IntProperty(
        name="End Frame",
        description="Last frame included in the VAT export",
        default=250,
    )
    reference_frame: IntProperty(
        name="Reference Frame",
        description="Frame used as the static reference pose for exported data",
        default=1,
    )
    frame_drop_step: IntProperty(
        name="Frame Step",
        description="Sample every Nth frame. A value of 1 exports every frame",
        default=1,
        min=1,
        soft_max=120,
    )
    uv_map_index: IntProperty(
        name="UV Index",
        description="UV channel index used for VAT lookup. Valid range is 0 to 3",
        default=0,
        min=0,
        max=3,
    )

    vertex_use_collection: BoolProperty(
        name="Use Source Collection",
        description="Use a collection as the Vertex mode source instead of a single object",
        default=False,
    )
    vertex_source_object: PointerProperty(
        name="Source Object",
        description="Mesh object used as the VAT source in Vertex mode",
        type=Object,
        poll=_mesh_object_poll,
    )
    vertex_source_collection: PointerProperty(
        name="Source Collection",
        description="Collection used as the VAT source in Vertex mode when enabled",
        type=Collection,
    )

    rbd_source_collection: PointerProperty(
        name="Source Collection",
        description="Collection used as the VAT source in RBD mode",
        type=Collection,
    )
    rbd_skip_scale: BoolProperty(
        name="Skip Scale",
        description="Skip exporting scale data in RBD mode",
        default=False,
    )
    rbd_packing_method: EnumProperty(
        name="Packing Method",
        description="How per-object ID data is packed into the VAT texture in RBD mode",
        items=constants.RBD_PACKING_METHOD_ITEMS,
        default=constants.RBD_PACKING_METHOD_UV,
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

    export_json_metadata: BoolProperty(
        name="Export JSON",
        description="Write metadata JSON next to the exported textures",
        default=False,
    )
    export_mesh_file: BoolProperty(
        name="Export FBX",
        description="Export the helper mesh as FBX after Geometry Nodes evaluation",
        default=False,
    )

    texture_prefix: StringProperty(
        name="Texture Prefix",
        description="Prefix used when naming exported textures",
        default="T_",
    )
    texture_naming_pattern: StringProperty(
        name="Texture Naming Pattern",
        description="Pattern used to build texture file names",
        default="T_{asset_name}_{attribute}",
    )
    keep_temporary_scene: BoolProperty(
        name="Keep Temporary Scene",
        description="Keep the temporary export scene and related data blocks for debugging",
        default=True,
    )


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
