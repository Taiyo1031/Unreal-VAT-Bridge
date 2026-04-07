import bpy
from bpy.types import Panel

from . import constants, preview


def _get_export_mode(settings):
    mode = str(getattr(settings, "export_mode", constants.EXPORT_MODE_VERTEX))
    export_mode_rbd = getattr(constants, "EXPORT_MODE_RBD", "RBD")
    if mode in ("RIGID", export_mode_rbd):
        return export_mode_rbd
    return mode

def _get_rbd_skip_scale(settings):
    return bool(getattr(settings, "rbd_skip_scale", getattr(settings, "skip_scale", False)))


def _is_export_mode_enum_stale(settings):
    try:
        prop = settings.bl_rna.properties.get("export_mode")
        if prop is None:
            return False
        identifiers = {item.identifier for item in prop.enum_items}
        return "RBD" not in identifiers
    except Exception:
        return False


def _get_export_summary_text(settings):
    if _get_export_mode(settings) == constants.EXPORT_MODE_RBD:
        return "Exports Position only, plus optional JSON and FBX."
    return "Exports Position + Normal, plus optional JSON and FBX."


class UVB_PT_main(Panel):
    bl_idname = "UVB_PT_main"
    bl_label = constants.ADDON_NAME
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = constants.PANEL_CATEGORY

    def draw(self, context):
        layout = self.layout
        settings = context.scene.unreal_vat_bridge

        version_row = layout.row()
        version_row.scale_y = 0.65
        version_row.label(text=constants.UI_VERSION_LABEL)

        mode_box = layout.box()
        mode_box.label(text="Mode")
        mode_box.prop(settings, "export_mode", text="Export Mode")
        if _is_export_mode_enum_stale(settings):
            mode_box.label(text="Export Mode enum is stale. Disable/enable addon.", icon="ERROR")
        if hasattr(settings, "use_abc_import"):
            mode_box.prop(settings, "use_abc_import", text="Use ABC Import")
        else:
            mode_box.label(text="Use ABC Import: unavailable (reload addon)")

        source_box = layout.box()
        source_box.label(text="Source")
        if _get_export_mode(settings) == constants.EXPORT_MODE_VERTEX:
            has_vertex_use_collection = hasattr(settings, "vertex_use_collection")
            use_collection = bool(getattr(settings, "vertex_use_collection", False))

            if has_vertex_use_collection:
                source_box.prop(settings, "vertex_use_collection", text="Use Source Collection")
            else:
                source_box.label(text="Use Source Collection: unavailable (reload addon)")

            if use_collection and hasattr(settings, "vertex_source_collection"):
                source_box.prop(settings, "vertex_source_collection", text="Source Collection")
            else:
                source_box.prop(settings, "vertex_source_object", text="Source Object")
        else:
            if hasattr(settings, "rbd_source_collection"):
                source_box.prop(settings, "rbd_source_collection", text="Source Collection")
            else:
                source_box.prop(settings, "rigid_source_collection", text="Source Collection")

        animation_box = layout.box()
        animation_box.label(text="Animation")
        animation_box.prop(settings, "start_frame", text="Start Frame")
        animation_box.prop(settings, "end_frame", text="End Frame")
        animation_box.prop(settings, "reference_frame", text="Reference Frame")


        source_fps = preview.get_source_fps(context.scene)
        source_fps_row = animation_box.row()
        source_fps_row.enabled = False
        source_fps_row.label(text=f"Source FPS: {source_fps:.3f}")

        if hasattr(settings, "frame_drop_step"):
            animation_box.prop(settings, "frame_drop_step", text="Frame Step")
        else:
            animation_box.label(text="Frame Step: unavailable (reload addon)")

        output_box = layout.box()
        output_box.label(text="Output")
        output_box.prop(settings, "output_directory", text="Output Folder")
        output_box.prop(settings, "export_json_metadata", text="Export JSON")
        output_box.prop(settings, "export_mesh_file", text="Export FBX")

        es_box = layout.box()
        es_box.label(text="Export Settings")
        if _get_export_mode(settings) == constants.EXPORT_MODE_VERTEX:
            if hasattr(settings, "uv_map_index"):
                es_box.prop(settings, "uv_map_index", text="UV Index")
            else:
                es_box.label(text="UV Index: 0 (reload addon)")

        if _get_export_mode(settings) == constants.EXPORT_MODE_RBD:

            if hasattr(settings, "rbd_skip_scale"):
                es_box.prop(settings, "rbd_skip_scale", text="Skip Scale")
            else:
                es_box.label(text=f"Skip Scale: {_get_rbd_skip_scale(settings)} (reload addon)")

            es_box.separator(factor=0.5)

            if hasattr(settings, "rbd_packing_method"):
                es_box.label(text="Packing Method")
                es_box.row().prop(settings, "rbd_packing_method", expand=True)
                packing_method = str(getattr(settings, "rbd_packing_method", constants.RBD_PACKING_METHOD_UV))
            else:
                es_box.label(text="Packing Method: unavailable (reload addon)")
                packing_method = constants.RBD_PACKING_METHOD_UV

            rbd_collection = getattr(settings, "rbd_source_collection", None)
            obj_count = len(rbd_collection.objects) if rbd_collection else 0
            count_row = es_box.row()
            count_row.enabled = False
            count_row.label(text=f"Objects in collection: {obj_count}")

            if packing_method == constants.RBD_PACKING_METHOD_VERTEX_COLOR:
                limit_row = es_box.row()
                limit_row.enabled = False
                if obj_count > 1020:
                    limit_row.alert = True
                    limit_row.label(text=f"Vertex Color Packing: max 1020 objects (current: {obj_count})", icon="ERROR")
                else:
                    limit_row.label(text="Vertex Color Packing: max 1020 objects")

            if packing_method == constants.RBD_PACKING_METHOD_UV:
                if hasattr(settings, "uv_map_index"):
                    es_box.prop(settings, "uv_map_index", text="UV Index")
                else:
                    es_box.label(text="UV Index: 0 (reload addon)")

        export_box = layout.box()
        export_box.label(text="Export")
        export_box.label(text=_get_export_summary_text(settings))
        button_row = export_box.row(align=True)
        button_row.scale_y = 1.25
        export_button = button_row.row(align=True)
        export_button.scale_x = 2.5
        export_button.operator("unreal_vat_bridge.export", text="Export")
        copy_button = button_row.row(align=True)
        copy_button.enabled = bool(getattr(settings, "last_exported_json_text", "").strip())
        copy_button.operator("unreal_vat_bridge.copy_last_json", text="Copy JSON")



class UVB_PT_advanced(Panel):
    bl_idname = "UVB_PT_advanced"
    bl_label = "Advanced"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = constants.PANEL_CATEGORY
    bl_parent_id = UVB_PT_main.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.unreal_vat_bridge
        layout.prop(settings, "texture_prefix", text="Texture Prefix")
        layout.prop(settings, "texture_naming_pattern", text="Texture Naming Pattern")


class UVB_PT_debug(Panel):
    bl_idname = "UVB_PT_debug"
    bl_label = "Debug"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = constants.PANEL_CATEGORY
    bl_parent_id = UVB_PT_main.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.unreal_vat_bridge

        if hasattr(settings, "keep_temporary_scene"):
            layout.prop(settings, "keep_temporary_scene", text="Keep Temporary Scene")
        else:
            layout.label(text="Keep Temporary Scene: unavailable (reload addon)", icon="ERROR")
        layout.label(text="On keeps temp data for debug. Off removes temp scene and related data.")


CLASSES = (
    UVB_PT_main,
    UVB_PT_advanced,
    UVB_PT_debug,
)


def register():
    for cls in CLASSES:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
