from __future__ import annotations

import glob
import json
import os
import time
import uuid
from dataclasses import dataclass

import bpy

from . import constants, preview

UVB_RUNTIME_NODE_GROUP_PREFIX = "UVB_Runtime_"
UVB_WORKFLOW_BUILD_ID = "uvb-workflow-2026-04-04-rbd-showalph-v30"
WHAT_TO_SHOW_POSITION = "Position"
WHAT_TO_SHOW_NORMAL = "Normal/UVOffset"
WHAT_TO_SHOW_MENU_INDEX = {
    WHAT_TO_SHOW_POSITION: 0,
    WHAT_TO_SHOW_NORMAL: 1,
}

class ExportWorkflowError(RuntimeError):
    pass


@dataclass
class PreparedExport:
    output_directory: str
    source_objects: list[bpy.types.Object]
    source_kind: str
    asset_name: str
    use_collection: bool
    source_object: bpy.types.Object | None
    source_collection: bpy.types.Collection | None
    source_fps: float
    frame_drop_step: int
    node_group_name: str
    node_group: bpy.types.NodeTree
    export_texture_width: int
    export_texture_height: int


@dataclass
class TempSceneState:
    scene: bpy.types.Scene
    camera_object: bpy.types.Object
    camera_data: bpy.types.Camera
    helper_object: bpy.types.Object
    helper_mesh: bpy.types.Mesh
    gn_modifier: bpy.types.NodesModifier
    source_copy_objects: list[bpy.types.Object]
    source_collection_for_gn: bpy.types.Collection | None
    source_object_for_gn: bpy.types.Object | None
    reference_object: bpy.types.Object | None
    reference_collection: bpy.types.Collection | None
    selected_uv_name: str


@dataclass
class ExportResult:
    position_texture: str
    normal_texture: str | None
    json_path: str | None
    mesh_path: str | None


def _report_progress(progress_callback, percent, message):
    if progress_callback is None:
        return
    try:
        progress_callback(float(percent), str(message))
    except Exception:
        pass


def run_export(context, settings, reporter=None, progress_callback=None):
    report = reporter if reporter else (lambda _level, _message: None)
    temp_state = None
    completed_successfully = False

    if hasattr(settings, "last_exported_json_text"):
        settings.last_exported_json_text = ""

    try:
        _report_progress(progress_callback, 1, "Starting export")
        report({"INFO"}, f"Workflow build: {UVB_WORKFLOW_BUILD_ID} ({__file__})")
        _report_progress(progress_callback, 4, "Clearing runtime cache")
        report({"INFO"}, "Clearing previous runtime node-group cache...")
        _purge_runtime_node_groups(report)

        _report_progress(progress_callback, 10, "Validating export settings")
        report({"INFO"}, "Validating export settings...")
        prepared = _validate_and_prepare(context, settings, report)

        _report_progress(progress_callback, 22, "Creating temporary compute scene")
        report({"INFO"}, "Creating temporary compute scene...")
        temp_state = _create_temporary_scene(context, prepared, settings, report)

        _report_progress(progress_callback, 34, "Applying Geometry Nodes inputs")
        report({"INFO"}, "Applying UI values to Geometry Nodes transfer layer...")
        _apply_ui_values_to_gn_transfer_layer(temp_state, prepared, settings, report)

        _report_progress(progress_callback, 42, "Baking Geometry Nodes animation")
        report({"INFO"}, "Running Geometry Nodes animation bake...")
        _bake_geometry_nodes_animation_node(context, temp_state, settings, report)

        _report_progress(progress_callback, 62, "Collecting end-frame export data")
        report({"INFO"}, "Moving playhead to End Frame and collecting end-frame export settings...")
        _apply_end_frame_export_settings(
            context,
            temp_state,
            prepared,
            settings,
            int(settings.end_frame),
            report,
        )

        _report_progress(progress_callback, 76, "Exporting textures")
        if _is_rbd_mode(settings):
            report({"INFO"}, "Exporting RBD position texture...")
        else:
            report({"INFO"}, "Exporting Position/Normal textures...")
        position_texture, normal_texture = _export_rendered_textures(context, prepared, temp_state, settings, report)

        json_path = None
        if settings.export_json_metadata:
            _report_progress(progress_callback, 88, "Exporting JSON metadata")
            report({"INFO"}, "Exporting JSON metadata...")
            json_path, json_text = _export_json_metadata(
                context,
                prepared,
                temp_state,
                settings,
                position_texture,
                normal_texture,
            )
            if hasattr(settings, "last_exported_json_text"):
                settings.last_exported_json_text = json_text

        mesh_path = None
        if settings.export_mesh_file:
            _report_progress(progress_callback, 94, "Exporting FBX mesh")
            report({"INFO"}, "Exporting FBX mesh...")
            mesh_path = _export_mesh_fbx(context, prepared, temp_state, settings, reporter=report)

        result = ExportResult(
            position_texture=position_texture,
            normal_texture=normal_texture,
            json_path=json_path,
            mesh_path=mesh_path,
        )
        completed_successfully = True
        report({"INFO"}, "Export workflow completed.")
        return result
    except ExportWorkflowError:
        raise
    except Exception as exc:
        raise ExportWorkflowError(f"Export failed: {exc}") from exc
    finally:
        if temp_state:
            if bool(getattr(settings, "keep_temporary_scene", True)):
                if completed_successfully:
                    _report_progress(progress_callback, 100, "Export completed")
                report({"INFO"}, "Temporary compute scene retained for debugging.")
            else:
                _report_progress(progress_callback, 99, "Cleaning temporary scene")
                try:
                    cleanup_summary = _cleanup_temporary_scene(temp_state)
                    report({"INFO"}, cleanup_summary)
                    if completed_successfully:
                        _report_progress(progress_callback, 100, "Export completed")
                except Exception as cleanup_exc:
                    report({"WARNING"}, f"Failed to fully clean temporary compute scene: {cleanup_exc}")
        elif completed_successfully:
            _report_progress(progress_callback, 100, "Export completed")


