import importlib
import traceback

import bpy
from bpy.types import Operator

from . import workflow as workflow_module


class UVB_OT_export(Operator):
    bl_idname = "unreal_vat_bridge.export"
    bl_label = "Export"
    bl_description = "Run the Unreal VAT Bridge export workflow"

    def execute(self, context):
        settings = context.scene.unreal_vat_bridge

        try:
            workflow = importlib.reload(workflow_module)
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Failed to reload workflow module: {exc}")
            return {"CANCELLED"}

        try:
            result = workflow.run_export(context, settings, reporter=self.report)
        except workflow.ExportWorkflowError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Unexpected export error: {exc}")
            return {"CANCELLED"}

        message = f"Export completed. Position: {result.position_texture}"
        if result.normal_texture:
            message += f", Normal: {result.normal_texture}"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class UVB_OT_copy_last_json(Operator):
    bl_idname = "unreal_vat_bridge.copy_last_json"
    bl_label = "Copy JSON"
    bl_description = "Copy last exported JSON to clipboard"

    def execute(self, context):
        settings = context.scene.unreal_vat_bridge
        json_text = (getattr(settings, "last_exported_json_text", "") or "").strip()
        if not json_text:
            self.report({"WARNING"}, "No exported JSON is available to copy.")
            return {"CANCELLED"}

        context.window_manager.clipboard = json_text
        self.report({"INFO"}, "Exported JSON copied to clipboard.")
        return {"FINISHED"}


CLASSES = (
    UVB_OT_export,
    UVB_OT_copy_last_json,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
