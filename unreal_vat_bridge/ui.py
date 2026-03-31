import bpy
from bpy.types import Panel

from . import constants, preview


class UVB_PT_main(Panel):
    bl_idname = "UVB_PT_main"
    bl_label = constants.ADDON_NAME
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = constants.PANEL_CATEGORY

    def draw(self, context):
        layout = self.layout
        settings = context.scene.unreal_vat_bridge

        mode_box = layout.box()
        mode_box.label(text="Mode")
        mode_box.prop(settings, "export_mode", text="Export Mode")

        source_box = layout.box()
        source_box.label(text="Source")
        if settings.export_mode == constants.EXPORT_MODE_VERTEX:
            has_vertex_use_collection = hasattr(settings, "vertex_use_collection")
            use_collection = bool(getattr(settings, "vertex_use_collection", False))

            if has_vertex_use_collection:
                source_box.prop(settings, "vertex_use_collection", text="Use Collection")
            else:
                source_box.label(text="Use Collection: unavailable (reload addon)")

            if use_collection and hasattr(settings, "vertex_source_collection"):
                source_box.prop(settings, "vertex_source_collection", text="Source Collection")
            else:
                source_box.prop(settings, "vertex_source_object", text="Source Object")
        else:
            source_box.prop(settings, "rigid_source_type", text="Source Type")
            if settings.rigid_source_type == constants.RIGID_SOURCE_TYPE_SINGLE:
                source_box.prop(settings, "rigid_source_object", text="Source Object")
            else:
                source_box.prop(settings, "rigid_source_collection", text="Source Collection")

        frame_box = layout.box()
        frame_box.label(text="Frame Settings")
        frame_box.prop(settings, "start_frame", text="Start Frame")
        frame_box.prop(settings, "end_frame", text="End Frame")
        frame_box.prop(settings, "reference_frame", text="Reference Frame")
        source_fps = preview.get_source_fps(context.scene)
        source_fps_row = frame_box.row()
        source_fps_row.enabled = False
        source_fps_row.label(text=f"Source FPS: {source_fps:.3f}")

        if hasattr(settings, "frame_drop_step"):
            frame_box.prop(settings, "frame_drop_step", text="FPS Dropping")
        else:
            frame_box.label(text="FPS Dropping: unavailable (reload addon)")

        output_box = layout.box()
        output_box.label(text="Output")
        output_box.prop(settings, "output_directory", text="Output Directory")

        json_header = output_box.row(align=True)
        json_icon = "TRIA_DOWN" if settings.show_output_json else "TRIA_RIGHT"
        json_header.prop(settings, "show_output_json", text="", emboss=False, icon=json_icon)
        json_header.label(text="Exported JSON")

        if settings.show_output_json:
            json_box = output_box.box()
            if settings.last_exported_json_text:
                json_box.prop(settings, "last_exported_json_text", text="")
                json_box.operator("unreal_vat_bridge.copy_last_json", text="Copy JSON")
            else:
                json_box.label(text="No exported JSON available.")

        image_box = layout.box()
        image_box.label(text="Image Export Settings")
        texture_row = image_box.row()
        texture_row.enabled = False
        texture_row.prop(settings, "texture_format", text="Texture Format")
        pixel_row = image_box.row()
        pixel_row.enabled = False
        pixel_row.prop(settings, "pixel_type", text="Pixel Type")
        compression_row = image_box.row()
        compression_row.enabled = False
        compression_row.prop(settings, "compression", text="Compression")
        attribute_row = image_box.row()
        attribute_row.enabled = False
        attribute_row.prop(settings, "attribute_output_mode", text="Attribute Output")

        options_box = layout.box()
        options_box.label(text="Export Options")
        options_box.prop(settings, "export_json_metadata", text="Export JSON Metadata")
        options_box.prop(settings, "export_mesh_file", text="Export Mesh File")

        summary_box = options_box.box()
        summary_box.label(text="Format Summary")
        summary_box.label(text="Texture Format: EXR")
        summary_box.label(text="Default Pixel Type: Half Float")
        summary_box.label(text="Default Compression: ZIP")
        summary_box.label(text="Attribute Textures: Separate Files")

        action_box = layout.box()
        action_box.label(text="Export Action")
        action_box.operator("unreal_vat_bridge.export", text="Export")

        future_box = layout.box()
        future_box.label(text="Future Panels Placeholder")
        future_box.label(text="Custom Attributes panel is reserved for future attributes.")


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
        keep_row = layout.row()
        keep_row.enabled = False
        keep_row.prop(settings, "keep_temporary_scene", text="Keep Temporary Scene")
        layout.label(text="Temporary scene is currently retained for debug.")


class UVB_PT_custom_attributes(Panel):
    bl_idname = "UVB_PT_custom_attributes"
    bl_label = "Custom Attributes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = constants.PANEL_CATEGORY
    bl_parent_id = UVB_PT_main.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Future extension area for custom attributes.")
        layout.label(text="Built-in exports currently: Position, Normal.")


CLASSES = (
    UVB_PT_main,
    UVB_PT_advanced,
    UVB_PT_custom_attributes,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