def _validate_and_prepare(context, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    if settings.end_frame < settings.start_frame:
        raise ExportWorkflowError("End Frame must be greater than or equal to Start Frame.")

    if settings.reference_frame < settings.start_frame or settings.reference_frame > settings.end_frame:
        raise ExportWorkflowError("Reference Frame must be inside the Start/End Frame range.")

    output_directory = (settings.output_directory or "").strip()
    if not output_directory:
        raise ExportWorkflowError("Output Directory is required.")

    output_directory = bpy.path.abspath(output_directory)
    os.makedirs(output_directory, exist_ok=True)
    if not os.path.isdir(output_directory):
        raise ExportWorkflowError("Output Directory is invalid.")

    (
        source_objects,
        source_kind,
        asset_name,
        use_collection,
        source_object,
        source_collection,
    ) = _resolve_source_selection(context.scene, settings)

    node_group_name = _get_node_group_name_for_mode(settings)
    node_group = _load_fresh_node_group_from_library(node_group_name, report)

    source_fps = preview.get_source_fps(context.scene)
    frame_drop_step = preview.get_frame_drop_step(settings)
    preview_estimate = preview.build_preview(settings)

    return PreparedExport(
        output_directory=output_directory,
        source_objects=source_objects,
        source_kind=source_kind,
        asset_name=asset_name,
        use_collection=use_collection,
        source_object=source_object,
        source_collection=source_collection,
        source_fps=source_fps,
        frame_drop_step=frame_drop_step,
        node_group_name=node_group_name,
        node_group=node_group,
        export_texture_width=int(preview_estimate.texture_width),
        export_texture_height=int(preview_estimate.texture_height),
    )


def _resolve_source_selection(scene, settings):
    if _get_export_mode(settings) == constants.EXPORT_MODE_VERTEX:
        use_collection = bool(getattr(settings, "vertex_use_collection", False))
        if use_collection:
            collection = getattr(settings, "vertex_source_collection", None)
            if collection is None:
                raise ExportWorkflowError("Source Collection is required when Use Collection is enabled.")

            source_objects = [
                obj for obj in collection.objects
                if obj.name in scene.objects and obj.type == "MESH"
            ]
            if not source_objects:
                raise ExportWorkflowError("Source Collection must contain mesh objects in the active scene.")

            return source_objects, "COLLECTION", collection.name, True, None, collection

        obj = getattr(settings, "vertex_source_object", None)
        if obj is None:
            raise ExportWorkflowError("Source Object is required in Vertex mode.")
        if obj.type != "MESH":
            raise ExportWorkflowError("Source Object must be a mesh in Vertex mode.")
        if obj.name not in scene.objects:
            raise ExportWorkflowError("Vertex Source Object must exist in the active scene.")
        return [obj], "OBJECT", obj.name, False, obj, None

    collection = _get_rbd_source_collection(settings)
    if collection is None:
        raise ExportWorkflowError("Source Collection is required in RBD mode.")
    source_objects = [obj for obj in collection.objects if obj.name in scene.objects]
    if not source_objects:
        raise ExportWorkflowError("Source Collection has no objects in the active scene.")
    return source_objects, "COLLECTION", collection.name, True, None, collection


def _create_temporary_scene(context, prepared, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    uv_map_index = _get_uv_map_index(settings)

    scene_name = constants.TEMP_SCENE_BASENAME
    temp_scene = _create_or_overwrite_named_scene(scene_name)
    temp_scene.render.resolution_x = int(prepared.export_texture_width)
    temp_scene.render.resolution_y = int(prepared.export_texture_height)
    temp_scene.render.resolution_percentage = 100

    camera_data = bpy.data.cameras.new(f"{scene_name}_CameraData")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 1.0
    camera_object = bpy.data.objects.new(constants.TEMP_CAMERA_NAME, camera_data)
    camera_object.location = (0.0, 0.0, 10.0)
    temp_scene.collection.objects.link(camera_object)
    temp_scene.camera = camera_object

    source_copy_objects, source_object_for_gn, source_collection_for_gn = _duplicate_sources_into_compute_scene(
        temp_scene,
        prepared,
    )
    selected_uv_name = _prepare_temp_source_uv_layers(
        source_copy_objects,
        uv_map_index,
        reporter=report,
    )

    reference_object = None
    reference_collection = None
    if _is_rbd_mode(settings):
        report({"INFO"}, "Creating static reference-frame collection...")
        reference_collection = _create_reference_frame_collection(
            context,
            temp_scene,
            source_copy_objects,
            int(settings.reference_frame),
        )
        _ensure_uv_layer_on_mesh_objects_in_collection(reference_collection, uv_map_index, selected_uv_name)
    else:
        report({"INFO"}, "Creating static reference-frame mesh object...")
        reference_object = _create_reference_frame_object(
            context,
            temp_scene,
            source_copy_objects,
            int(settings.reference_frame),
        )
        _ensure_mesh_object_uv_layer(reference_object, uv_map_index, selected_uv_name)

    helper_mesh = bpy.data.meshes.new(f"{scene_name}_HelperPlaneMesh")
    helper_mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    helper_mesh.update()
    helper_object = bpy.data.objects.new(constants.TEMP_HELPER_PLANE_NAME, helper_mesh)
    temp_scene.collection.objects.link(helper_object)

    gn_modifier = helper_object.modifiers.new(constants.TEMP_GN_MODIFIER_NAME, "NODES")
    gn_modifier.node_group = prepared.node_group

    return TempSceneState(
        scene=temp_scene,
        camera_object=camera_object,
        camera_data=camera_data,
        helper_object=helper_object,
        helper_mesh=helper_mesh,
        gn_modifier=gn_modifier,
        source_copy_objects=source_copy_objects,
        source_collection_for_gn=source_collection_for_gn,
        source_object_for_gn=source_object_for_gn,
        reference_object=reference_object,
        reference_collection=reference_collection,
        selected_uv_name=selected_uv_name,
    )


def _duplicate_sources_into_compute_scene(temp_scene, prepared):
    source_copy_objects = []
    source_collection_for_gn = None

    for source in prepared.source_objects:
        obj_copy = source.copy()
        if source.data is not None:
            obj_copy.data = source.data.copy()
        obj_copy.matrix_world = source.matrix_world.copy()
        temp_scene.collection.objects.link(obj_copy)
        source_copy_objects.append(obj_copy)

    if not source_copy_objects:
        raise ExportWorkflowError("No source objects were copied into VAT_Compute_Scene.")

    source_object_for_gn = None
    if prepared.source_object:
        for obj_copy in source_copy_objects:
            if obj_copy.name.startswith(prepared.source_object.name):
                source_object_for_gn = obj_copy
                break

    if not prepared.use_collection and source_object_for_gn is None:
        source_object_for_gn = source_copy_objects[0]

    if prepared.use_collection:
        collection_name = _unique_name("VAT_SourceCollection", bpy.data.collections.keys())
        source_collection_for_gn = bpy.data.collections.new(collection_name)
        temp_scene.collection.children.link(source_collection_for_gn)
        for obj_copy in source_copy_objects:
            source_collection_for_gn.objects.link(obj_copy)

    return source_copy_objects, source_object_for_gn, source_collection_for_gn


def _prepare_temp_source_uv_layers(source_objects, uv_layer_index, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    mesh_objects = [obj for obj in source_objects if obj is not None and obj.type == "MESH" and getattr(obj, "data", None) is not None]
    if not mesh_objects:
        raise ExportWorkflowError("No mesh objects were available to prepare UV layers for export.")

    uv_layer_index = max(0, int(uv_layer_index))
    canonical_name = _ensure_mesh_object_uv_layer(mesh_objects[0], uv_layer_index)
    for obj in mesh_objects[1:]:
        _ensure_mesh_object_uv_layer(obj, uv_layer_index, canonical_name)

    report(
        {"INFO"},
        f"Using UV index {uv_layer_index} with UV name '{canonical_name}' for Geometry Nodes export.",
    )
    return canonical_name


def _ensure_uv_layer_on_mesh_objects_in_collection(collection, uv_layer_index, preferred_name=None):
    if collection is None:
        return
    for obj in collection.objects:
        if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        _ensure_mesh_object_uv_layer(obj, uv_layer_index, preferred_name)


def _ensure_mesh_object_uv_layer(obj, uv_layer_index, preferred_name=None):
    if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        raise ExportWorkflowError("A mesh object is required when preparing UV layers.")

    uv_layers = getattr(obj.data, "uv_layers", None)
    if uv_layers is None:
        raise ExportWorkflowError(f"Object '{obj.name}' does not support UV layers.")

    uv_layer_index = max(0, int(uv_layer_index))
    while len(uv_layers) <= uv_layer_index:
        created = uv_layers.new(name=_default_uv_layer_name(len(uv_layers)))
        if created is None:
            raise ExportWorkflowError(
                f"Failed to create UV layer {len(uv_layers)} on object '{obj.name}'."
            )

    target_layer = uv_layers[uv_layer_index]
    desired_name = preferred_name or target_layer.name or _default_uv_layer_name(uv_layer_index)

    for index, layer in enumerate(uv_layers):
        if index == uv_layer_index:
            continue
        if layer.name != desired_name:
            continue
        layer.name = _default_uv_layer_name(index)

    target_layer.name = desired_name
    return str(target_layer.name)


def _default_uv_layer_name(index):
    if int(index) <= 0:
        return "UVMap"
    return f"UVMap.{int(index):03d}"


def _get_uv_map_index(settings):
    try:
        value = int(getattr(settings, "uv_map_index", 0))
    except Exception:
        value = 0
    return max(0, min(3, value))


def _get_export_mode(settings):
    mode = str(getattr(settings, "export_mode", constants.EXPORT_MODE_VERTEX))
    export_mode_rbd = getattr(constants, "EXPORT_MODE_RBD", "RBD")
    if mode in ("RIGID", export_mode_rbd):
        return export_mode_rbd
    return mode


def _is_rbd_mode(settings):
    return _get_export_mode(settings) == getattr(constants, "EXPORT_MODE_RBD", "RBD")


def _get_node_group_name_for_mode(settings):
    export_mode = _get_export_mode(settings)
    mapping = getattr(constants, "GN_GROUP_BY_MODE", {}) or {}
    if export_mode in mapping:
        return mapping[export_mode]
    if export_mode == getattr(constants, "EXPORT_MODE_VERTEX", "VERTEX"):
        return "ComputeVertexVAT"
    if export_mode == getattr(constants, "EXPORT_MODE_RBD", "RBD"):
        return "ComputeRBDtoVAT"
    raise ExportWorkflowError(f"Unsupported export mode: {export_mode}")

def _get_rbd_source_collection(settings):
    return getattr(
        settings,
        "rbd_source_collection",
        getattr(settings, "rigid_source_collection", None),
    )


def _get_rbd_skip_scale(settings):
    return bool(getattr(settings, "rbd_skip_scale", getattr(settings, "skip_scale", False)))


def _get_vat_mode_index(settings):
    mode = _get_export_mode(settings)
    if mode == getattr(constants, "EXPORT_MODE_RBD", "RBD"):
        return 1
    return 0


def _json_vector3(value):
    if value is None:
        raise ExportWorkflowError("Expected vector value for JSON export, got None.")

    try:
        x = float(value[0])
        y = float(value[1])
        z = float(value[2])
    except Exception as exc:
        raise ExportWorkflowError(
            f"Expected a 3D vector value for JSON export, got {value!r}."
        ) from exc

    # Match Unreal-style FVector field names for direct JSON deserialization.
    return {
        "X": x,
        "Y": y,
        "Z": z,
    }


def _json_texture_size_value(width, height):
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ExportWorkflowError(
            f"Expected positive texture size for JSON export, got {width} x {height}."
        )

    # TextureSize is validated as square earlier in the workflow, so Unreal-side
    # consumers can treat this as a single scalar value.
    return float(width)


def _get_position_range_attribute_names(settings):
    if _is_rbd_mode(settings):
        return ("Pos_Min", "Pos_Max")
    return ("min", "max")


def _get_size_range_attribute_names(settings):
    if _is_rbd_mode(settings):
        return ("Size_Min", "Size_Max")
    return ()


def _exports_normal_texture(settings):
    return not _is_rbd_mode(settings)


def _attribute_lookup_key(name):
    return _normalize_socket_name(name)


def _uses_show_alph_composite(settings):
    return _is_rbd_mode(settings)


def _create_reference_frame_object(context, temp_scene, source_copy_objects, reference_frame):
    previous_window_scene = context.window.scene if context.window else None
    generated_parts = []

    try:
        if context.window:
            context.window.scene = temp_scene

        temp_scene.frame_set(reference_frame)
        depsgraph = context.evaluated_depsgraph_get()

        for src_obj in source_copy_objects:
            if src_obj.type != "MESH":
                continue

            eval_obj = src_obj.evaluated_get(depsgraph)
            eval_mesh = bpy.data.meshes.new_from_object(
                eval_obj,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            if eval_mesh is None:
                continue
            if len(eval_mesh.vertices) == 0:
                bpy.data.meshes.remove(eval_mesh, do_unlink=True)
                continue

            part_name = _unique_name(f"VAT_RefPart_{src_obj.name}", bpy.data.objects.keys())
            part_obj = bpy.data.objects.new(part_name, eval_mesh)
            part_obj.matrix_world = src_obj.matrix_world.copy()
            temp_scene.collection.objects.link(part_obj)
            generated_parts.append(part_obj)

        if not generated_parts:
            raise ExportWorkflowError("Failed to generate reference-frame mesh from source objects.")

        if len(generated_parts) > 1:
            ref_obj = _join_objects_in_scene(context, temp_scene, generated_parts)
        else:
            ref_obj = generated_parts[0]

        ref_obj.name = _unique_name("VAT_RefFrameOBJ", bpy.data.objects.keys())
        ref_obj.animation_data_clear()
        for constraint in list(ref_obj.constraints):
            ref_obj.constraints.remove(constraint)
        for modifier in list(ref_obj.modifiers):
            ref_obj.modifiers.remove(modifier)
        return ref_obj
    finally:
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _create_reference_frame_collection(context, temp_scene, source_copy_objects, reference_frame):
    previous_window_scene = context.window.scene if context.window else None
    reference_collection = None

    try:
        if context.window:
            context.window.scene = temp_scene

        temp_scene.frame_set(int(reference_frame))
        depsgraph = context.evaluated_depsgraph_get()

        collection_name = _unique_name("VAT_RefFrameCollection", bpy.data.collections.keys())
        reference_collection = bpy.data.collections.new(collection_name)
        temp_scene.collection.children.link(reference_collection)

        frozen_count = 0
        for src_obj in source_copy_objects:
            frozen_obj = _create_static_reference_collection_object(src_obj, depsgraph)
            if frozen_obj is None:
                continue
            reference_collection.objects.link(frozen_obj)
            frozen_count += 1

        if frozen_count == 0:
            raise ExportWorkflowError("Failed to generate reference-frame collection from source objects.")

        return reference_collection
    finally:
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _create_static_reference_collection_object(src_obj, depsgraph):
    if src_obj is None:
        return None

    object_name = _unique_name(f"VAT_Ref_{src_obj.name}", bpy.data.objects.keys())

    if getattr(src_obj, "type", None) == "MESH":
        eval_obj = src_obj.evaluated_get(depsgraph)
        eval_mesh = bpy.data.meshes.new_from_object(
            eval_obj,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if eval_mesh is None or len(eval_mesh.vertices) == 0:
            if eval_mesh is not None:
                bpy.data.meshes.remove(eval_mesh, do_unlink=True)
            return None
        frozen_obj = bpy.data.objects.new(object_name, eval_mesh)
        frozen_obj.matrix_world = eval_obj.matrix_world.copy()
    else:
        frozen_obj = src_obj.copy()
        if getattr(src_obj, "data", None) is not None and hasattr(src_obj.data, "copy"):
            frozen_obj.data = src_obj.data.copy()
        frozen_obj.name = object_name
        frozen_obj.matrix_world = src_obj.matrix_world.copy()

    frozen_obj.animation_data_clear()
    for constraint in list(getattr(frozen_obj, "constraints", [])):
        frozen_obj.constraints.remove(constraint)
    for modifier in list(getattr(frozen_obj, "modifiers", [])):
        frozen_obj.modifiers.remove(modifier)
    return frozen_obj


def _join_objects_in_scene(context, scene, objects_to_join):
    view_layer = scene.view_layers[0]
    for obj in scene.objects:
        obj.select_set(False)

    for obj in objects_to_join:
        obj.select_set(True)

    active = objects_to_join[0]
    view_layer.objects.active = active

    with context.temp_override(
        scene=scene,
        view_layer=view_layer,
        active_object=active,
        object=active,
        selected_objects=objects_to_join,
        selected_editable_objects=objects_to_join,
    ):
        result = bpy.ops.object.join()
        if "FINISHED" not in result:
            raise ExportWorkflowError("Failed to join reference-frame mesh parts.")

    joined = view_layer.objects.active
    if joined is None:
        raise ExportWorkflowError("Reference-frame mesh join did not produce an active object.")
    return joined


def _apply_ui_values_to_gn_transfer_layer(temp_state, prepared, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    gn_modifier = temp_state.gn_modifier
    uv_map_index = _get_uv_map_index(settings)

    payload = {
        "mode": _get_export_mode(settings),
        "start_frame": settings.start_frame,
        "end_frame": settings.end_frame,
        "reference_frame": settings.reference_frame,
        "uv_map_index": uv_map_index,
        "uv_name": temp_state.selected_uv_name,
        "use_collection": prepared.use_collection,
        "object": temp_state.source_object_for_gn.name if temp_state.source_object_for_gn else "",
        "collection": temp_state.source_collection_for_gn.name if temp_state.source_collection_for_gn else "",
        "reference_object": temp_state.reference_object.name if temp_state.reference_object else "",
        "reference_collection": temp_state.reference_collection.name if temp_state.reference_collection else "",
        "texture_width": prepared.export_texture_width,
        "texture_height": prepared.export_texture_height,
        "frame_drop_step": prepared.frame_drop_step,
        "source_fps": prepared.source_fps,
        "source_kind": prepared.source_kind,
        "asset_name": prepared.asset_name,
        "show_export_mesh": False,
        "skip_grid_transfer": False,
        "skip_scale": _get_rbd_skip_scale(settings),
    }
    gn_modifier["uvb_deferred_socket_payload"] = json.dumps(payload)

    resolved = _resolve_input_socket_identifiers(prepared.node_group)
    required_missing = []

    def set_required(label, aliases, value):
        identifier = _find_socket_identifier(resolved, aliases)
        if not identifier:
            required_missing.append(label)
            return
        try:
            gn_modifier[identifier] = value
        except Exception as exc:
            raise ExportWorkflowError(
                f"Failed to assign '{label}' to Geometry Nodes input '{identifier}': {exc}"
            ) from exc

    set_required("Start Frame", ("StartFrame", "Start Frame"), int(settings.start_frame))
    set_required("End Frame", ("EndFrame", "End Frame"), int(settings.end_frame))
    set_required("Reference Frame", ("ReferenceFrame", "Reference Frame"), int(settings.reference_frame))
    if _get_export_mode(settings) == constants.EXPORT_MODE_VERTEX:
        set_required("Use Collection", ("UseCollection", "Use Collection"), bool(prepared.use_collection))
    else:
        use_collection_identifier = _find_socket_identifier(resolved, ("UseCollection", "Use Collection"))
        if use_collection_identifier:
            try:
                gn_modifier[use_collection_identifier] = bool(prepared.use_collection)
            except Exception as exc:
                raise ExportWorkflowError(
                    f"Failed to assign 'Use Collection' to Geometry Nodes input '{use_collection_identifier}': {exc}"
                ) from exc

    set_required(
        "Frame Drop Step",
        ("FrameDropStep", "Frame Drop Step", "FPSDropping", "FPS Dropping"),
        int(prepared.frame_drop_step),
    )
    if _is_rbd_mode(settings):
        set_required(
            "Ref Frame Collection",
            ("RefFrameCollection", "Ref Frame Collection", "ReferenceCollection", "Reference Collection"),
            temp_state.reference_collection,
        )
        ref_object_identifier = _find_socket_identifier(
            resolved,
            ("RefFrameOBJ", "Ref Frame OBJ", "ReferenceObject", "Reference Object", "RefObject", "Ref Object"),
        )
        if ref_object_identifier:
            try:
                gn_modifier[ref_object_identifier] = None
            except Exception:
                report({"WARNING"}, "Could not clear Ref Frame Object socket in RBD mode.")
    else:
        set_required(
            "Ref Frame Object",
            ("RefFrameOBJ", "Ref Frame OBJ", "ReferenceObject", "Reference Object", "RefObject", "Ref Object"),
            temp_state.reference_object,
        )
    set_required("UV Name", ("UVName", "UV Name"), str(temp_state.selected_uv_name))
    if _is_rbd_mode(settings):
        _set_skip_scale(prepared.node_group, gn_modifier, _get_rbd_skip_scale(settings), reporter=report)
        _set_show_alph(prepared.node_group, gn_modifier, False, reporter=report, allow_missing=True)

    if prepared.use_collection:
        set_required(
            "Collection",
            ("Collection", "SourceCollection", "Source Collection"),
            temp_state.source_collection_for_gn,
        )
        object_identifier = _find_socket_identifier(resolved, ("Object", "SourceObject", "Source Object"))
        if object_identifier:
            try:
                gn_modifier[object_identifier] = None
            except Exception:
                report({"WARNING"}, "Could not clear Object socket when Use Collection is enabled.")
    else:
        set_required(
            "Object",
            ("Object", "SourceObject", "Source Object"),
            temp_state.source_object_for_gn,
        )
        collection_identifier = _find_socket_identifier(
            resolved,
            ("Collection", "SourceCollection", "Source Collection"),
        )
        if collection_identifier:
            try:
                gn_modifier[collection_identifier] = None
            except Exception:
                report({"WARNING"}, "Could not clear Collection socket when Use Collection is disabled.")

    _set_skip_grid_transfer(prepared.node_group, gn_modifier, False, report)
    _set_show_export_mesh(prepared.node_group, gn_modifier, False, reporter=report, allow_missing=True)
    if not _is_rbd_mode(settings):
        _set_what_to_show_mode(
            prepared.node_group,
            gn_modifier,
            WHAT_TO_SHOW_POSITION,
            reporter=report,
            allow_missing=True,
        )

    if required_missing:
        missing_text = ", ".join(required_missing)
        raise ExportWorkflowError(
            f"{prepared.node_group_name} input sockets are missing: "
            f"{missing_text}. Please match the fixed socket names."
        )


def _find_what_to_show_identifier(node_group):
    resolved = _resolve_input_socket_identifiers(node_group)
    return _find_socket_identifier(resolved, ("WhatToShow", "What To Show"))


def _find_uv_name_identifier(node_group):
    resolved = _resolve_input_socket_identifiers(node_group)
    return _find_socket_identifier(resolved, ("UVName", "UV Name"))


def _find_show_export_mesh_identifier(node_group):
    resolved = _resolve_input_socket_identifiers(node_group)
    return _find_socket_identifier(resolved, ("ShowExportMesh", "Show Export Mesh"))


def _find_skip_scale_identifier(node_group):
    resolved = _resolve_input_socket_identifiers(node_group)
    return _find_socket_identifier(
        resolved,
        ("SkipScale", "Skip Scale", "SkipScail", "Skip Scail"),
    )


def _find_show_alph_identifier(node_group):
    resolved = _resolve_input_socket_identifiers(node_group)
    return _find_socket_identifier(
        resolved,
        ("ShowAlph", "Show Alph", "ShowAlpha", "Show Alpha"),
    )


def _set_what_to_show_mode(node_group, gn_modifier, mode_name, reporter=None, allow_missing=False):
    report = reporter if reporter else (lambda _level, _message: None)
    identifier = _find_what_to_show_identifier(node_group)
    if not identifier:
        if allow_missing:
            report({"WARNING"}, "WhatToShow input was not found on Geometry Nodes group.")
            return None
        raise ExportWorkflowError(
            "WhatToShow input was not found on Geometry Nodes group. "
            "Required for Position/Normal display export."
        )

    menu_index = WHAT_TO_SHOW_MENU_INDEX.get(mode_name)
    if menu_index is None:
        raise ExportWorkflowError(f"Unsupported WhatToShow mode: {mode_name}")

    try:
        gn_modifier[identifier] = int(menu_index)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign WhatToShow='{mode_name}' to Geometry Nodes input '{identifier}': {exc}"
        ) from exc

    return identifier


def _set_uv_name_input(node_group, gn_modifier, uv_name, reporter=None, allow_missing=False):
    report = reporter if reporter else (lambda _level, _message: None)
    identifier = _find_uv_name_identifier(node_group)
    if not identifier:
        if allow_missing:
            report({"WARNING"}, "UVName input was not found on Geometry Nodes group.")
            return None
        raise ExportWorkflowError(
            "UVName input was not found on Geometry Nodes group. "
            "Required for UV transfer."
        )

    try:
        gn_modifier[identifier] = str(uv_name or "")
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign UVName='{uv_name}' to Geometry Nodes input '{identifier}': {exc}"
        ) from exc

    return identifier


def _set_show_export_mesh(node_group, gn_modifier, enabled, reporter=None, allow_missing=False):
    report = reporter if reporter else (lambda _level, _message: None)
    identifier = _find_show_export_mesh_identifier(node_group)
    if not identifier:
        if allow_missing:
            report({"WARNING"}, "ShowExportMesh input was not found on Geometry Nodes group.")
            return None
        raise ExportWorkflowError(
            "ShowExportMesh input was not found on Geometry Nodes group. "
            "Required for FBX export mesh output."
        )

    try:
        gn_modifier[identifier] = bool(enabled)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign ShowExportMesh={bool(enabled)} to Geometry Nodes input '{identifier}': {exc}"
        ) from exc

    return identifier


def _set_skip_scale(node_group, gn_modifier, enabled, reporter=None, allow_missing=False):
    report = reporter if reporter else (lambda _level, _message: None)
    identifier = _find_skip_scale_identifier(node_group)
    if not identifier:
        if allow_missing:
            report({"WARNING"}, "SkipScale input was not found on Geometry Nodes group.")
            return None
        raise ExportWorkflowError(
            "SkipScale input was not found on Geometry Nodes group. "
            "Required for RBD export."
        )

    try:
        gn_modifier[identifier] = bool(enabled)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign SkipScale={bool(enabled)} to Geometry Nodes input '{identifier}': {exc}"
        ) from exc

    return identifier


def _set_show_alph(node_group, gn_modifier, enabled, reporter=None, allow_missing=False):
    report = reporter if reporter else (lambda _level, _message: None)
    identifier = _find_show_alph_identifier(node_group)
    if not identifier:
        if allow_missing:
            report({"WARNING"}, "ShowAlph input was not found on Geometry Nodes group.")
            return None
        raise ExportWorkflowError(
            "ShowAlph input was not found on Geometry Nodes group. "
            "Required for RBD alpha export."
        )

    try:
        gn_modifier[identifier] = bool(enabled)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign ShowAlph={bool(enabled)} to Geometry Nodes input '{identifier}': {exc}"
        ) from exc

    return identifier


def _set_skip_grid_transfer(node_group, gn_modifier, enabled, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    resolved = _resolve_input_socket_identifiers(node_group)
    identifier = _find_socket_identifier(
        resolved,
        (
            "SkipGridTransfer",
            "Skip Grid Transfer",
            "SkipGrid",
            "Skip Grid",
        ),
    )

    if not identifier:
        if enabled:
            raise ExportWorkflowError(
                "SkipGridTransfer input was not found on Geometry Nodes group. "
                "Required for attribute-safe readback."
            )
        report({"WARNING"}, "SkipGridTransfer input was not found on Geometry Nodes group.")
        return

    try:
        gn_modifier[identifier] = bool(enabled)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to assign SkipGridTransfer to Geometry Nodes input '{identifier}': {exc}"
        ) from exc


def _bake_geometry_nodes_animation_node(context, temp_state, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    if not hasattr(bpy.ops.object, "geometry_node_bake_single"):
        raise ExportWorkflowError("Geometry Nodes bake operator is unavailable in this Blender build.")

    scene = temp_state.scene
    helper_object = temp_state.helper_object
    gn_modifier = temp_state.gn_modifier
    bakes = list(getattr(gn_modifier, "bakes", []))

    if not bakes:
        report({"WARNING"}, "No Geometry Nodes bake items were found on the helper modifier.")
        return

    # Run bake only once: prefer GeometryNodeBake, fallback to SimulationOutput.
    target_bake = None
    target_node_type = ""
    for bake_item in bakes:
        node = getattr(bake_item, "node", None)
        node_type = getattr(node, "bl_idname", "")
        if node_type == "GeometryNodeBake":
            target_bake = bake_item
            target_node_type = node_type
            break
    if target_bake is None:
        for bake_item in bakes:
            node = getattr(bake_item, "node", None)
            node_type = getattr(node, "bl_idname", "")
            if node_type == "GeometryNodeSimulationOutput":
                target_bake = bake_item
                target_node_type = node_type
                break

    if target_bake is None:
        report(
            {"WARNING"},
            "No animation-capable bake node was found on the Geometry Nodes modifier."
            " Skipping bake step.",
        )
        return

    previous_window_scene = context.window.scene if context.window else None
    view_layer = scene.view_layers[0]
    previous_active = view_layer.objects.active
    previous_selected = [obj for obj in scene.objects if obj.select_get()]
    previous_frame = scene.frame_current
    previous_frame_start = scene.frame_start
    previous_frame_end = scene.frame_end

    bake_start = int(settings.start_frame)
    bake_end = int(settings.end_frame)
    if bake_end < bake_start:
        raise ExportWorkflowError("End Frame must be greater than or equal to Start Frame for bake.")

    try:
        if context.window:
            context.window.scene = scene

        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass

        for obj in scene.objects:
            obj.select_set(False)

        helper_object.select_set(True)
        view_layer.objects.active = helper_object

        # Force animation bake across the full requested frame range.
        scene.frame_start = bake_start
        scene.frame_end = bake_end
        scene.frame_set(bake_start)

        if hasattr(target_bake, "bake_mode"):
            try:
                target_bake.bake_mode = "ANIMATION"
            except Exception:
                report({"WARNING"}, "Could not set bake_mode to ANIMATION; using current bake mode.")

        if hasattr(target_bake, "use_custom_simulation_frame_range"):
            target_bake.use_custom_simulation_frame_range = True
        if hasattr(target_bake, "frame_start"):
            target_bake.frame_start = bake_start
        if hasattr(target_bake, "frame_end"):
            target_bake.frame_end = bake_end

        bake_id = int(getattr(target_bake, "bake_id", 0))
        with context.temp_override(
            scene=scene,
            view_layer=view_layer,
            object=helper_object,
            active_object=helper_object,
            selected_objects=[helper_object],
            selected_editable_objects=[helper_object],
        ):
            if hasattr(bpy.ops.object, "geometry_node_bake_delete_single"):
                try:
                    bpy.ops.object.geometry_node_bake_delete_single(
                        session_uid=int(helper_object.session_uid),
                        modifier_name=gn_modifier.name,
                        bake_id=bake_id,
                    )
                except RuntimeError:
                    pass

            result = bpy.ops.object.geometry_node_bake_single(
                session_uid=int(helper_object.session_uid),
                modifier_name=gn_modifier.name,
                bake_id=bake_id,
            )

        if "FINISHED" not in result:
            raise ExportWorkflowError(
                f"Geometry Nodes animation bake failed for bake_id={bake_id}"
                f" ({target_node_type}) on modifier '{gn_modifier.name}'."
            )

        scene.frame_set(bake_end)
        report(
            {"INFO"},
            f"Geometry Nodes animation bake completed ({target_node_type}, bake_id={bake_id},"
            f" range={bake_start}-{bake_end}).",
        )
    except RuntimeError as exc:
        raise ExportWorkflowError(f"Geometry Nodes bake failed: {exc}") from exc
    finally:
        for obj in scene.objects:
            obj.select_set(False)
        for obj in previous_selected:
            if obj.name in scene.objects:
                obj.select_set(True)

        if previous_active and previous_active.name in scene.objects:
            view_layer.objects.active = previous_active

        scene.frame_start = previous_frame_start
        scene.frame_end = previous_frame_end
        scene.frame_set(previous_frame)

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _evaluate_timeline_sequence(context, scene, start_frame, end_frame):
    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current

    try:
        if context.window:
            context.window.scene = scene

        start = int(start_frame)
        end = int(end_frame)
        for frame in range(start, end + 1):
            scene.frame_set(frame)
        scene.frame_set(end)
    finally:
        scene.frame_set(previous_frame)
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _apply_end_frame_export_settings(context, temp_state, prepared, settings, end_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    end_frame = int(end_frame)
    range_attr_names = _get_position_range_attribute_names(settings)

    report({"INFO"}, "Enabling SkipGridTransfer for attribute-safe readback.")
    _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)

    try:
        temp_state.scene.frame_set(end_frame)
        prepared.end_frame_attributes = _read_named_attributes_from_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            end_frame,
            (
                "texturesize",
                range_attr_names[0],
                range_attr_names[1],
                *_get_size_range_attribute_names(settings),
                "cameraorthoscale",
                "cameraortho",
                "cameralocation",
                "cameraposition",
            ),
        )

        _apply_texture_size_from_gn_attribute(
            context,
            temp_state,
            prepared,
            end_frame,
            report,
        )
        _apply_camera_settings_from_gn_attributes(
            context,
            temp_state,
            end_frame,
            report,
        )
        _ensure_end_frame_min_max_attributes(
            context,
            temp_state,
            prepared,
            settings,
            end_frame,
            report,
        )
    finally:
        report({"INFO"}, "Restoring SkipGridTransfer to false.")
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
        temp_state.scene.frame_set(end_frame)


def _apply_camera_settings_from_gn_attributes(context, temp_state, evaluate_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    values = _read_named_attributes_from_evaluated_geometry(
        context,
        temp_state.scene,
        temp_state.helper_object,
        int(evaluate_frame),
        (
            "cameraorthoscale",
            "cameraortho",
            "cameralocation",
            "cameraposition",
        ),
    )

    applied_any = False
    ortho_value = None
    for key in ("cameraorthoscale", "cameraortho"):
        if key in values:
            ortho_value = values[key]
            break

    ortho_scalar = _coerce_float_numeric_value(ortho_value)
    if ortho_scalar is not None and ortho_scalar > 0.0:
        temp_state.camera_data.ortho_scale = float(ortho_scalar)
        applied_any = True

    location_value = None
    for key in ("cameralocation", "cameraposition"):
        if key in values:
            location_value = values[key]
            break

    if isinstance(location_value, (list, tuple)) and len(location_value) >= 3:
        temp_state.camera_object.location = (
            float(location_value[0]),
            float(location_value[1]),
            float(location_value[2]),
        )
        applied_any = True

    if applied_any:
        report({"INFO"}, "Applied camera settings from End Frame Geometry Nodes attributes.")


def _ensure_end_frame_min_max_attributes(context, temp_state, prepared, settings, evaluate_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    eval_frame = int(evaluate_frame)
    min_attr_name, max_attr_name = _get_position_range_attribute_names(settings)
    size_attr_names = _get_size_range_attribute_names(settings)
    min_attr_key = _attribute_lookup_key(min_attr_name)
    max_attr_key = _attribute_lookup_key(max_attr_name)
    size_attr_keys = tuple(_attribute_lookup_key(name) for name in size_attr_names)

    attrs = getattr(prepared, "end_frame_attributes", {}) or {}
    min_value = attrs.get(min_attr_key)
    max_value = attrs.get(max_attr_key)
    if min_value is not None and max_value is not None:
        if not size_attr_names:
            return
        if attrs.get(size_attr_keys[0]) is not None and attrs.get(size_attr_keys[1]) is not None:
            return

    missing_labels = [min_attr_name, max_attr_name]
    missing_labels.extend(size_attr_names)
    report({"INFO"}, f"{', '.join(missing_labels)} not found with SkipGridTransfer=True. Retrying with SkipGridTransfer=False...")
    values_false = {}

    try:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
        temp_state.scene.frame_set(eval_frame)
        values_false = _read_named_attributes_from_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
            (min_attr_name, max_attr_name, *size_attr_names),
        )
    finally:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)
        temp_state.scene.frame_set(eval_frame)

    for attr_name in (min_attr_name, max_attr_name, *size_attr_names):
        attr_key = _attribute_lookup_key(attr_name)
        if attr_key in values_false and attrs.get(attr_key) is None:
            attrs[attr_key] = values_false[attr_key]

    prepared.end_frame_attributes = attrs

    min_value = attrs.get(min_attr_key)
    max_value = attrs.get(max_attr_key)
    size_ok = True
    if size_attr_names:
        size_ok = attrs.get(size_attr_keys[0]) is not None and attrs.get(size_attr_keys[1]) is not None
    if min_value is not None and max_value is not None and size_ok:
        report({"INFO"}, f"Collected {', '.join(missing_labels)} from Geometry Nodes End Frame attributes.")
        return

    point_true = _list_available_attribute_names_on_evaluated_geometry(
        context,
        temp_state.scene,
        temp_state.helper_object,
        eval_frame,
    )
    instance_true = _list_available_attribute_names_via_realized_instances_probe(
        context,
        temp_state.scene,
        temp_state.helper_object,
        eval_frame,
    )

    point_false = []
    instance_false = []
    try:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
        temp_state.scene.frame_set(eval_frame)
        point_false = _list_available_attribute_names_on_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
        )
        instance_false = _list_available_attribute_names_via_realized_instances_probe(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
        )
    finally:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)
        temp_state.scene.frame_set(eval_frame)

    text_point_true = ", ".join(point_true) if point_true else "(none)"
    text_instance_true = ", ".join(instance_true) if instance_true else "(none)"
    text_point_false = ", ".join(point_false) if point_false else "(none)"
    text_instance_false = ", ".join(instance_false) if instance_false else "(none)"

    raise ExportWorkflowError(
        f"Geometry Nodes attributes {missing_labels!r} were not found in End Frame readback. "
        f"SkipGridTransfer=True -> Point: {text_point_true} | Instance(realized): {text_instance_true}. "
        f"SkipGridTransfer=False -> Point: {text_point_false} | Instance(realized): {text_instance_false}."
    )
