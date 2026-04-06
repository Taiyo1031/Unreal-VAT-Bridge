import importlib
import traceback

import bpy
from bpy.types import Operator

from . import constants as constants_module
from . import preview as preview_module
from . import workflow as workflow_module


def _tag_progress_redraw(context):
    window_manager = getattr(context, "window_manager", None)
    if window_manager is None:
        return

    for window in getattr(window_manager, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []):
            if getattr(area, "type", "") in {"STATUSBAR", "TOPBAR", "VIEW_3D"}:
                try:
                    area.tag_redraw()
                except Exception:
                    pass

    # Force an immediate redraw so the footer status text becomes visible
    # during blocking execute(). wm.progress_begin/end update the OS cursor
    # directly and bypass this, but status_text_set needs a redraw flush.
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:
        pass


class _FooterProgress:
    def __init__(self, context):
        self._context = context
        self._window_manager = getattr(context, "window_manager", None)
        self._workspace = getattr(context, "workspace", None)
        self._started = False

    def begin(self, message="Starting export..."):
        if self._window_manager is not None:
            try:
                self._window_manager.progress_begin(0, 100)
                self._started = True
            except Exception:
                self._started = False
        self.update(0.0, message)

    def update(self, percent, message=None):
        clamped = max(0.0, min(100.0, float(percent)))
        if self._window_manager is not None and self._started:
            try:
                self._window_manager.progress_update(clamped)
            except Exception:
                pass

        if self._workspace is not None and hasattr(self._workspace, "status_text_set"):
            try:
                if message:
                    self._workspace.status_text_set(
                        f"Unreal VAT Bridge: {int(round(clamped))}% - {message}"
                    )
                else:
                    self._workspace.status_text_set(
                        f"Unreal VAT Bridge: {int(round(clamped))}%"
                    )
            except Exception:
                pass

        _tag_progress_redraw(self._context)

    def end(self):
        if self._window_manager is not None and self._started:
            try:
                self._window_manager.progress_end()
            except Exception:
                pass
        if self._workspace is not None and hasattr(self._workspace, "status_text_set"):
            try:
                self._workspace.status_text_set(None)
            except Exception:
                pass
        _tag_progress_redraw(self._context)


class UVB_OT_export(Operator):
    bl_idname = "unreal_vat_bridge.export"
    bl_label = "Export"
    bl_description = "Run the Unreal VAT Bridge export workflow"

    def execute(self, context):
        settings = context.scene.unreal_vat_bridge
        footer_progress = _FooterProgress(context)
        footer_progress.begin("Reloading export workflow")

        try:
            try:
                importlib.reload(constants_module)
                importlib.reload(preview_module)
                workflow = importlib.reload(workflow_module)
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"}, f"Failed to reload workflow module: {exc}")
                return {"CANCELLED"}

            try:
                result = workflow.run_export(
                    context,
                    settings,
                    reporter=self.report,
                    progress_callback=footer_progress.update,
                )
            except workflow.ExportWorkflowError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"}, f"Unexpected export error: {exc}")
                return {"CANCELLED"}
        finally:
            footer_progress.end()

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
