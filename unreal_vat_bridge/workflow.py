from __future__ import annotations

import glob
import json
import os
import uuid
from dataclasses import dataclass

import bpy

from . import constants, preview

UVB_RUNTIME_NODE_GROUP_PREFIX = "UVB_Runtime_"
UVB_WORKFLOW_BUILD_ID = "uvb-workflow-2026-04-01-real-render-v6"

class ExportWorkflowError(RuntimeError):
    pass


@dataclass
class PreparedExport:
    output_directory: str
    source_scene: bpy.types.Scene
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
    preview_estimate: preview.ExportPreview
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
    reference_object: bpy.types.Object


@dataclass
class ExportResult:
    position_texture: str
    normal_texture: str
    json_path: str | None
    mesh_path: str | None


def run_export(context, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    temp_state = None

    if hasattr(settings, "last_exported_json_text"):
        settings.last_exported_json_text = ""

    try:
        report({"INFO"}, f"Workflow build: {UVB_WORKFLOW_BUILD_ID} ({__file__})")
        report({"INFO"}, "Clearing previous runtime node-group cache...")
        _purge_runtime_node_groups(report)

        report({"INFO"}, "Validating export settings...")
        prepared = _validate_and_prepare(context, settings, report)

        report({"INFO"}, "Creating temporary compute scene...")
        temp_state = _create_temporary_scene(context, prepared, settings, report)

        report({"INFO"}, "Applying UI values to Geometry Nodes transfer layer...")
        _apply_ui_values_to_gn_transfer_layer(temp_state, prepared, settings, report)

        report({"INFO"}, "Running Geometry Nodes animation bake...")
        _bake_geometry_nodes_animation_node(context, temp_state, settings, report)

        report({"INFO"}, "Moving playhead to End Frame and collecting end-frame export settings...")
        _apply_end_frame_export_settings(
            context,
            temp_state,
            prepared,
            int(settings.end_frame),
            report,
        )

        report({"INFO"}, "Exporting rendered Position/Normal textures...")
        position_texture, normal_texture = _export_rendered_textures(context, prepared, temp_state, settings, report)

        json_path = None
        if settings.export_json_metadata:
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
            report({"INFO"}, "Exporting FBX mesh...")
            mesh_path = _export_mesh_fbx(context, prepared)

        result = ExportResult(
            position_texture=position_texture,
            normal_texture=normal_texture,
            json_path=json_path,
            mesh_path=mesh_path,
        )
        report({"INFO"}, "Export workflow completed.")
        return result
    except ExportWorkflowError:
        raise
    except Exception as exc:
        raise ExportWorkflowError(f"Export failed: {exc}") from exc
    finally:
        if temp_state:
            report({"INFO"}, "Temporary compute scene retained for debugging.")


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

    node_group_name = constants.GN_GROUP_BY_MODE[settings.export_mode]
    node_group = _load_fresh_node_group_from_library(node_group_name, report)

    source_fps = preview.get_source_fps(context.scene)
    frame_drop_step = preview.get_frame_drop_step(settings)
    preview_estimate = preview.build_preview(context, settings)

    return PreparedExport(
        output_directory=output_directory,
        source_scene=context.scene,
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
        preview_estimate=preview_estimate,
        export_texture_width=int(preview_estimate.texture_width),
        export_texture_height=int(preview_estimate.texture_height),
    )


def _resolve_source_selection(scene, settings):
    if settings.export_mode == constants.EXPORT_MODE_VERTEX:
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

    if settings.rigid_source_type == constants.RIGID_SOURCE_TYPE_SINGLE:
        obj = settings.rigid_source_object
        if obj is None:
            raise ExportWorkflowError("Source Object is required when Source Type is Single Object.")
        if obj.name not in scene.objects:
            raise ExportWorkflowError("Rigid Source Object must exist in the active scene.")
        return [obj], "OBJECT", obj.name, False, obj, None

    collection = settings.rigid_source_collection
    if collection is None:
        raise ExportWorkflowError("Source Collection is required when Source Type is Collection.")
    source_objects = [obj for obj in collection.objects if obj.name in scene.objects]
    if not source_objects:
        raise ExportWorkflowError("Source Collection has no objects in the active scene.")
    return source_objects, "COLLECTION", collection.name, True, None, collection


def _create_temporary_scene(context, prepared, settings, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)

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

    report({"INFO"}, "Creating static reference-frame mesh object...")
    reference_object = _create_reference_frame_object(
        context,
        temp_scene,
        source_copy_objects,
        int(settings.reference_frame),
    )

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

    payload = {
        "mode": settings.export_mode,
        "start_frame": settings.start_frame,
        "end_frame": settings.end_frame,
        "reference_frame": settings.reference_frame,
        "use_collection": prepared.use_collection,
        "object": temp_state.source_object_for_gn.name if temp_state.source_object_for_gn else "",
        "collection": temp_state.source_collection_for_gn.name if temp_state.source_collection_for_gn else "",
        "reference_object": temp_state.reference_object.name if temp_state.reference_object else "",
        "texture_width": prepared.export_texture_width,
        "texture_height": prepared.export_texture_height,
        "frame_drop_step": prepared.frame_drop_step,
        "source_fps": prepared.source_fps,
        "source_kind": prepared.source_kind,
        "asset_name": prepared.asset_name,
        "skip_grid_transfer": False,
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
    set_required("Use Collection", ("UseCollection", "Use Collection"), bool(prepared.use_collection))

    set_required(
        "Frame Drop Step",
        ("FrameDropStep", "Frame Drop Step", "FPSDropping", "FPS Dropping"),
        int(prepared.frame_drop_step),
    )
    set_required(
        "Ref Frame Object",
        ("RefFrameOBJ", "Ref Frame OBJ", "ReferenceObject", "Reference Object", "RefObject", "Ref Object"),
        temp_state.reference_object,
    )

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

    if required_missing:
        missing_text = ", ".join(required_missing)
        raise ExportWorkflowError(
            "ComputeVertexVAT input sockets are missing: "
            f"{missing_text}. Please match the fixed socket names."
        )


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


def _apply_end_frame_export_settings(context, temp_state, prepared, end_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    end_frame = int(end_frame)

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
                "min",
                "max",
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


def _ensure_end_frame_min_max_attributes(context, temp_state, prepared, evaluate_frame, reporter=None):
    report = reporter if reporter else (lambda _level, _message: None)
    eval_frame = int(evaluate_frame)

    attrs = getattr(prepared, "end_frame_attributes", {}) or {}
    min_value = attrs.get("min")
    max_value = attrs.get("max")
    if min_value is not None and max_value is not None:
        return

    report({"INFO"}, "min/max not found with SkipGridTransfer=True. Retrying with SkipGridTransfer=False...")
    values_false = {}

    try:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, False, report)
        temp_state.scene.frame_set(eval_frame)
        values_false = _read_named_attributes_from_evaluated_geometry(
            context,
            temp_state.scene,
            temp_state.helper_object,
            eval_frame,
            ("min", "max"),
        )
    finally:
        _set_skip_grid_transfer(prepared.node_group, temp_state.gn_modifier, True, report)
        temp_state.scene.frame_set(eval_frame)

    if "min" in values_false and attrs.get("min") is None:
        attrs["min"] = values_false["min"]
    if "max" in values_false and attrs.get("max") is None:
        attrs["max"] = values_false["max"]

    prepared.end_frame_attributes = attrs

    min_value = attrs.get("min")
    max_value = attrs.get("max")
    if min_value is not None and max_value is not None:
        report({"INFO"}, "Collected min/max from Geometry Nodes End Frame attributes.")
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
        "Geometry Nodes attributes 'min' and 'max' were not found in End Frame readback. "
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

def _read_required_gn_min_max_attributes(context, scene, helper_object, evaluate_frame):
    values = _read_named_attributes_from_evaluated_geometry(
        context,
        scene,
        helper_object,
        int(evaluate_frame),
        ("min", "max"),
    )

    if "min" not in values or "max" not in values:
        raise ExportWorkflowError(
            "Geometry Nodes attributes 'min' and 'max' are required on the evaluated helper mesh."
        )

    return values["min"], values["max"]


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


def _extract_texture_size_components(sample):
    if hasattr(sample, "vector"):
        vec = sample.vector
        if len(vec) >= 2:
            return vec[0], vec[1]

    if hasattr(sample, "color"):
        col = sample.color
        if len(col) >= 2:
            return col[0], col[1]

    if hasattr(sample, "value"):
        value = sample.value
        return value, value

    if hasattr(sample, "x") and hasattr(sample, "y"):
        return sample.x, sample.y

    return None, None


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

    prefix = _build_texture_prefix(settings, prepared.asset_name)
    position_filename = f"{prefix}_{constants.POSITION_SUFFIX}.exr"
    normal_filename = f"{prefix}_{constants.NORMAL_SUFFIX}.exr"
    position_path = os.path.join(prepared.output_directory, position_filename)
    normal_path = os.path.join(prepared.output_directory, normal_filename)

    scene = temp_state.scene
    view_layer = scene.view_layers[0]
    eval_frame = int(settings.end_frame)

    previous_window_scene = context.window.scene if context.window else None
    previous_frame = scene.frame_current
    previous_engine = scene.render.engine
    previous_compositor_flags = _enable_scene_compositor_flags(scene)
    previous_pass_normal = getattr(view_layer, "use_pass_normal", None)
    previous_pass_position = getattr(view_layer, "use_pass_position", None)
    previous_compositing_node_group = getattr(scene, "compositing_node_group", None) if hasattr(scene, "compositing_node_group") else None

    node_tree = None
    created_nodes = []
    created_compositor_tree = False

    try:
        if context.window:
            context.window.scene = scene

        scene.frame_set(eval_frame)
        scene.render.engine = "CYCLES"

        if not hasattr(view_layer, "use_pass_normal"):
            raise ExportWorkflowError("View Layer Normal pass is not available in this Blender build.")
        if not hasattr(view_layer, "use_pass_position"):
            raise ExportWorkflowError("View Layer Position pass is not available in this Blender build.")

        view_layer.use_pass_normal = True
        view_layer.use_pass_position = True

        node_tree, created_compositor_tree = _ensure_scene_compositor_tree(scene)
        if node_tree is None:
            caps = _describe_scene_compositor_capabilities(scene)
            raise ExportWorkflowError(
                "Compositor node tree is unavailable for rendered EXR export. "
                f"Scene compositor-related attrs: {caps}"
            )

        nodes = node_tree.nodes
        links = node_tree.links

        render_layers = nodes.new("CompositorNodeRLayers")
        created_nodes.append(render_layers)

        position_output = nodes.new("CompositorNodeOutputFile")
        created_nodes.append(position_output)

        normal_output = nodes.new("CompositorNodeOutputFile")
        created_nodes.append(normal_output)

        token = f"UVB_TMP_{uuid.uuid4().hex}"
        position_prefix = f"{token}_Position_"
        normal_prefix = f"{token}_Normal_"

        _configure_file_output_node_for_exr(position_output, prepared.output_directory, position_prefix)
        _configure_file_output_node_for_exr(normal_output, prepared.output_directory, normal_prefix)

        _remove_file_output_candidates(prepared.output_directory, position_prefix)
        _remove_file_output_candidates(prepared.output_directory, normal_prefix)

        position_socket = _find_compositor_output_socket(render_layers, ("Position",))
        normal_socket = _find_compositor_output_socket(render_layers, ("Normal",))

        if position_socket is None or normal_socket is None:
            available = [sock.name for sock in render_layers.outputs]
            available_text = ", ".join(available) if available else "(none)"
            raise ExportWorkflowError(
                "Required compositor passes were not found. "
                f"Need Position and Normal. Available Render Layers outputs: {available_text}"
            )

        links.new(position_socket, position_output.inputs[0])
        links.new(normal_socket, normal_output.inputs[0])

        with context.temp_override(scene=scene, view_layer=view_layer):
            result = bpy.ops.render.render(write_still=False, use_viewport=False)

        if "FINISHED" not in result:
            raise ExportWorkflowError("Render operation did not finish successfully for EXR export.")

        generated_position = _find_latest_file_output(prepared.output_directory, position_prefix)
        generated_normal = _find_latest_file_output(prepared.output_directory, normal_prefix)

        if generated_position is None or generated_normal is None:
            raise ExportWorkflowError(
                "Rendered EXR files were not generated by compositor file outputs. "
                f"Position prefix: {position_prefix}, Normal prefix: {normal_prefix}"
            )

        if os.path.exists(position_path):
            os.remove(position_path)
        if os.path.exists(normal_path):
            os.remove(normal_path)

        os.replace(generated_position, position_path)
        os.replace(generated_normal, normal_path)

        report({"INFO"}, f"Rendered Position EXR: {position_path}")
        report({"INFO"}, f"Rendered Normal EXR: {normal_path}")
        return position_path, normal_path

    except RuntimeError as exc:
        raise ExportWorkflowError(f"Rendered EXR export failed: {exc}") from exc
    finally:
        if node_tree is not None:
            for node in created_nodes:
                try:
                    if node.name in node_tree.nodes:
                        node_tree.nodes.remove(node)
                except Exception:
                    pass

        if previous_pass_normal is not None and hasattr(view_layer, "use_pass_normal"):
            view_layer.use_pass_normal = previous_pass_normal
        if previous_pass_position is not None and hasattr(view_layer, "use_pass_position"):
            view_layer.use_pass_position = previous_pass_position

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

        scene.frame_set(previous_frame)

        if context.window and previous_window_scene:
            context.window.scene = previous_window_scene


def _configure_file_output_node_for_exr(node, base_path, slot_path_prefix):
    node.base_path = base_path
    node.format.file_format = "OPEN_EXR"
    node.format.color_mode = "RGB"
    node.format.color_depth = "16"
    node.format.exr_codec = "ZIP"

    if len(node.file_slots) == 0:
        node.file_slots.new("Image")

    node.file_slots[0].path = slot_path_prefix


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

    total_frame_count = _compute_total_sampled_frame_count(
        int(settings.start_frame),
        int(settings.end_frame),
        int(prepared.frame_drop_step),
    )
    _ensure_end_frame_min_max_attributes(
        context,
        temp_state,
        prepared,
        int(settings.end_frame),
        None,
    )

    end_frame_attrs = getattr(prepared, "end_frame_attributes", {}) or {}
    min_value = end_frame_attrs.get("min")
    max_value = end_frame_attrs.get("max")

    if min_value is None or max_value is None:
        raise ExportWorkflowError(
            "Geometry Nodes attributes 'min' and 'max' were not found in End Frame readback even after JSON-stage fallback."
        )

    payload = {
        "total_frame_count": total_frame_count,
        "blender_fps": float(prepared.source_fps),
        "frame_drop_step": int(prepared.frame_drop_step),
        "exported_texture_names": [
            os.path.basename(position_texture),
            os.path.basename(normal_texture),
        ],
        "exported_object_names": [obj.name for obj in prepared.source_objects],
        "texture_resolution": {
            "width": int(prepared.export_texture_width),
            "height": int(prepared.export_texture_height),
        },
        "geometry_nodes_min": min_value,
        "geometry_nodes_max": max_value,
    }

    json_text = json.dumps(payload, indent=2)
    with open(json_path, "w", encoding="utf-8") as handle:
        handle.write(json_text)

    return json_path, json_text


def _compute_total_sampled_frame_count(start_frame, end_frame, frame_drop_step):
    step = max(1, int(frame_drop_step))
    frame_span = max(1, int(end_frame) - int(start_frame) + 1)
    return int((frame_span + step - 1) // step)


def _export_mesh_fbx(context, prepared):
    filename = f"{prepared.asset_name}.fbx"
    mesh_path = os.path.join(prepared.output_directory, filename)
    view_layer = context.view_layer

    previous_selection = [obj for obj in view_layer.objects if obj.select_get()]
    previous_active = view_layer.objects.active

    export_objects = [obj for obj in prepared.source_objects if obj.name in prepared.source_scene.objects]
    if not export_objects:
        raise ExportWorkflowError("No exportable source object was found for FBX export.")

    try:
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass

        bpy.ops.object.select_all(action="DESELECT")
        for obj in export_objects:
            obj.select_set(True)
        view_layer.objects.active = export_objects[0]

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
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous_selection:
            if obj.name in prepared.source_scene.objects:
                obj.select_set(True)
        if previous_active and previous_active.name in prepared.source_scene.objects:
            view_layer.objects.active = previous_active

    return mesh_path


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








