def _apply_texture_size_from_gn_attribute(context, temp_state, prepared, evaluate_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    eval_frame = int(evaluate_frame)
    width, height = _resolve_texture_size_from_attribute_values(
        getattr(prepared, "end_frame_attributes", {}) or {}
    )
    resolution_source = "Geometry Nodes end-frame attributes"

    if width is None or height is None:
        width, height = _read_texture_size_from_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
        )
        resolution_source = "Geometry Nodes attribute 'TextureSize' (SkipGridTransfer=True)"

    if width is None or height is None:
        width, height = _read_texture_size_from_gn_input_settings(
            temp_state.gn_modifier,
            prepared.node_group,
        )
        if width is not None and height is not None:
            resolution_source = "Geometry Nodes input settings"

    if width is None or height is None:
        report({"INFO"}, "TextureSize not found with SkipGridTransfer=True. Retrying with SkipGridTransfer=False...")
        try:
            _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
            temp_state.scene.frame_set(eval_frame)
            width, height = _read_texture_size_from_evaluated_geometry(
                context,
                temp_state.scene,
                temp_state.helper_object,
                eval_frame,
            )
            if width is not None and height is not None:
                resolution_source = "Geometry Nodes attribute 'TextureSize' (fallback: SkipGridTransfer=False)"
        finally:
            _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)
            temp_state.scene.frame_set(eval_frame)

    if width is None or height is None:
        point_true = _list_available_attribute_names_on_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
        )
        instance_true = _list_available_attribute_names_via_realized_instances_probe(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
        )

        point_false = []
        instance_false = []
        try:
            _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
            temp_state.scene.frame_set(eval_frame)
            point_false = _list_available_attribute_names_on_evaluated_geometry(
                context,
                temp_state.scene,
                temp_state.helper_object,
                eval_frame,
            )
            instance_false = _list_available_attribute_names_via_realized_instances_probe(
                context,
                temp_state.scene,
                temp_state.helper_object,
                eval_frame,
            )
        finally:
            _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)
            temp_state.scene.frame_set(eval_frame)

        text_point_true = ", ".join(point_true) if point_true else "(none)"
        text_instance_true = ", ".join(instance_true) if instance_true else "(none)"
        text_point_false = ", ".join(point_false) if point_false else "(none)"
        text_instance_false = ", ".join(instance_false) if instance_false else "(none)"

        raise ExportWorkflowError(
            "TextureSize was not found in Geometry Nodes. "
            "Provide a named attribute 'TextureSize' (or TextureSize input socket). "
            f"SkipGridTransfer=True -> Point: {text_point_true} | Instance(realized): {text_instance_true}. "
            f"SkipGridTransfer=False -> Point: {text_point_false} | Instance(realized): {text_instance_false}."
        )

    width = int(width)
    height = int(height)

    if width <= 0 or height <= 0:
        raise ExportWorkflowError(
            f"Invalid TextureSize from Geometry Nodes: {width} x {height}. "
            "Resolution must be positive."
        )

    if width != height:
        raise ExportWorkflowError(
            f"Invalid TextureSize from Geometry Nodes: {width} x {height}. "
            "Resolution must be square (width == height)."
        )

    if not _is_power_of_two(width):
        raise ExportWorkflowError(
            f"Invalid TextureSize from Geometry Nodes: {width} x {height}. "
            "Resolution must be a power of two (256, 512, 1024, 2048, ...)."
        )

    prepared.export_texture_width = width
    prepared.export_texture_height = height
    temp_state.scene.render.resolution_x = width
    temp_state.scene.render.resolution_y = height
    temp_state.scene.render.resolution_percentage = 100
    report({"INFO"}, f"Texture size from {resolution_source}: {width} x {height}")


def _read_texture_size_from_evaluated_geometry(context, scene, helper_object, evaluate_frame):
    values = _read_named_attributes_from_evaluated_geometry(
        context,
        scene,
        helper_object,
        int(evaluate_frame),
        ("texturesize",),
    )
    return _resolve_texture_size_from_attribute_values(values)


def _resolve_texture_size_from_attribute_values(values):
    for key in ("texturesize",):
        if key in values:
            resolution = _coerce_resolution_from_value(values.get(key))
            if resolution is not None:
                return resolution
    return None, None


def _read_texture_size_from_gn_input_settings(gn_modifier, node_group):
    resolved = _resolve_input_socket_identifiers(node_group)

    single_identifier = _find_socket_identifier(
        resolved,
        (
            "TextureSize",
            "Texture Size",
        ),
    )
    if single_identifier:
        value = _read_modifier_input_value(gn_modifier, node_group, single_identifier)
        resolution = _coerce_resolution_from_value(value)
        if resolution is not None:
            return resolution

    width_identifier = _find_socket_identifier(
        resolved,
        ("TextureWidth", "Texture Width"),
    )
    height_identifier = _find_socket_identifier(
        resolved,
        ("TextureHeight", "Texture Height"),
    )

    if width_identifier and height_identifier:
        width_value = _read_modifier_input_value(gn_modifier, node_group, width_identifier)
        height_value = _read_modifier_input_value(gn_modifier, node_group, height_identifier)
        width = _coerce_scalar_numeric_value(width_value)
        height = _coerce_scalar_numeric_value(height_value)
        if width is not None and height is not None:
            return width, height

    return None, None


def _read_modifier_input_value(gn_modifier, node_group, identifier):
    try:
        if identifier in gn_modifier:
            return gn_modifier[identifier]
    except Exception:
        pass

    return _read_interface_socket_default(node_group, identifier)


def _read_interface_socket_default(node_group, identifier):
    interface = getattr(node_group, "interface", None)
    if interface and hasattr(interface, "items_tree"):
        for item in interface.items_tree:
            if getattr(item, "item_type", "") != "SOCKET":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            if getattr(item, "identifier", "") != identifier:
                continue
            if hasattr(item, "default_value"):
                return item.default_value

    if hasattr(node_group, "inputs"):
        for socket in node_group.inputs:
            if getattr(socket, "identifier", "") != identifier:
                continue
            if hasattr(socket, "default_value"):
                return socket.default_value

    return None


def _coerce_resolution_from_value(value):
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip().lower().replace(" ", "")
        if "x" in text:
            parts = text.split("x")
            if len(parts) == 2:
                width = _coerce_scalar_numeric_value(parts[0])
                height = _coerce_scalar_numeric_value(parts[1])
                if width is not None and height is not None:
                    return width, height
        scalar = _coerce_scalar_numeric_value(text)
        if scalar is not None:
            return scalar, scalar
        return None

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        width = _coerce_scalar_numeric_value(value[0])
        height = _coerce_scalar_numeric_value(value[1])
        if width is not None and height is not None:
            return width, height
        return None

    if hasattr(value, "x") and hasattr(value, "y"):
        width = _coerce_scalar_numeric_value(value.x)
        height = _coerce_scalar_numeric_value(value.y)
        if width is not None and height is not None:
            return width, height
        return None

    scalar = _coerce_scalar_numeric_value(value)
    if scalar is not None:
        return scalar, scalar

    return None


def _coerce_scalar_numeric_value(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return max(1, int(round(float(value))))

    if isinstance(value, str):
        try:
            return max(1, int(round(float(value))))
        except ValueError:
            return None

    return None


def _coerce_float_numeric_value(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None

    return None

def _read_named_attributes_from_evaluated_geometry(context, scene, helper_object, evaluate_frame, required_names):
    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    required = {_normalize_socket_name(name) for name in required_names}

    try:
        if context.window:
            context.window.scene = scene

        scene.frame_set(int(evaluate_frame))
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = helper_object.evaluated_get(depsgraph)

        values = _extract_named_attribute_values_from_data_block(getattr(eval_obj, "data", None), required)

        eval_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if eval_mesh is not None:
            try:
                values.update(_extract_named_attribute_values_from_data_block(eval_mesh, required))
            finally:
                eval_obj.to_mesh_clear()

        missing = [name for name in required if name not in values]
        if missing:
            realized_values = _read_named_attributes_via_realized_instances_probe(
                context,
                scene,
                helper_object,
                int(evaluate_frame),
                set(missing),
            )
            values.update(realized_values)

        return values
    finally:
        scene.frame_set(previous_frame)
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _extract_named_attribute_values_from_data_block(data_block, required_names):
    values = {}
    if data_block is None:
        return values

    attributes = getattr(data_block, "attributes", None)
    if attributes is None:
        return values

    for attr in attributes:
        attr_name = _normalize_socket_name(getattr(attr, "name", ""))
        if attr_name not in required_names:
            continue
        if len(attr.data) == 0:
            continue

        sample_value = _extract_attribute_sample_value(attr.data[0])
        values[attr_name] = _to_json_serializable_value(sample_value)
    return values


def _extract_named_attribute_values_from_mesh(mesh, required_names):
    return _extract_named_attribute_values_from_data_block(mesh, required_names)


def _list_available_attribute_names_on_evaluated_geometry(context, scene, helper_object, evaluate_frame):
    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current

    try:
        if context.window:
            context.window.scene = scene

        scene.frame_set(int(evaluate_frame))
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = helper_object.evaluated_get(depsgraph)

        names = set()
        data_block = getattr(eval_obj, "data", None)
        attributes = getattr(data_block, "attributes", None)
        if attributes is not None:
            for attr in attributes:
                names.add(str(getattr(attr, "name", "")))

        eval_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if eval_mesh is not None:
            try:
                for attr in eval_mesh.attributes:
                    names.add(str(getattr(attr, "name", "")))
            finally:
                eval_obj.to_mesh_clear()

        return sorted([name for name in names if name])
    finally:
        scene.frame_set(previous_frame)
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene

def _list_available_attribute_names_via_realized_instances_probe(context, scene, helper_object, evaluate_frame):
    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current

    probe_object = None
    probe_group = None
    names = set()

    try:
        if context.window:
            context.window.scene = scene
        scene.frame_set(int(evaluate_frame))

        probe_object = helper_object.copy()
        if helper_object.data is not None:
            probe_object.data = helper_object.data.copy()
        probe_object.name = _unique_name("UVB_AttrProbeObject", bpy.data.objects.keys())
        scene.collection.objects.link(probe_object)

        probe_group = _create_realize_instances_probe_group()
        probe_modifier = probe_object.modifiers.new("UVB_RealizeProbe", "NODES")
        probe_modifier.node_group = probe_group

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = probe_object.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if eval_mesh is None:
            return []

        try:
            attributes = getattr(eval_mesh, "attributes", None)
            if attributes is None:
                return []
            for attr in attributes:
                name = str(getattr(attr, "name", ""))
                if name:
                    names.add(name)
        finally:
            eval_obj.to_mesh_clear()

        return sorted(names)
    finally:
        if probe_object is not None:
            mesh_data = probe_object.data
            try:
                bpy.data.objects.remove(probe_object, do_unlink=True)
            except Exception:
                pass

            if mesh_data is not None and getattr(mesh_data, "users", 0) == 0:
                try:
                    bpy.data.meshes.remove(mesh_data, do_unlink=True)
                except Exception:
                    pass

        if probe_group is not None and getattr(probe_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(probe_group, do_unlink=True)
            except Exception:
                pass

        scene.frame_set(previous_frame)
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _read_named_attributes_via_realized_instances_probe(context, scene, helper_object, evaluate_frame, required_names):
    if not required_names:
        return {}

    probe_object = None
    probe_group = None

    try:
        probe_object = helper_object.copy()
        if helper_object.data is not None:
            probe_object.data = helper_object.data.copy()
        probe_object.name = _unique_name("UVB_AttrProbeObject", bpy.data.objects.keys())
        scene.collection.objects.link(probe_object)

        probe_group = _create_realize_instances_probe_group()
        probe_modifier = probe_object.modifiers.new("UVB_RealizeProbe", "NODES")
        probe_modifier.node_group = probe_group

        if context.window:
            context.window.scene = scene
        scene.frame_set(int(evaluate_frame))

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = probe_object.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if eval_mesh is None:
            return {}

        try:
            return _extract_named_attribute_values_from_mesh(eval_mesh, required_names)
        finally:
            eval_obj.to_mesh_clear()
    finally:
        if probe_object is not None:
            mesh_data = probe_object.data
            try:
                bpy.data.objects.remove(probe_object, do_unlink=True)
            except Exception:
                pass

            if mesh_data is not None and getattr(mesh_data, "users", 0) == 0:
                try:
                    bpy.data.meshes.remove(mesh_data, do_unlink=True)
                except Exception:
                    pass

        if probe_group is not None and getattr(probe_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(probe_group, do_unlink=True)
            except Exception:
                pass


def _create_realize_instances_probe_group():
    group_name = _unique_name("UVB_RealizeInstancesProbe", bpy.data.node_groups.keys())
    node_group = bpy.data.node_groups.new(group_name, "GeometryNodeTree")

    interface = getattr(node_group, "interface", None)
    if interface and hasattr(interface, "new_socket"):
        interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    elif hasattr(node_group, "inputs") and hasattr(node_group, "outputs"):
        node_group.inputs.new("NodeSocketGeometry", "Geometry")
        node_group.outputs.new("NodeSocketGeometry", "Geometry")

    nodes = node_group.nodes
    links = node_group.links

    group_input = nodes.new("NodeGroupInput")
    realize = nodes.new("GeometryNodeRealizeInstances")
    group_output = nodes.new("NodeGroupOutput")

    links.new(group_input.outputs[0], realize.inputs[0])
    links.new(realize.outputs[0], group_output.inputs[0])

    return node_group


def _is_power_of_two(value):
    ivalue = int(value)
    return ivalue > 0 and (ivalue & (ivalue - 1)) == 0


def _extract_attribute_sample_value(sample):
    if hasattr(sample, "vector"):
        return [float(v) for v in sample.vector]

    if hasattr(sample, "color"):
        return [float(v) for v in sample.color]

    if hasattr(sample, "value"):
        return float(sample.value)

    if hasattr(sample, "x") and hasattr(sample, "y"):
        z_value = float(getattr(sample, "z", 0.0))
        return [float(sample.x), float(sample.y), z_value]

    return None


def _to_json_serializable_value(value):
    if isinstance(value, tuple):
        return [float(v) for v in value]
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _resolve_input_socket_identifiers(node_group):
    resolved = {}

    interface = getattr(node_group, "interface", None)
    if interface and hasattr(interface, "items_tree"):
        for item in interface.items_tree:
            if getattr(item, "item_type", "") != "SOCKET":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            name = getattr(item, "name", "")
            identifier = getattr(item, "identifier", "")
            if name and identifier:
                resolved[_normalize_socket_name(name)] = identifier

    if not resolved and hasattr(node_group, "inputs"):
        for socket in node_group.inputs:
            name = getattr(socket, "name", "")
            identifier = getattr(socket, "identifier", "")
            if name and identifier:
                resolved[_normalize_socket_name(name)] = identifier

    return resolved


def _find_socket_identifier(resolved, aliases):
    for alias in aliases:
        key = _normalize_socket_name(alias)
        identifier = resolved.get(key)
        if identifier:
            return identifier
    return None


def _normalize_socket_name(name):
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


def _runtime_node_group_base_name(node_group_name):
    return f"{UVB_RUNTIME_NODE_GROUP_PREFIX}{node_group_name}"


def _purge_runtime_node_groups(reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    removed = 0
    skipped = 0

    for node_group in list(bpy.data.node_groups):
        if not node_group.name.startswith(UVB_RUNTIME_NODE_GROUP_PREFIX):
            continue

        try:
            bpy.data.node_groups.remove(node_group, do_unlink=True)
            removed += 1
        except RuntimeError:
            skipped += 1
        except Exception:
            skipped += 1

    if removed:
        report({"INFO"}, f"Cleared {removed} stale runtime Geometry Nodes group(s).")
    if skipped:
        report(
            {"WARNING"},
            f"Could not clear {skipped} runtime Geometry Nodes group(s) because they are still in use.",
        )


def _load_fresh_node_group_from_library(node_group_name, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    blend_path = _node_library_path()

    if not os.path.isfile(blend_path):
        raise ExportWorkflowError(
            "Required Geometry Nodes library is missing: "
            f"{blend_path}"
        )

    before_ptrs = {ng.as_pointer() for ng in bpy.data.node_groups}
    appended_handles = []

    try:
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
            available = list(data_from.node_groups)
            if node_group_name not in available:
                available_text = ", ".join(available) if available else "(none)"
                raise ExportWorkflowError(
                    f"Node group '{node_group_name}' was not found in '{blend_path}'. "
                    f"Available node groups: {available_text}"
                )
            data_to.node_groups = [node_group_name]
            appended_handles = list(data_to.node_groups)
    except ExportWorkflowError:
        raise
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to append node group '{node_group_name}' from '{blend_path}': {exc}"
        ) from exc

    loaded_group = None

    for handle in appended_handles:
        if handle is None:
            continue

        candidate = None
        if isinstance(handle, str):
            candidate = bpy.data.node_groups.get(handle)
        elif hasattr(handle, "name"):
            candidate = handle

        if candidate is not None and hasattr(candidate, "name"):
            loaded_group = candidate
            break

    if loaded_group is None:
        loaded_candidates = [
            ng for ng in bpy.data.node_groups
            if ng.as_pointer() not in before_ptrs
            and (ng.name == node_group_name or ng.name.startswith(f"{node_group_name}."))
        ]
        if loaded_candidates:
            loaded_group = loaded_candidates[0]

    if loaded_group is None:
        raise ExportWorkflowError(
            f"Failed to load Geometry Nodes group '{node_group_name}' from '{blend_path}'."
        )

    runtime_name = _unique_name(_runtime_node_group_base_name(node_group_name), bpy.data.node_groups.keys())
    loaded_group.name = runtime_name
    loaded_group.use_fake_user = False
    report({"INFO"}, f"Loaded fresh node group from assets: {runtime_name}")
    return loaded_group


def _node_library_path():
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(addon_dir, "assets", "vat_nodes.blend")


def _get_scene_compositor_flag_names(scene):
    names = []
    for attr in (
        "use_nodes",
        "use_compositor",
        "use_compositing",
        "use_compositor_nodes",
        "use_compositing_nodes",
    ):
        if hasattr(scene, attr):
            names.append(attr)
    return names


def _capture_scene_compositor_flags(scene):
    state = {}
    for attr in _get_scene_compositor_flag_names(scene):
        try:
            state[attr] = bool(getattr(scene, attr))
        except Exception:
            pass
    return state


def _restore_scene_compositor_flags(scene, state):
    for attr, value in state.items():
        if not hasattr(scene, attr):
            continue
        try:
            setattr(scene, attr, bool(value))
        except Exception:
            pass


def _enable_scene_compositor_flags(scene):
    state = _capture_scene_compositor_flags(scene)
    for attr in state.keys():
        try:
            setattr(scene, attr, True)
        except Exception:
            pass
    return state


def _resolve_scene_compositor_node_tree(scene):
    for attr in ("node_tree", "compositor_node_tree", "compositing_node_tree", "compositing_node_group"):
        if not hasattr(scene, attr):
            continue
        try:
            tree = getattr(scene, attr)
        except Exception:
            tree = None
        if tree is not None and hasattr(tree, "nodes") and hasattr(tree, "links"):
            return tree

    compositing = getattr(scene, "compositing", None)
    if compositing is not None:
        for attr in ("node_tree", "tree", "compositor_node_tree"):
            if not hasattr(compositing, attr):
                continue
            try:
                tree = getattr(compositing, attr)
            except Exception:
                tree = None
            if tree is not None and hasattr(tree, "nodes") and hasattr(tree, "links"):
                return tree

    for attr in dir(scene):
        attr_lower = attr.lower()
        if "node_tree" not in attr_lower:
            continue
        if "composit" not in attr_lower and attr_lower != "node_tree":
            continue
        try:
            tree = getattr(scene, attr)
        except Exception:
            continue
        if tree is not None and hasattr(tree, "nodes") and hasattr(tree, "links"):
            return tree

    return None


def _describe_scene_compositor_capabilities(scene):
    attrs = []
    for attr in dir(scene):
        a = attr.lower()
        if "composit" in a or a in ("use_nodes", "node_tree"):
            attrs.append(attr)
    return ", ".join(sorted(set(attrs))) if attrs else "(none)"



def _ensure_scene_compositor_tree(scene):
    tree = _resolve_scene_compositor_node_tree(scene)
    if tree is not None:
        return tree, False

    if hasattr(scene, "compositing_node_group"):
        try:
            group = bpy.data.node_groups.new(
                _unique_name("UVB_CompositorRuntime", bpy.data.node_groups.keys()),
                "CompositorNodeTree",
            )
            scene.compositing_node_group = group
            if hasattr(group, "nodes") and hasattr(group, "links"):
                return group, True
        except Exception:
            pass

    return None, False
def _export_rendered_textures(context, prepared, temp_state, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    try:
        return _export_textures_from_realized_geometry(context, prepared, temp_state, settings, reporter=report)
    except ExportWorkflowError as exc:
        report(
            {"WARNING"},
            "Direct Geometry Nodes texel export failed. Falling back to render export: "
            f"{exc}",
        )
        return _export_rendered_textures_via_render(context, prepared, temp_state, settings, reporter=report)


def _export_textures_from_realized_geometry(context, prepared, temp_state, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    prefix = _build_texture_prefix(settings, prepared.asset_name)
    position_filename = f"{prefix}_{constants.POSITION_SUFFIX}.exr"
    position_path = os.path.join(prepared.output_directory, position_filename)
    normal_path = None
    if _exports_normal_texture(settings):
        normal_filename = f"{prefix}_{constants.NORMAL_SUFFIX}.exr"
        normal_path = os.path.join(prepared.output_directory, normal_filename)

    scene = temp_state.scene
    eval_frame = int(settings.end_frame)

    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    previous_image_settings = _capture_scene_image_settings(scene)

    what_to_show_identifier = None
    previous_what_to_show = None
    requires_what_to_show = _exports_normal_texture(settings)
    show_alph_identifier = None
    previous_show_alph = None
    use_show_alph_composite = _uses_show_alph_composite(settings)

    try:
        if context.window:
            context.window.scene = scene

        scene.frame_set(eval_frame)
        _apply_scene_exr_image_settings(scene)

        if requires_what_to_show:
            what_to_show_identifier = _find_what_to_show_identifier(prepared.node_group)
            if not what_to_show_identifier:
                raise ExportWorkflowError(
                    "WhatToShow input was not found on Geometry Nodes group. "
                    "Required for Position/Normal attribute export."
                )
            previous_what_to_show = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                what_to_show_identifier,
            )

        if use_show_alph_composite:
            show_alph_identifier = _find_show_alph_identifier(prepared.node_group)
            if not show_alph_identifier:
                raise ExportWorkflowError(
                    "ShowAlph input was not found on Geometry Nodes group. "
                    "Required for RBD alpha export."
                )
            previous_show_alph = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                show_alph_identifier,
            )

            _set_show_alph(prepared.node_group, temp_state.gn_modifier, False, reporter=report)
            scene.frame_set(eval_frame)
            color_pixels = _read_display_attribute_pixels_via_realized_instances_probe(
                context,
                scene,
                temp_state.helper_object,
                int(eval_frame),
                int(prepared.export_texture_width),
                int(prepared.export_texture_height),
                attribute_name="DisplayAttribute",
                uv_layer_name=temp_state.selected_uv_name,
                allow_partial=_is_rbd_mode(settings),
                reporter=report,
            )

            _set_show_alph(prepared.node_group, temp_state.gn_modifier, True, reporter=report)
            scene.frame_set(eval_frame)
            alpha_pixels = _read_display_attribute_pixels_via_realized_instances_probe(
                context,
                scene,
                temp_state.helper_object,
                int(eval_frame),
                int(prepared.export_texture_width),
                int(prepared.export_texture_height),
                attribute_name="DisplayAttribute",
                uv_layer_name=temp_state.selected_uv_name,
                allow_partial=_is_rbd_mode(settings),
                reporter=report,
            )

            combined_pixels = _compose_rgb_and_alpha_pixels(color_pixels, alpha_pixels)
            _save_float_exr_image(
                scene,
                position_path,
                int(prepared.export_texture_width),
                int(prepared.export_texture_height),
                combined_pixels,
            )
        else:
            _write_display_attribute_texture_from_geometry(
                context,
                scene,
                temp_state,
                prepared,
                position_path,
                eval_frame,
                WHAT_TO_SHOW_POSITION if requires_what_to_show else None,
                reporter=report,
            )
        if normal_path:
            _write_display_attribute_texture_from_geometry(
                context,
                scene,
                temp_state,
                prepared,
                normal_path,
                eval_frame,
                WHAT_TO_SHOW_NORMAL,
                reporter=report,
            )

        report({"INFO"}, f"Direct Position EXR: {position_path}")
        if normal_path:
            report({"INFO"}, f"Direct Normal EXR: {normal_path}")
        return position_path, normal_path

    finally:
        if show_alph_identifier:
            try:
                temp_state.gn_modifier[show_alph_identifier] = bool(previous_show_alph)
            except Exception:
                pass
        if what_to_show_identifier:
            try:
                temp_state.gn_modifier[what_to_show_identifier] = int(previous_what_to_show)
            except Exception:
                pass

        _restore_scene_image_settings(scene, previous_image_settings)

        scene.frame_set(previous_frame)

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _export_rendered_textures_via_render(context, prepared, temp_state, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

    prefix = _build_texture_prefix(settings, prepared.asset_name)
    position_filename = f"{prefix}_{constants.POSITION_SUFFIX}.exr"
    position_path = os.path.join(prepared.output_directory, position_filename)
    normal_path = None
    if _exports_normal_texture(settings):
        normal_filename = f"{prefix}_{constants.NORMAL_SUFFIX}.exr"
        normal_path = os.path.join(prepared.output_directory, normal_filename)

    scene = temp_state.scene
    view_layer = scene.view_layers[0]
    eval_frame = int(settings.end_frame)

    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    previous_engine = scene.render.engine
    previous_compositor_flags = _enable_scene_compositor_flags(scene)
    previous_material_override = getattr(view_layer, "material_override", None) if hasattr(view_layer, "material_override") else None
    previous_compositing_node_group = getattr(scene, "compositing_node_group", None) if hasattr(scene, "compositing_node_group") else None
    previous_film_transparent = getattr(scene.render, "film_transparent", None) if hasattr(scene.render, "film_transparent") else None
    previous_image_settings = _capture_scene_image_settings(scene)
    previous_cycles_settings = _capture_cycles_export_settings(scene)
    previous_hide_render = {obj.name: bool(getattr(obj, "hide_render", False)) for obj in scene.objects}
    previous_view_transform = getattr(scene.view_settings, "view_transform", None) if hasattr(scene, "view_settings") else None
    previous_look = getattr(scene.view_settings, "look", None) if hasattr(scene, "view_settings") and hasattr(scene.view_settings, "look") else None
    previous_exposure = getattr(scene.view_settings, "exposure", None) if hasattr(scene, "view_settings") and hasattr(scene.view_settings, "exposure") else None
    previous_gamma = getattr(scene.view_settings, "gamma", None) if hasattr(scene, "view_settings") and hasattr(scene.view_settings, "gamma") else None

    node_tree = None
    created_nodes = []
    created_compositor_tree = False
    override_material = None
    what_to_show_identifier = None
    previous_what_to_show = None
    requires_what_to_show = _exports_normal_texture(settings)
    show_alph_identifier = None
    previous_show_alph = None
    use_show_alph_composite = _uses_show_alph_composite(settings)

    def ensure_render_layers_node():
        nonlocal node_tree, created_compositor_tree
        node_tree, created_compositor_tree = _ensure_scene_compositor_tree(scene)
        if node_tree is None:
            caps = _describe_scene_compositor_capabilities(scene)
            raise ExportWorkflowError(
                "Compositor node tree is unavailable for rendered EXR export. "
                f"Scene compositor-related attrs: {caps}"
            )

        nodes = node_tree.nodes
        render_layers = nodes.new("CompositorNodeRLayers")
        created_nodes.append(render_layers)
        return render_layers

    try:
        if context.window:
            context.window.scene = scene

        scene.frame_set(eval_frame)
        scene.render.engine = "CYCLES"

        _set_scene_render_visibility_for_texture_export(scene, temp_state.helper_object, temp_state.camera_object)

        override_material = _create_attribute_override_material("DisplayAttribute", attribute_type="INSTANCER")
        if hasattr(view_layer, "material_override"):
            view_layer.material_override = override_material

        _set_view_transform_if_available(scene, "Raw")
        if previous_look is not None:
            _set_scene_look_if_available(scene, "None")
        if previous_exposure is not None:
            scene.view_settings.exposure = 0.0
        if previous_gamma is not None:
            scene.view_settings.gamma = 1.0
        if previous_film_transparent is not None:
            scene.render.film_transparent = True
        _apply_scene_exr_image_settings(scene)
        _apply_cycles_export_settings(scene)
        render_layers = None
        token = f"UVB_TMP_{uuid.uuid4().hex}"
        position_prefix = f"{token}_Position_"
        normal_prefix = f"{token}_Normal_"

        if requires_what_to_show:
            what_to_show_identifier = _find_what_to_show_identifier(prepared.node_group)
            if not what_to_show_identifier:
                raise ExportWorkflowError(
                    "WhatToShow input was not found on Geometry Nodes group. "
                    "Required for Position/Normal attribute export."
                )
            previous_what_to_show = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                what_to_show_identifier,
            )

        generated_normal = None
        if use_show_alph_composite:
            show_alph_identifier = _find_show_alph_identifier(prepared.node_group)
            if not show_alph_identifier:
                raise ExportWorkflowError(
                    "ShowAlph input was not found on Geometry Nodes group. "
                    "Required for RBD alpha export."
                )
            previous_show_alph = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                show_alph_identifier,
            )

            try:
                _set_show_alph(prepared.node_group, temp_state.gn_modifier, False, reporter=report)
                color_width, color_height, generated_position_rgb = _render_display_attribute_pixels(
                    context,
                    scene,
                    view_layer,
                    temp_state,
                    prepared,
                    eval_frame,
                    None,
                    reporter=report,
                )

                _set_show_alph(prepared.node_group, temp_state.gn_modifier, True, reporter=report)
                alpha_width, alpha_height, generated_position_alpha = _render_display_attribute_pixels(
                    context,
                    scene,
                    view_layer,
                    temp_state,
                    prepared,
                    eval_frame,
                    None,
                    reporter=report,
                )
                if color_width != alpha_width or color_height != alpha_height:
                    raise ExportWorkflowError(
                        f"RGB pass and alpha pass dimensions do not match: "
                        f"{color_width}x{color_height} vs {alpha_width}x{alpha_height}."
                    )
                combined_pixels = _compose_rgb_and_alpha_pixels(
                    generated_position_rgb,
                    generated_position_alpha,
                )
                _save_float_exr_image(
                    scene,
                    position_path,
                    color_width,
                    color_height,
                    combined_pixels,
                )
                generated_position = position_path
            except ExportWorkflowError as exc:
                report(
                    {"WARNING"},
                    "In-memory Render Result capture failed for RBD alpha export. "
                    f"Falling back to compositor temp EXR files: {exc}",
                )
                render_layers = ensure_render_layers_node()
                generated_position_rgb = None
                generated_position_alpha = None
                try:
                    _set_show_alph(prepared.node_group, temp_state.gn_modifier, False, reporter=report)
                    generated_position_rgb = _render_display_attribute_texture(
                        context,
                        scene,
                        view_layer,
                        render_layers,
                        temp_state,
                        prepared,
                        prepared.output_directory,
                        position_prefix,
                        eval_frame,
                        None,
                        reporter=report,
                    )
                    alpha_prefix = f"{token}_Alpha_"
                    _set_show_alph(prepared.node_group, temp_state.gn_modifier, True, reporter=report)
                    generated_position_alpha = _render_display_attribute_texture(
                        context,
                        scene,
                        view_layer,
                        render_layers,
                        temp_state,
                        prepared,
                        prepared.output_directory,
                        alpha_prefix,
                        eval_frame,
                        None,
                        reporter=report,
                    )
                    _compose_rendered_exr_alpha(
                        scene,
                        position_path,
                        generated_position_rgb,
                        generated_position_alpha,
                        reporter=report,
                    )
                    generated_position = position_path
                finally:
                    for temp_path in (generated_position_rgb, generated_position_alpha):
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
        else:
            render_layers = ensure_render_layers_node()
            image_socket = _find_compositor_output_socket(render_layers, ("Image", "Combined"))
            if image_socket is None:
                available = [sock.name for sock in render_layers.outputs]
                available_text = ", ".join(available) if available else "(none)"
                raise ExportWorkflowError(
                    "Required compositor passes were not found. "
                    f"Need Image/Combined. Available Render Layers outputs: {available_text}"
                )
            generated_position = _render_display_attribute_texture(
                context,
                scene,
                view_layer,
                render_layers,
                temp_state,
                prepared,
                prepared.output_directory,
                position_prefix,
                eval_frame,
                WHAT_TO_SHOW_POSITION if requires_what_to_show else None,
                reporter=report,
            )
            if normal_path:
                generated_normal = _render_display_attribute_texture(
                    context,
                    scene,
                    view_layer,
                    render_layers,
                    temp_state,
                    prepared,
                    prepared.output_directory,
                    normal_prefix,
                    eval_frame,
                    WHAT_TO_SHOW_NORMAL,
                    reporter=report,
                )

        if generated_position != position_path and os.path.exists(position_path):
            os.remove(position_path)
        if normal_path and generated_normal and generated_normal != normal_path and os.path.exists(normal_path):
            os.remove(normal_path)

        if generated_position != position_path:
            os.replace(generated_position, position_path)
        if normal_path and generated_normal:
            os.replace(generated_normal, normal_path)

        report({"INFO"}, f"Rendered Position EXR: {position_path}")
        if normal_path:
            report({"INFO"}, f"Rendered Normal EXR: {normal_path}")
        return position_path, normal_path

    except RuntimeError as exc:
        raise ExportWorkflowError(f"Rendered EXR export failed: {exc}") from exc
    finally:
        if show_alph_identifier:
            try:
                temp_state.gn_modifier[show_alph_identifier] = bool(previous_show_alph)
            except Exception:
                pass
        if node_tree is not None:
            for node in created_nodes:
                try:
                    if node.name in node_tree.nodes:
                        node_tree.nodes.remove(node)
                except Exception:
                    pass

        if hasattr(view_layer, "material_override"):
            try:
                view_layer.material_override = previous_material_override
            except Exception:
                pass

        if what_to_show_identifier:
            try:
                temp_state.gn_modifier[what_to_show_identifier] = int(previous_what_to_show)
            except Exception:
                pass

        scene.render.engine = previous_engine
        _restore_scene_compositor_flags(scene, previous_compositor_flags)

        if hasattr(scene, "compositing_node_group"):
            try:
                scene.compositing_node_group = previous_compositing_node_group
            except Exception:
                pass

        if created_compositor_tree and node_tree is not None:
            try:
                if getattr(node_tree, "users", 0) == 0:
                    bpy.data.node_groups.remove(node_tree, do_unlink=True)
            except Exception:
                pass

        if previous_film_transparent is not None and hasattr(scene.render, "film_transparent"):
            scene.render.film_transparent = previous_film_transparent
        _restore_scene_image_settings(scene, previous_image_settings)
        _restore_cycles_export_settings(scene, previous_cycles_settings)

        _restore_scene_render_visibility(scene, previous_hide_render)
        _restore_scene_view_settings(
            scene,
            previous_view_transform,
            previous_look,
            previous_exposure,
            previous_gamma,
        )

        if override_material is not None:
            try:
                if getattr(override_material, "users", 0) == 0:
                    bpy.data.materials.remove(override_material, do_unlink=True)
            except Exception:
                pass

        scene.frame_set(previous_frame)

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _write_display_attribute_texture_from_geometry(
    context,
    scene,
    temp_state,
    prepared,
    output_path,
    evaluate_frame,
    what_to_show_mode,
    reporter=None,
):
    report = reporter if reporter else (lambda _level, _message: None)
    if what_to_show_mode:
        report({"INFO"}, f"Writing texels directly from Geometry Nodes for WhatToShow='{what_to_show_mode}'..." )
        _set_what_to_show_mode(prepared.node_group, temp_state.gn_modifier, what_to_show_mode, reporter=report)
    else:
        report({"INFO"}, "Writing texels directly from Geometry Nodes DisplayAttribute..." )

    scene.frame_set(int(evaluate_frame))

    pixels = _read_display_attribute_pixels_via_realized_instances_probe(
        context,
        scene,
        temp_state.helper_object,
        int(evaluate_frame),
        int(prepared.export_texture_width),
        int(prepared.export_texture_height),
        attribute_name="DisplayAttribute",
        uv_layer_name=temp_state.selected_uv_name,
        allow_partial=_is_rbd_mode(settings),
        reporter=report,
    )
    _save_float_exr_image(scene, output_path, int(prepared.export_texture_width), int(prepared.export_texture_height), pixels)


def _read_display_attribute_pixels_via_realized_instances_probe(
    context,
    scene,
    helper_object,
    evaluate_frame,
    texture_width,
    texture_height,
    attribute_name="DisplayAttribute",
    uv_layer_name=None,
    allow_partial=False,
    reporter=None,
):
    report = reporter if reporter else (lambda _level, _message: None)
    if texture_width <= 0 or texture_height <= 0:
        raise ExportWorkflowError("Texture resolution must be positive for direct texel export.")

    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    probe_object = None
    probe_group = None

    try:
        probe_object = helper_object.copy()
        if helper_object.data is not None:
            probe_object.data = helper_object.data.copy()
        probe_object.name = _unique_name("UVB_TexelProbeObject", bpy.data.objects.keys())
        scene.collection.objects.link(probe_object)

        probe_group = _create_realize_instances_probe_group()
        probe_modifier = probe_object.modifiers.new("UVB_RealizeProbe", "NODES")
        probe_modifier.node_group = probe_group

        if context.window:
            context.window.scene = scene
        scene.frame_set(int(evaluate_frame))

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = probe_object.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if eval_mesh is None:
            raise ExportWorkflowError("Failed to evaluate realized Geometry Nodes mesh for direct texel export.")

        try:
            report(
                {"INFO"},
                _describe_realized_texel_probe_mesh(
                    eval_mesh,
                    attribute_name,
                    texture_width,
                    texture_height,
                    uv_layer_name=uv_layer_name,
                    allow_partial=allow_partial,
                ),
            )
            return _rasterize_realized_attribute_mesh(
                eval_mesh,
                texture_width,
                texture_height,
                attribute_name,
                uv_layer_name=uv_layer_name,
                allow_partial=allow_partial,
                reporter=report,
            )
        finally:
            eval_obj.to_mesh_clear()
    finally:
        if probe_object is not None:
            mesh_data = probe_object.data
            try:
                bpy.data.objects.remove(probe_object, do_unlink=True)
            except Exception:
                pass

            if mesh_data is not None and getattr(mesh_data, "users", 0) == 0:
                try:
                    bpy.data.meshes.remove(mesh_data, do_unlink=True)
                except Exception:
                    pass

        if probe_group is not None and getattr(probe_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(probe_group, do_unlink=True)
            except Exception:
                pass

        scene.frame_set(previous_frame)
        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _get_mesh_uv_layer(mesh, preferred_name=None):
    uv_layers = getattr(mesh, "uv_layers", None)
    if uv_layers is None or len(uv_layers) == 0:
        return None

    if preferred_name:
        try:
            layer = uv_layers.get(str(preferred_name))
        except Exception:
            layer = None
        if layer is not None:
            return layer

    active = getattr(uv_layers, "active", None)
    if active is not None:
        return active

    try:
        return uv_layers[0]
    except Exception:
        return None


def _summarize_mesh_uv_layers(mesh):
    uv_layers = getattr(mesh, "uv_layers", None)
    if uv_layers is None or len(uv_layers) == 0:
        return "(none)"
    names = []
    for layer in uv_layers:
        name = str(getattr(layer, "name", ""))
        if name:
            names.append(name)
    return ", ".join(names) if names else "(unnamed)"


def _describe_realized_texel_probe_mesh(
    mesh,
    attribute_name,
    texture_width,
    texture_height,
    uv_layer_name=None,
    allow_partial=False,
):
    attributes = getattr(mesh, "attributes", None)
    attribute = None
    if attributes is not None and attribute_name in attributes:
        attribute = attributes[attribute_name]

    selected_uv = _get_mesh_uv_layer(mesh, uv_layer_name)
    selected_uv_name = getattr(selected_uv, "name", None) if selected_uv is not None else None
    attribute_domain = getattr(attribute, "domain", "(missing)") if attribute is not None else "(missing)"
    attribute_data_type = getattr(attribute, "data_type", "(missing)") if attribute is not None else "(missing)"
    attribute_count = len(attribute.data) if attribute is not None else 0
    return (
        "Direct texel probe mesh summary: "
        f"verts={len(mesh.vertices)}, polys={len(mesh.polygons)}, loops={len(mesh.loops)}, "
        f"target={texture_width}x{texture_height}, "
        f"attribute='{attribute_name}' domain={attribute_domain} type={attribute_data_type} count={attribute_count}, "
        f"requested_uv='{uv_layer_name}', selected_uv='{selected_uv_name}', "
        f"uv_layers=[{_summarize_mesh_uv_layers(mesh)}], allow_partial={bool(allow_partial)}"
    )


def _resolve_polygon_pixel_from_uv(mesh, polygon, texture_width, texture_height, uv_layer_name=None):
    uv_layer = _get_mesh_uv_layer(mesh, uv_layer_name)
    if uv_layer is None:
        return None

    loop_indices = list(getattr(polygon, "loop_indices", ()))
    if not loop_indices:
        return None

    total_u = 0.0
    total_v = 0.0
    sample_count = 0

    for loop_index in loop_indices:
        try:
            uv = uv_layer.data[int(loop_index)].uv
        except Exception:
            continue
        total_u += float(uv[0])
        total_v += float(uv[1])
        sample_count += 1

    if sample_count <= 0:
        return None

    u = total_u / float(sample_count)
    v = total_v / float(sample_count)
    pixel_x = max(0, min(texture_width - 1, int(u * float(texture_width))))
    pixel_y = max(0, min(texture_height - 1, int(v * float(texture_height))))
    return pixel_x, pixel_y


def _resolve_polygon_pixel_from_position(mesh, polygon, texture_width, texture_height):
    vertex_indices = list(polygon.vertices)
    if not vertex_indices:
        return None

    center_x = sum(float(mesh.vertices[index].co.x) for index in vertex_indices) / float(len(vertex_indices))
    center_y = sum(float(mesh.vertices[index].co.y) for index in vertex_indices) / float(len(vertex_indices))
    pixel_x = int(round(((center_x + 0.5) * float(texture_width)) - 0.5))
    pixel_y = int(round(((center_y + 0.5) * float(texture_height)) - 0.5))
    if pixel_x < 0 or pixel_x >= texture_width or pixel_y < 0 or pixel_y >= texture_height:
        return None
    return pixel_x, pixel_y


def _rasterize_realized_attribute_mesh_with_locator(
    mesh,
    texture_width,
    texture_height,
    attribute,
    pixel_locator,
):
    pixels = [0.0] * (texture_width * texture_height * 4)
    write_counts = [0] * (texture_width * texture_height)
    mapped_polygons = 0

    for poly in mesh.polygons:
        pixel = pixel_locator(mesh, poly, texture_width, texture_height)
        if pixel is None:
            continue
        mapped_polygons += 1

        pixel_x, pixel_y = pixel
        rgba = _average_polygon_attribute_rgba(attribute, poly)
        pixel_index = (pixel_y * texture_width) + pixel_x
        base = pixel_index * 4
        previous_count = write_counts[pixel_index]
        if previous_count == 0:
            pixels[base:base + 4] = rgba
        else:
            total = float(previous_count + 1)
            for channel_index, value in enumerate(rgba):
                current_value = pixels[base + channel_index]
                pixels[base + channel_index] = ((current_value * float(previous_count)) + float(value)) / total
        write_counts[pixel_index] = previous_count + 1

    missing_count = sum(1 for count in write_counts if count == 0)
    filled_texels = sum(1 for count in write_counts if count > 0)
    overlapping_texels = sum(1 for count in write_counts if count > 1)
    max_stack = max(write_counts) if write_counts else 0
    return pixels, {
        "missing_count": missing_count,
        "filled_texels": filled_texels,
        "overlapping_texels": overlapping_texels,
        "max_stack": max_stack,
        "mapped_polygons": mapped_polygons,
        "total_polygons": len(mesh.polygons),
    }


def _format_texel_attempt_stats(label, stats, texture_width, texture_height):
    total_texels = texture_width * texture_height
    filled = int(stats.get("filled_texels", 0))
    missing = int(stats.get("missing_count", total_texels))
    overlap = int(stats.get("overlapping_texels", 0))
    max_stack = int(stats.get("max_stack", 0))
    mapped_polygons = int(stats.get("mapped_polygons", 0))
    total_polygons = int(stats.get("total_polygons", 0))
    fill_percent = (float(filled) / float(total_texels) * 100.0) if total_texels > 0 else 0.0
    return (
        f"{label}: filled={filled}/{total_texels} ({fill_percent:.2f}%), "
        f"missing={missing}, overlapping_texels={overlap}, max_stack={max_stack}, "
        f"mapped_polygons={mapped_polygons}/{total_polygons}"
    )


def _rasterize_realized_attribute_mesh(
    mesh,
    texture_width,
    texture_height,
    attribute_name,
    uv_layer_name=None,
    allow_partial=False,
    reporter=None,
):
    report = reporter if reporter else (lambda _level, _message: None)
    attributes = getattr(mesh, "attributes", None)
    if attributes is None or attribute_name not in attributes:
        available = []
        if attributes is not None:
            available = [str(getattr(attr, "name", "")) for attr in attributes]
        available_text = ", ".join(name for name in available if name) if available else "(none)"
        raise ExportWorkflowError(
            f"Attribute '{attribute_name}' was not found on the realized Geometry Nodes mesh. "
            f"Available attributes: {available_text}"
        )

    attribute = attributes[attribute_name]

    attempts = []
    if _get_mesh_uv_layer(mesh, uv_layer_name) is not None:
        attempts.append(
            (
                f"UV layer '{getattr(_get_mesh_uv_layer(mesh, uv_layer_name), 'name', uv_layer_name)}'",
                lambda m, p, w, h: _resolve_polygon_pixel_from_uv(m, p, w, h, uv_layer_name=uv_layer_name),
            )
        )
    attempts.append(
        (
            "helper-plane XY positions",
            _resolve_polygon_pixel_from_position,
        )
    )

    best_pixels = None
    best_missing = None
    best_label = None
    attempt_summaries = []

    for label, locator in attempts:
        pixels, stats = _rasterize_realized_attribute_mesh_with_locator(
            mesh,
            texture_width,
            texture_height,
            attribute,
            locator,
        )
        missing_count = int(stats["missing_count"])
        attempt_text = _format_texel_attempt_stats(label, stats, texture_width, texture_height)
        attempt_summaries.append(attempt_text)
        report({"INFO"}, f"Direct texel mapping attempt: {attempt_text}")
        if best_missing is None or missing_count < best_missing:
            best_pixels = pixels
            best_missing = missing_count
            best_label = label
            best_stats = stats
        if missing_count == 0:
            return pixels

    if allow_partial and best_pixels is not None:
        report(
            {"INFO"},
            "Direct texel export accepted sparse occupancy: "
            f"{_format_texel_attempt_stats(best_label, best_stats, texture_width, texture_height)}",
        )
        return best_pixels

    raise ExportWorkflowError(
        "Direct texel export could not map all pixels from the realized Geometry Nodes mesh. "
        f"Best method: {best_label}. Missing texels: {best_missing}/{texture_width * texture_height}. "
        f"Attempts: {' | '.join(attempt_summaries)}"
    )


def _average_polygon_attribute_rgba(attribute, polygon):
    indices = _resolve_polygon_attribute_indices(attribute, polygon)
    if not indices:
        return (0.0, 0.0, 0.0, 1.0)

    totals = [0.0, 0.0, 0.0, 0.0]
    for index in indices:
        rgba = _attribute_sample_to_rgba(attribute.data[index])
        for channel_index, value in enumerate(rgba):
            totals[channel_index] += float(value)

    inv = 1.0 / float(len(indices))
    return tuple(value * inv for value in totals)


def _resolve_polygon_attribute_indices(attribute, polygon):
    domain = getattr(attribute, "domain", "")
    if domain == "POINT":
        return list(polygon.vertices)
    if domain == "CORNER":
        return list(polygon.loop_indices)
    if domain == "FACE":
        return [int(polygon.index)]
    return []


def _attribute_sample_to_rgba(sample):
    if hasattr(sample, "color"):
        values = [float(value) for value in sample.color]
        if len(values) >= 4:
            return tuple(values[:4])
        if len(values) == 3:
            return (values[0], values[1], values[2], 1.0)

    if hasattr(sample, "vector"):
        values = [float(value) for value in sample.vector]
        if len(values) >= 3:
            return (values[0], values[1], values[2], 1.0)
        if len(values) == 2:
            return (values[0], values[1], 0.0, 1.0)

    if hasattr(sample, "uv"):
        values = [float(value) for value in sample.uv]
        if len(values) >= 2:
            return (values[0], values[1], 0.0, 1.0)

    if hasattr(sample, "value"):
        value = float(sample.value)
        return (value, value, value, 1.0)

    if hasattr(sample, "x") and hasattr(sample, "y"):
        x_value = float(sample.x)
        y_value = float(sample.y)
        z_value = float(getattr(sample, "z", 0.0))
        return (x_value, y_value, z_value, 1.0)

    return (0.0, 0.0, 0.0, 1.0)


def _compose_rgb_and_alpha_pixels(color_pixels, alpha_pixels):
    if len(color_pixels) != len(alpha_pixels):
        raise ExportWorkflowError("RGB pass and alpha pass pixel buffers must be the same length.")
    if len(color_pixels) % 4 != 0:
        raise ExportWorkflowError("Pixel buffers must contain RGBA tuples.")

    combined = list(color_pixels)
    for base in range(0, len(combined), 4):
        alpha_value = max(
            float(alpha_pixels[base + 0]),
            float(alpha_pixels[base + 1]),
            float(alpha_pixels[base + 2]),
        )
        combined[base + 3] = alpha_value
    return combined


def _get_render_result_image():
    for image in bpy.data.images:
        if getattr(image, "type", "") == "RENDER_RESULT":
            return image
    return bpy.data.images.get("Render Result")


def _copy_image_pixels(image, source_label):
    if image is None:
        raise ExportWorkflowError(f"{source_label} image was not available after render.")

    try:
        image.update()
    except Exception:
        pass

    width = int(image.size[0]) if len(image.size) >= 2 else 0
    height = int(image.size[1]) if len(image.size) >= 2 else 0
    if width <= 0 or height <= 0:
        raise ExportWorkflowError(
            f"{source_label} image had invalid dimensions: {width}x{height}."
        )

    expected = width * height * 4
    pixels = [0.0] * expected
    try:
        image.pixels.foreach_get(pixels)
    except Exception as exc:
        raise ExportWorkflowError(
            f"Failed to read RGBA pixels from {source_label}: {exc}"
        ) from exc

    if len(pixels) != expected:
        raise ExportWorkflowError(
            f"{source_label} pixel buffer size mismatch: expected {expected}, got {len(pixels)}."
        )

    return width, height, pixels


def _render_display_attribute_pixels(
    context,
    scene,
    view_layer,
    temp_state,
    prepared,
    evaluate_frame,
    what_to_show_mode,
    reporter=None,
):
    report = reporter if reporter else (lambda _level, _message: None)
    if what_to_show_mode:
        report({"INFO"}, f"Rendering DisplayAttribute for WhatToShow='{what_to_show_mode}'...")
        _set_what_to_show_mode(prepared.node_group, temp_state.gn_modifier, what_to_show_mode, reporter=report)
    else:
        report({"INFO"}, "Rendering DisplayAttribute...")

    scene.frame_set(int(evaluate_frame))
    with context.temp_override(scene=scene, view_layer=view_layer):
        result = bpy.ops.render.render(write_still=False, use_viewport=False)

    if "FINISHED" not in result:
        raise ExportWorkflowError(
            f"Render operation did not finish successfully for WhatToShow='{what_to_show_mode}'."
        )

    return _copy_image_pixels(_get_render_result_image(), "Render Result")


def _load_image_pixels_from_file(filepath):
    deadline = time.monotonic() + 5.0
    last_problem = "unknown"

    while True:
        if not os.path.exists(filepath):
            last_problem = "file does not exist yet"
        else:
            try:
                file_size = int(os.path.getsize(filepath))
            except OSError as exc:
                file_size = -1
                last_problem = f"could not read file size: {exc}"
            else:
                if file_size <= 0:
                    last_problem = f"file size is {file_size} bytes"
                else:
                    image = None
                    try:
                        image = bpy.data.images.load(filepath, check_existing=False)
                        try:
                            image.reload()
                        except Exception:
                            pass
                        try:
                            image.update()
                        except Exception:
                            pass

                        width = int(image.size[0]) if len(image.size) >= 2 else 0
                        height = int(image.size[1]) if len(image.size) >= 2 else 0
                        if width > 0 and height > 0:
                            expected = width * height * 4
                            pixels = [0.0] * expected
                            image.pixels.foreach_get(pixels)
                            return width, height, pixels

                        last_problem = (
                            f"loaded image dimensions were {width}x{height} "
                            f"(file size {file_size} bytes)"
                        )
                    except Exception as exc:
                        last_problem = f"image load failed: {exc}"
                    finally:
                        if image is not None:
                            try:
                                bpy.data.images.remove(image, do_unlink=True)
                            except Exception:
                                pass

        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)

    raise ExportWorkflowError(
        "Loaded EXR image did not become readable within 5.0 seconds. "
        f"Path: {filepath}. Last observed problem: {last_problem}."
    )


def _describe_debug_file_state(filepath):
    exists = os.path.exists(filepath)
    if not exists:
        return f"path='{filepath}', exists=False"
    try:
        size = os.path.getsize(filepath)
    except OSError as exc:
        return f"path='{filepath}', exists=True, size_error={exc}"
    return f"path='{filepath}', exists=True, size={size} bytes"


def _compose_rendered_exr_alpha(scene, output_path, color_path, alpha_path, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    report({"INFO"}, f"Compositing temp EXR RGB pass: {_describe_debug_file_state(color_path)}")
    report({"INFO"}, f"Compositing temp EXR Alpha pass: {_describe_debug_file_state(alpha_path)}")
    color_width, color_height, color_pixels = _load_image_pixels_from_file(color_path)
    alpha_width, alpha_height, alpha_pixels = _load_image_pixels_from_file(alpha_path)
    if color_width != alpha_width or color_height != alpha_height:
        raise ExportWorkflowError(
            f"RGB pass and alpha pass dimensions do not match: "
            f"{color_width}x{color_height} vs {alpha_width}x{alpha_height}."
        )

    combined_pixels = _compose_rgb_and_alpha_pixels(color_pixels, alpha_pixels)
    _save_float_exr_image(scene, output_path, color_width, color_height, combined_pixels)


def _save_float_exr_image(scene, output_path, width, height, pixels):
    image = bpy.data.images.new(
        _unique_name("UVB_ExportImage", bpy.data.images.keys()),
        width=int(width),
        height=int(height),
        alpha=True,
        float_buffer=True,
        is_data=True,
    )

    try:
        if hasattr(image, "alpha_mode"):
            try:
                image.alpha_mode = "STRAIGHT"
            except Exception:
                pass
        if hasattr(image, "colorspace_settings") and hasattr(image.colorspace_settings, "name"):
            try:
                image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass

        image.pixels.foreach_set(pixels)
        image.filepath_raw = output_path
        image.file_format = "OPEN_EXR"

        if os.path.exists(output_path):
            os.remove(output_path)
        image.save_render(output_path, scene=scene)
    finally:
        try:
            bpy.data.images.remove(image, do_unlink=True)
        except Exception:
            pass


def _render_display_attribute_texture(
    context,
    scene,
    view_layer,
    render_layers,
    temp_state,
    prepared,
    output_directory,
    output_prefix,
    evaluate_frame,
    what_to_show_mode,
    reporter=None,
):
    report = reporter if reporter else (lambda _level, _message: None)
    if what_to_show_mode:
        report({"INFO"}, f"Rendering DisplayAttribute for WhatToShow='{what_to_show_mode}'...")
        _set_what_to_show_mode(prepared.node_group, temp_state.gn_modifier, what_to_show_mode, reporter=report)
    else:
        report({"INFO"}, "Rendering DisplayAttribute...")
    scene.frame_set(int(evaluate_frame))

    node_tree = render_layers.id_data
    nodes = node_tree.nodes
    links = node_tree.links
    output_node = nodes.new("CompositorNodeOutputFile")
    alpha_convert_node = nodes.new("CompositorNodePremulKey")
    set_alpha_node = nodes.new("CompositorNodeSetAlpha")
    image_link = None
    alpha_link = None
    premul_link = None
    output_link = None

    try:
        image_socket = _find_compositor_output_socket(render_layers, ("Image", "Combined"))
        alpha_socket = _find_compositor_output_socket(render_layers, ("Alpha",))
        if image_socket is None:
            available = [sock.name for sock in render_layers.outputs]
            available_text = ", ".join(available) if available else "(none)"
            raise ExportWorkflowError(
                "Image/Combined pass was not found for DisplayAttribute render. "
                f"Available Render Layers outputs: {available_text}"
            )
        if alpha_socket is None:
            available = [sock.name for sock in render_layers.outputs]
            available_text = ", ".join(available) if available else "(none)"
            raise ExportWorkflowError(
                "Alpha pass was not found for DisplayAttribute render. "
                f"Available Render Layers outputs: {available_text}"
            )

        _set_premul_key_to_straight(alpha_convert_node)
        _set_set_alpha_replace(set_alpha_node)

        output_input = _configure_file_output_node_for_exr(
            output_node,
            output_directory,
            output_prefix,
            socket_type="RGBA",
            color_mode="RGBA",
        )
        _remove_file_output_candidates(output_directory, output_prefix)
        image_link = links.new(image_socket, alpha_convert_node.inputs[0])
        premul_link = links.new(alpha_convert_node.outputs[0], set_alpha_node.inputs[0])
        alpha_link = links.new(alpha_socket, set_alpha_node.inputs[1])
        output_link = links.new(set_alpha_node.outputs[0], output_input)

        with context.temp_override(scene=scene, view_layer=view_layer):
            result = bpy.ops.render.render(write_still=False, use_viewport=False)

        if "FINISHED" not in result:
            raise ExportWorkflowError(
                f"Render operation did not finish successfully for WhatToShow='{what_to_show_mode}'."
            )

        generated_path = _find_latest_file_output(output_directory, output_prefix)
        if generated_path is None:
            raise ExportWorkflowError(
                "Rendered EXR file was not generated by compositor file output. "
                f"WhatToShow='{what_to_show_mode}', prefix='{output_prefix}'."
            )
        return generated_path
    except RuntimeError as exc:
        raise ExportWorkflowError(
            f"Rendered EXR export failed for WhatToShow='{what_to_show_mode}': {exc}"
        ) from exc
    finally:
        try:
            if image_link is not None:
                links.remove(image_link)
        except Exception:
            pass
        try:
            if alpha_link is not None:
                links.remove(alpha_link)
        except Exception:
            pass
        try:
            if premul_link is not None:
                links.remove(premul_link)
        except Exception:
            pass
        try:
            if output_link is not None:
                links.remove(output_link)
        except Exception:
            pass
        try:
            if set_alpha_node.name in nodes:
                nodes.remove(set_alpha_node)
        except Exception:
            pass
        try:
            if alpha_convert_node.name in nodes:
                nodes.remove(alpha_convert_node)
        except Exception:
            pass
        try:
            if output_node.name in nodes:
                nodes.remove(output_node)
        except Exception:
            pass


def _capture_scene_image_settings(scene):
    image_settings = getattr(scene.render, "image_settings", None)
    if image_settings is None:
        return {}

    state = {}
    for attr in ("file_format", "color_mode", "color_depth", "exr_codec", "compression", "color_management"):
        if not hasattr(image_settings, attr):
            continue
        try:
            state[attr] = getattr(image_settings, attr)
        except Exception:
            pass
    return state


def _capture_cycles_export_settings(scene):
    cycles = getattr(scene, "cycles", None)
    if cycles is None:
        return {}

    state = {}
    for attr in (
        "samples",
        "use_denoising",
        "pixel_filter_type",
        "filter_width",
        "diffuse_bounces",
        "glossy_bounces",
        "transmission_bounces",
        "volume_bounces",
        "transparent_max_bounces",
        "max_bounces",
    ):
        if not hasattr(cycles, attr):
            continue
        try:
            state[attr] = getattr(cycles, attr)
        except Exception:
            pass
    return state


def _apply_scene_exr_image_settings(scene):
    image_settings = getattr(scene.render, "image_settings", None)
    if image_settings is None:
        return

    try:
        image_settings.file_format = "OPEN_EXR"
    except Exception:
        pass
    try:
        image_settings.color_mode = "RGBA"
    except Exception:
        pass
    try:
        image_settings.color_depth = "16"
    except Exception:
        pass
    if hasattr(image_settings, "exr_codec"):
        try:
            image_settings.exr_codec = "ZIP"
        except Exception:
            pass
    if hasattr(image_settings, "compression"):
        try:
            image_settings.compression = 0
        except Exception:
            pass
    if hasattr(image_settings, "color_management"):
        try:
            image_settings.color_management = "OVERRIDE"
        except Exception:
            pass


def _apply_cycles_export_settings(scene):
    cycles = getattr(scene, "cycles", None)
    if cycles is None:
        return

    for attr, value in (
        ("samples", 1),
        ("use_denoising", False),
        ("pixel_filter_type", "BOX"),
        ("filter_width", 0.01),
        ("diffuse_bounces", 0),
        ("glossy_bounces", 0),
        ("transmission_bounces", 0),
        ("volume_bounces", 0),
        ("max_bounces", 0),
    ):
        if not hasattr(cycles, attr):
            continue
        try:
            setattr(cycles, attr, value)
        except Exception:
            pass


def _restore_scene_image_settings(scene, state):
    image_settings = getattr(scene.render, "image_settings", None)
    if image_settings is None:
        return

    for attr, value in state.items():
        if not hasattr(image_settings, attr):
            continue
        try:
            setattr(image_settings, attr, value)
        except Exception:
            pass


def _restore_cycles_export_settings(scene, state):
    cycles = getattr(scene, "cycles", None)
    if cycles is None:
        return

    for attr, value in state.items():
        if not hasattr(cycles, attr):
            continue
        try:
            setattr(cycles, attr, value)
        except Exception:
            pass


def _set_premul_key_to_straight(node):
    for input_socket in getattr(node, "inputs", []):
        if getattr(input_socket, "name", "") != "Type":
            continue
        try:
            input_socket.default_value = "To Straight"
        except Exception:
            pass
        return


def _set_set_alpha_replace(node):
    for input_socket in getattr(node, "inputs", []):
        if getattr(input_socket, "name", "") != "Type":
            continue
        try:
            input_socket.default_value = "Replace Alpha"
        except Exception:
            pass
        return


def _set_scene_render_visibility_for_texture_export(scene, helper_object, camera_object):
    for obj in scene.objects:
        obj.hide_render = obj not in {helper_object, camera_object}


def _restore_scene_render_visibility(scene, previous_hide_render):
    for obj in scene.objects:
        if obj.name not in previous_hide_render:
            continue
        try:
            obj.hide_render = bool(previous_hide_render[obj.name])
        except Exception:
            pass


def _set_view_transform_if_available(scene, transform_name):
    if not hasattr(scene, "view_settings") or not hasattr(scene.view_settings, "view_transform"):
        return
    try:
        scene.view_settings.view_transform = transform_name
    except Exception:
        pass


def _set_scene_look_if_available(scene, look_name):
    if not hasattr(scene, "view_settings") or not hasattr(scene.view_settings, "look"):
        return
    try:
        scene.view_settings.look = look_name
    except Exception:
        pass


def _restore_scene_view_settings(scene, view_transform, look, exposure, gamma):
    if not hasattr(scene, "view_settings"):
        return

    if view_transform is not None and hasattr(scene.view_settings, "view_transform"):
        try:
            scene.view_settings.view_transform = view_transform
        except Exception:
            pass

    if look is not None and hasattr(scene.view_settings, "look"):
        try:
            scene.view_settings.look = look
        except Exception:
            pass

    if exposure is not None and hasattr(scene.view_settings, "exposure"):
        try:
            scene.view_settings.exposure = float(exposure)
        except Exception:
            pass

    if gamma is not None and hasattr(scene.view_settings, "gamma"):
        try:
            scene.view_settings.gamma = float(gamma)
        except Exception:
            pass


def _create_attribute_override_material(attribute_name, attribute_type="INSTANCER"):
    material_name = _unique_name("UVB_AttributeOverride", bpy.data.materials.keys())
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True

    node_tree = material.node_tree
    if node_tree is None:
        raise ExportWorkflowError("Failed to create node tree for attribute override material.")

    nodes = node_tree.nodes
    links = node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix_shader = nodes.new("ShaderNodeMixShader")
    attribute = nodes.new("ShaderNodeAttribute")

    attribute.attribute_name = attribute_name
    if hasattr(attribute, "attribute_type"):
        try:
            attribute.attribute_type = attribute_type
        except Exception:
            pass

    emission.inputs["Strength"].default_value = 1.0

    color_output = None
    for socket_name in ("Vector", "Color"):
        socket = attribute.outputs.get(socket_name)
        if socket is not None:
            color_output = socket
            break
    if color_output is None:
        raise ExportWorkflowError("Attribute node did not expose a usable color/vector output.")

    alpha_output = None
    for socket_name in ("Alpha", "Fac"):
        socket = attribute.outputs.get(socket_name)
        if socket is not None:
            alpha_output = socket
            break

    links.new(color_output, emission.inputs["Color"])
    links.new(transparent.outputs["BSDF"], mix_shader.inputs[1])
    links.new(emission.outputs["Emission"], mix_shader.inputs[2])
    if alpha_output is not None:
        links.new(alpha_output, mix_shader.inputs[0])
    else:
        mix_shader.inputs[0].default_value = 1.0
    links.new(mix_shader.outputs["Shader"], output.inputs["Surface"])
    return material


def _configure_file_output_node_for_exr(node, base_path, slot_path_prefix, socket_type="RGBA", color_mode="RGBA"):
    if hasattr(node, "base_path"):
        node.base_path = base_path
        node.format.file_format = "OPEN_EXR"
        node.format.color_mode = color_mode
        node.format.color_depth = "16"
        node.format.exr_codec = "ZIP"

        if len(node.file_slots) == 0:
            node.file_slots.new("Image")

        node.file_slots[0].path = slot_path_prefix
        return node.inputs[0]

    if hasattr(node, "directory"):
        node.directory = base_path
        if hasattr(node, "file_name"):
            node.file_name = slot_path_prefix

        output_item, input_socket = _ensure_file_output_item(node, socket_type, "Image")
        output_item.override_node_format = True
        output_item.format.file_format = "OPEN_EXR"
        output_item.format.color_mode = color_mode
        output_item.format.color_depth = "16"
        output_item.format.exr_codec = "ZIP"

        if hasattr(output_item, "save_as_render"):
            output_item.save_as_render = False
        if hasattr(node, "save_as_render"):
            node.save_as_render = False

        return input_socket

    raise ExportWorkflowError(
        "Compositor File Output node API is unsupported in this Blender build. "
        f"Available attrs: {', '.join(sorted(name for name in dir(node) if not name.startswith('_')))}"
    )


def _ensure_file_output_item(node, socket_type, item_name):
    items = getattr(node, "file_output_items", None)
    if items is None:
        raise ExportWorkflowError("File Output node does not expose file_output_items in this Blender build.")

    if len(items) == 0:
        items.new(socket_type, item_name)
    else:
        existing = items[0]
        if hasattr(existing, "socket_type") and existing.socket_type != socket_type:
            items.clear()
            items.new(socket_type, item_name)
        elif hasattr(existing, "name") and existing.name != item_name:
            existing.name = item_name

    for socket in node.inputs:
        if getattr(socket, "name", "") == item_name:
            return items[0], socket

    if len(node.inputs) > 0:
        return items[0], node.inputs[0]

    raise ExportWorkflowError("File Output node did not expose a usable input socket.")


def _find_compositor_output_socket(render_layers_node, aliases):
    for alias in aliases:
        target = _normalize_socket_name(alias)
        for socket in render_layers_node.outputs:
            if _normalize_socket_name(socket.name) == target:
                return socket
    return None


def _file_output_candidates(base_path, prefix):
    candidates = []
    patterns = [
        os.path.join(base_path, f"{prefix}*.exr"),
        os.path.join(base_path, f"{prefix}*.EXR"),
    ]

    for pattern in patterns:
        for path in glob.glob(pattern):
            if path not in candidates:
                candidates.append(path)
    return candidates


def _remove_file_output_candidates(base_path, prefix):
    for path in _file_output_candidates(base_path, prefix):
        try:
            os.remove(path)
        except Exception:
            pass


def _find_latest_file_output(base_path, prefix):
    candidates = _file_output_candidates(base_path, prefix)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _export_json_metadata(context, prepared, temp_state, settings, position_texture, normal_texture):
    filename = f"{prepared.asset_name}_VAT.json"
    json_path = os.path.join(prepared.output_directory, filename)

    duration = _compute_total_sampled_frame_count(
        int(settings.start_frame),
        int(settings.end_frame),
        int(prepared.frame_drop_step),
    )
    _ensure_end_frame_min_max_attributes(
        context,
        temp_state,
        prepared,
        settings,
        int(settings.end_frame),
        None,
    )

    end_frame_attrs = getattr(prepared, "end_frame_attributes", {}) or {}
    pos_min_attr_name, pos_max_attr_name = _get_position_range_attribute_names(settings)
    size_attr_names = _get_size_range_attribute_names(settings)
    min_value = end_frame_attrs.get(_attribute_lookup_key(pos_min_attr_name))
    max_value = end_frame_attrs.get(_attribute_lookup_key(pos_max_attr_name))

    if min_value is None or max_value is None:
        raise ExportWorkflowError(
            f"Geometry Nodes attributes '{pos_min_attr_name}' and '{pos_max_attr_name}' were not found in End Frame readback even after JSON-stage fallback."
        )

    texture_names = [os.path.basename(position_texture)]
    if normal_texture:
        texture_names.append(os.path.basename(normal_texture))

    payload = {
        "Vat_NumFrames": duration,
        "VatMode": _get_vat_mode_index(settings),
        "Vat_UVIndex": _get_uv_map_index(settings),
        "Vat_OriginalFPS": float(prepared.source_fps),
        "Vat_FrameStepping": int(prepared.frame_drop_step),
        "exported_texture_names": texture_names,
        "exported_object_names": [obj.name for obj in prepared.source_objects],
        "Vat_TextureSize": _json_texture_size_value(
            prepared.export_texture_width,
            prepared.export_texture_height,
        ),
        "Vat_PositionRangeMin": _json_vector3(min_value),
        "Vat_PositionRangeMax": _json_vector3(max_value),
    }

    if _is_rbd_mode(settings) and size_attr_names:
        payload["Vat_SkipScale"] = bool(_get_rbd_skip_scale(settings))
        size_min_value = end_frame_attrs.get(_attribute_lookup_key(size_attr_names[0]))
        size_max_value = end_frame_attrs.get(_attribute_lookup_key(size_attr_names[1]))
        if size_min_value is None or size_max_value is None:
            raise ExportWorkflowError(
                f"Geometry Nodes attributes '{size_attr_names[0]}' and '{size_attr_names[1]}' are required for RBD JSON export."
            )
        payload["Vat_SizeRangeMin"] = _json_vector3(size_min_value)
        payload["Vat_SizeRangeMax"] = _json_vector3(size_max_value)

    json_text = json.dumps(payload, indent=2)
    with open(json_path, "w", encoding="utf-8") as handle:
        handle.write(json_text)

    return json_path, json_text


def _compute_total_sampled_frame_count(start_frame, end_frame, frame_drop_step):
    step = max(1, int(frame_drop_step))
    frame_span = max(1, int(end_frame) - int(start_frame) + 1)
    return int((frame_span + step - 1) // step)


def _export_mesh_fbx(context, prepared, temp_state, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    filename = f"{prepared.asset_name}.fbx"
    mesh_path = os.path.join(prepared.output_directory, filename)
    scene = temp_state.scene
    view_layer = scene.view_layers[0]
    eval_frame = int(settings.end_frame)

    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    previous_selection = []
    previous_active = None
    renamed_conflicts = []
    export_object = None
    show_export_identifier = None
    previous_show_export_value = None
    skip_grid_identifier = None
    previous_skip_grid_value = None
    uv_name_identifier = None
    previous_uv_name_value = None

    try:
        if context.window:
            context.window.scene = scene

        previous_selection = [obj for obj in scene.objects if obj.select_get()]
        previous_active = view_layer.objects.active
        scene.frame_set(eval_frame)

        if context.mode != "OBJECT":
            try:
                with context.temp_override(scene=scene, view_layer=view_layer):
                    bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass

        resolved = _resolve_input_socket_identifiers(prepared.node_group)
        show_export_identifier = _find_show_export_mesh_identifier(prepared.node_group)
        uv_name_identifier = _find_uv_name_identifier(prepared.node_group)
        skip_grid_identifier = _find_socket_identifier(
            resolved,
            ("SkipGridTransfer", "Skip Grid Transfer"),
        )

        if show_export_identifier:
            previous_show_export_value = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                show_export_identifier,
            )
        if uv_name_identifier:
            previous_uv_name_value = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                uv_name_identifier,
            )
        if skip_grid_identifier:
            previous_skip_grid_value = _read_modifier_input_value(
                temp_state.gn_modifier,
                prepared.node_group,
                skip_grid_identifier,
            )

        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
        _set_uv_name_input(prepared.node_group, temp_state.gn_modifier, temp_state.selected_uv_name, reporter=report)
        _set_show_export_mesh(prepared.node_group, temp_state.gn_modifier, True, reporter=report)
        scene.frame_set(eval_frame)
        if _is_rbd_mode(settings):
            report({"INFO"}, "Creating FBX export mesh from VAT_HelperPlane with ShowExportMesh enabled...")
            export_object = _create_evaluated_export_mesh_snapshot(
                context,
                scene,
                temp_state.helper_object,
            )
            export_object.animation_data_clear()
            renamed_conflicts = _reserve_exact_object_name(prepared.asset_name, exclude_object=export_object)
            export_object.name = prepared.asset_name
        else:
            export_object = temp_state.helper_object.copy()
            if temp_state.helper_object.data is not None:
                export_object.data = temp_state.helper_object.data.copy()
            export_object.animation_data_clear()
            scene.collection.objects.link(export_object)

            renamed_conflicts = _reserve_exact_object_name(prepared.asset_name, exclude_object=export_object)
            export_object.name = prepared.asset_name

            export_modifier = _find_nodes_modifier(export_object, constants.TEMP_GN_MODIFIER_NAME)
            if export_modifier is None:
                raise ExportWorkflowError("Geometry Nodes modifier was not found on VAT_HelperPlane for FBX export.")

            _set_skip_grid_transfer(prepared.node_group, export_modifier, False, report)
            _set_uv_name_input(prepared.node_group, export_modifier, temp_state.selected_uv_name, reporter=report)
            _set_show_export_mesh(prepared.node_group, export_modifier, True, reporter=report)
            scene.frame_set(eval_frame)

            for modifier in list(export_object.modifiers):
                _select_only_object_in_scene(context, scene, view_layer, export_object)
                with context.temp_override(
                    scene=scene,
                    view_layer=view_layer,
                    active_object=export_object,
                    object=export_object,
                    selected_objects=[export_object],
                    selected_editable_objects=[export_object],
                ):
                    result = bpy.ops.object.modifier_apply(modifier=modifier.name)
                if "FINISHED" not in result:
                    raise ExportWorkflowError(
                        f"Failed to apply modifier '{modifier.name}' on VAT_HelperPlane for FBX export."
                    )

        _ensure_export_mesh_uv_layer(export_object, temp_state.selected_uv_name, _get_uv_map_index(settings))

        if getattr(export_object, "data", None) is None or len(export_object.data.vertices) == 0:
            raise ExportWorkflowError("Export mesh is empty after applying VAT_HelperPlane modifiers.")

        _select_only_object_in_scene(context, scene, view_layer, export_object)
        with context.temp_override(
            scene=scene,
            view_layer=view_layer,
            active_object=export_object,
            object=export_object,
            selected_objects=[export_object],
            selected_editable_objects=[export_object],
        ):
            result = bpy.ops.export_scene.fbx(
                filepath=mesh_path,
                use_selection=True,
                bake_anim=False,
                add_leaf_bones=False,
            )
        if "FINISHED" not in result:
            raise ExportWorkflowError("FBX export did not complete successfully.")
    except RuntimeError as exc:
        raise ExportWorkflowError(f"FBX export failed: {exc}") from exc
    finally:
        if export_object is not None:
            export_mesh_data = export_object.data
            try:
                bpy.data.objects.remove(export_object, do_unlink=True)
            except Exception:
                pass
            if export_mesh_data is not None and getattr(export_mesh_data, "users", 0) == 0:
                try:
                    bpy.data.meshes.remove(export_mesh_data, do_unlink=True)
                except Exception:
                    pass

        _restore_reserved_object_names(renamed_conflicts)

        if show_export_identifier:
            try:
                temp_state.gn_modifier[show_export_identifier] = bool(previous_show_export_value)
            except Exception:
                pass
        if uv_name_identifier and previous_uv_name_value is not None:
            try:
                temp_state.gn_modifier[uv_name_identifier] = str(previous_uv_name_value)
            except Exception:
                pass
        if skip_grid_identifier:
            try:
                temp_state.gn_modifier[skip_grid_identifier] = bool(previous_skip_grid_value)
            except Exception:
                pass

        if context.window:
            context.window.scene = scene
        try:
            _restore_scene_object_selection(scene, view_layer, previous_selection, previous_active)
        except Exception:
            pass
        scene.frame_set(previous_frame)

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene

    return mesh_path


def _create_evaluated_export_mesh_snapshot(context, scene, source_object):
    previous_window_scene = context.window.scene if context.window else None
    probe_object = None
    probe_group = None

    try:
        if context.window:
            context.window.scene = scene

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = source_object.evaluated_get(depsgraph)
        eval_mesh = bpy.data.meshes.new_from_object(
            eval_obj,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if eval_mesh is None or len(eval_mesh.vertices) == 0:
            if eval_mesh is not None:
                bpy.data.meshes.remove(eval_mesh, do_unlink=True)
            eval_mesh = None

            probe_object = source_object.copy()
            if source_object.data is not None:
                probe_object.data = source_object.data.copy()
            probe_object.name = _unique_name("VAT_ExportProbe", bpy.data.objects.keys())
            scene.collection.objects.link(probe_object)

            probe_group = _create_realize_instances_probe_group()
            probe_modifier = probe_object.modifiers.new("UVB_ExportRealizeProbe", "NODES")
            probe_modifier.node_group = probe_group

            depsgraph = context.evaluated_depsgraph_get()
            eval_probe_obj = probe_object.evaluated_get(depsgraph)
            eval_mesh = bpy.data.meshes.new_from_object(
                eval_probe_obj,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            if eval_mesh is None or len(eval_mesh.vertices) == 0:
                if eval_mesh is not None:
                    bpy.data.meshes.remove(eval_mesh, do_unlink=True)
                raise ExportWorkflowError(
                    "Evaluated geometry from VAT_HelperPlane did not contain a mesh even with ShowExportMesh enabled."
                )

        export_object = bpy.data.objects.new(
            _unique_name("VAT_ExportMesh", bpy.data.objects.keys()),
            eval_mesh,
        )
        export_object.matrix_world = eval_obj.matrix_world.copy()
        scene.collection.objects.link(export_object)
        return export_object
    finally:
        if probe_object is not None:
            probe_mesh_data = probe_object.data
            try:
                bpy.data.objects.remove(probe_object, do_unlink=True)
            except Exception:
                pass
            if probe_mesh_data is not None and getattr(probe_mesh_data, "users", 0) == 0:
                try:
                    bpy.data.meshes.remove(probe_mesh_data, do_unlink=True)
                except Exception:
                    pass

        if probe_group is not None and getattr(probe_group, "users", 0) == 0:
            try:
                bpy.data.node_groups.remove(probe_group, do_unlink=True)
            except Exception:
                pass

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _find_nodes_modifier(obj, preferred_name=None):
    if obj is None:
        return None

    if preferred_name:
        modifier = obj.modifiers.get(preferred_name)
        if modifier is not None and modifier.type == "NODES":
            return modifier

    for modifier in obj.modifiers:
        if modifier.type == "NODES":
            return modifier
    return None


def _ensure_export_mesh_uv_layer(obj, preferred_name, fallback_index=0):
    if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        raise ExportWorkflowError("A mesh object is required when preparing export UV layers.")

    uv_layers = getattr(obj.data, "uv_layers", None)
    if uv_layers is None:
        raise ExportWorkflowError(f"Object '{obj.name}' does not support UV layers.")

    desired_name = str(preferred_name or "").strip() or _default_uv_layer_name(max(0, int(fallback_index)))
    existing_layer = uv_layers.get(desired_name)
    if existing_layer is not None:
        return str(existing_layer.name)

    if len(uv_layers) == 0:
        created_layer = uv_layers.new(name=desired_name)
        if created_layer is None:
            raise ExportWorkflowError(f"Failed to create export UV layer '{desired_name}' on object '{obj.name}'.")
        return str(created_layer.name)

    source_index = max(0, min(int(fallback_index), len(uv_layers) - 1))
    source_layer = uv_layers[source_index]
    created_layer = uv_layers.new(name=desired_name)
    if created_layer is None:
        raise ExportWorkflowError(f"Failed to create export UV layer '{desired_name}' on object '{obj.name}'.")

    try:
        source_data = source_layer.data
        target_data = created_layer.data
        for index in range(min(len(source_data), len(target_data))):
            target_data[index].uv = source_data[index].uv
    except Exception:
        pass

    return str(created_layer.name)


def _select_only_object_in_scene(context, scene, view_layer, target_object):
    if context.window:
        context.window.scene = scene

    for obj in scene.objects:
        obj.select_set(False)

    target_object.select_set(True)
    view_layer.objects.active = target_object


def _restore_scene_object_selection(scene, view_layer, previous_selection, previous_active):
    for obj in scene.objects:
        obj.select_set(False)

    for obj in previous_selection:
        restored = scene.objects.get(obj.name)
        if restored is None:
            continue
        restored.select_set(True)

    if previous_active:
        restored_active = scene.objects.get(previous_active.name)
        if restored_active is not None:
            view_layer.objects.active = restored_active


def _reserve_exact_object_name(target_name, exclude_object=None):
    renamed_conflicts = []
    for obj in bpy.data.objects:
        if exclude_object is not None and obj == exclude_object:
            continue
        if obj.name != target_name:
            continue
        temporary_name = _unique_name(f"{target_name}_Temp", bpy.data.objects.keys())
        obj.name = temporary_name
        renamed_conflicts.append((obj, target_name))
    return renamed_conflicts


def _restore_reserved_object_names(renamed_conflicts):
    for obj, original_name in reversed(renamed_conflicts):
        try:
            obj.name = original_name
        except Exception:
            pass


def _create_or_overwrite_named_scene(scene_name):
    existing = bpy.data.scenes.get(scene_name)
    if existing is None:
        return bpy.data.scenes.new(scene_name)

    replacement_name = _unique_name(f"{scene_name}_Replacement", bpy.data.scenes.keys())
    replacement = bpy.data.scenes.new(replacement_name)

    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager:
        for win in window_manager.windows:
            if win.scene == existing:
                win.scene = replacement

    bpy.data.scenes.remove(existing, do_unlink=True)
    replacement.name = scene_name
    return replacement


def _iter_collection_descendants(collection):
    descendants = []
    if collection is None:
        return descendants
    for child in collection.children:
        descendants.append(child)
        descendants.extend(_iter_collection_descendants(child))
    return descendants


def _dedupe_ids(items):
    unique = []
    seen = set()
    for item in items:
        if item is None:
            continue
        try:
            key = item.as_pointer()
        except Exception:
            key = id(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _remove_id_fallback(data_block):
    if data_block is None:
        return False

    managers = (
        ("scenes", bpy.types.Scene),
        ("objects", bpy.types.Object),
        ("collections", bpy.types.Collection),
        ("meshes", bpy.types.Mesh),
        ("cameras", bpy.types.Camera),
        ("node_groups", bpy.types.NodeTree),
        ("worlds", bpy.types.World),
        ("materials", bpy.types.Material),
        ("images", bpy.types.Image),
    )
    for manager_name, manager_type in managers:
        try:
            if not isinstance(data_block, manager_type):
                continue
        except Exception:
            continue
        manager = getattr(bpy.data, manager_name, None)
        if manager is None:
            continue
        try:
            manager.remove(data_block, do_unlink=True)
            return True
        except Exception:
            return False
    return False


def _remove_unused_data_blocks(data_blocks):
    removable = []
    for data_block in _dedupe_ids(data_blocks):
        try:
            users = int(getattr(data_block, "users", 0))
        except Exception:
            users = 0
        if users == 0:
            removable.append(data_block)

    if not removable:
        return 0

    if hasattr(bpy.data, "batch_remove"):
        try:
            bpy.data.batch_remove(removable)
            return len(removable)
        except Exception:
            pass

    removed = 0
    for data_block in removable:
        if _remove_id_fallback(data_block):
            removed += 1
    return removed


def _cleanup_temporary_scene(temp_state):
    temp_scene = getattr(temp_state, "scene", None)
    if temp_scene is None:
        return "Temporary compute scene cleanup skipped: no scene was created."

    if bpy.data.scenes.get(temp_scene.name) is None:
        return f"Temporary compute scene '{temp_scene.name}' was already removed."

    window_manager = getattr(bpy.context, "window_manager", None)
    replacement_scene = None
    for scene in bpy.data.scenes:
        if scene != temp_scene:
            replacement_scene = scene
            break

    if window_manager is not None and replacement_scene is not None:
        for window in window_manager.windows:
            try:
                if window.scene == temp_scene:
                    window.scene = replacement_scene
            except Exception:
                pass

    temp_objects = list(temp_scene.objects)
    temp_object_data = [getattr(obj, "data", None) for obj in temp_objects if getattr(obj, "data", None) is not None]
    temp_child_collections = _iter_collection_descendants(temp_scene.collection)
    cleanup_candidates = [
        temp_scene.collection,
        getattr(temp_scene, "world", None),
        getattr(getattr(temp_state, "gn_modifier", None), "node_group", None),
        *temp_object_data,
        *temp_child_collections,
    ]

    removed_objects = 0
    for obj in temp_objects:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed_objects += 1
        except Exception:
            pass

    scene_removed = False
    try:
        bpy.data.scenes.remove(temp_scene, do_unlink=True)
        scene_removed = True
    except Exception:
        scene_removed = False

    removed_data_blocks = _remove_unused_data_blocks(cleanup_candidates)
    scene_name = getattr(temp_scene, "name", "VAT_Compute_Scene")
    if scene_removed:
        return (
            f"Removed temporary compute scene '{scene_name}' "
            f"({removed_objects} objects, {removed_data_blocks} related data-blocks)."
        )
    return (
        f"Removed {removed_objects} temporary objects and {removed_data_blocks} related data-blocks, "
        f"but scene '{scene_name}' could not be removed."
    )


def _build_texture_prefix(settings, asset_name):
    pattern = (settings.texture_naming_pattern or "").strip()
    if pattern:
        try:
            return pattern.format(
                prefix=settings.texture_prefix,
                asset_name=asset_name,
                attribute="",
            ).rstrip("_")
        except Exception:
            pass
    return f"{settings.texture_prefix}{asset_name}"


def _unique_name(base_name, existing_names):
    if base_name not in existing_names:
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}.{index:03d}"
        if candidate not in existing_names:
            return candidate
        index += 1
