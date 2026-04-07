# Unreal VAT Bridge Project Spec

## Purpose

This repository contains a Blender 5.1 add-on for exporting Vertex Animation Texture data for Unreal Engine.

The add-on is designed as a UI-first workflow:

- choose a source object or collection
- bake/evaluate Geometry Nodes in a temporary compute scene
- export VAT textures
- optionally export JSON metadata
- optionally export an FBX mesh derived from the helper object

This file is the handoff document for future contributors, including other AI agents.

When behavior changes, this file should be updated in the same change set.

## Source Of Truth

- Add-on package: [unreal_vat_bridge](unreal_vat_bridge)
- Main workflow implementation: [workflow.py](unreal_vat_bridge/workflow.py)
- Blender Geometry Nodes asset: [vat_nodes.blend](unreal_vat_bridge/assets/vat_nodes.blend)
- Packaging script: [package.sh](package.sh)

## Current Target Environment

- Blender: `5.1.x`
- Add-on entry point: [__init__.py](unreal_vat_bridge/__init__.py)
- Current workflow build id is defined in [workflow.py](unreal_vat_bridge/workflow.py)

## Repository Layout

- [README.md](README.md): very short project readme
- [PROJECT_SPEC.md](PROJECT_SPEC.md): this document
- [package.sh](package.sh): creates installable zip
- [unreal_vat_bridge/__init__.py](unreal_vat_bridge/__init__.py): Blender add-on registration and `bl_info`
- [unreal_vat_bridge/constants.py](unreal_vat_bridge/constants.py): names, modes, temp object identifiers, suffixes
- [unreal_vat_bridge/properties.py](unreal_vat_bridge/properties.py): scene property group
- [unreal_vat_bridge/ui.py](unreal_vat_bridge/ui.py): sidebar UI
- [unreal_vat_bridge/operators.py](unreal_vat_bridge/operators.py): export operator and JSON copy operator
- [unreal_vat_bridge/preview.py](unreal_vat_bridge/preview.py): preview math for texture sizing
- [unreal_vat_bridge/workflow.py](unreal_vat_bridge/workflow.py): end-to-end export system
- [unreal_vat_bridge/assets/vat_nodes.blend](unreal_vat_bridge/assets/vat_nodes.blend): Geometry Nodes groups used at runtime

## User-Facing UI

The add-on panel lives in:

- `View3D > Sidebar > Unreal VAT Bridge`

The UI implementation is part of the project spec and should be kept in sync with code changes.

The property definitions live in [properties.py](unreal_vat_bridge/properties.py) and the drawing logic lives in [ui.py](unreal_vat_bridge/ui.py).

### Panel Structure

Current panels:

- main panel: `UVB_PT_main`
- child panel: `UVB_PT_advanced`
- child panel: `UVB_PT_debug`

### Main Panel Layout

The main panel currently renders these sections in order:

1. small top version label showing `v12`
2. `Mode`
3. `Source`
4. `Animation`
5. `Output`
6. `Export Settings` (both modes — inline box section, always visible and always expanded)
7. `Export`

Below the main sections, the panel exposes collapsible child panels:

- `Advanced`
- `Debug`

The UI is intentionally kept simple:

- the main panel shows only the minimum export controls
- supporting or diagnostic controls live in collapsed child panels
- explanations should prefer Blender hover tooltips via property `description`

### Mode Section

Current field:

- `Export Mode`

UI labels currently shown in the mode dropdown:

- `Vertex`
- `RBD`

Note:

- the internal enum value for `RBD` mode is now `RBD`
- Geometry Nodes group naming is now based on `ComputeRBDtoVAT`

### Source Section

This section is mode-dependent.

In `VERTEX` mode:

- `Use Source Collection`
- if `Use Source Collection = True`: `Source Collection`
- otherwise: `Source Object`

In `RBD` mode:

- `Source Collection`

### Animation Section

Fields common to both modes:

- `Start Frame`
- `End Frame`
- `Reference Frame`
- `Source FPS` as read-only label
- `Frame Step`

Fields common to both modes (Animation section):

- `Start Frame`
- `End Frame`
- `Reference Frame`
- `Source FPS` as read-only label
- `Frame Step`

Compatibility notes:

- if an older Blender session still holds a stale property group and `uv_map_index` is missing, the UI currently shows `UV Index: 0 (reload addon)` instead of failing
- if `frame_drop_step` is missing, the UI shows `Frame Step: unavailable (reload addon)`
- if `rbd_packing_method` is missing, the UI shows `Packing Method: unavailable (reload addon)` and falls back to `UV_PACKING`
- if the `Export Mode` enum is stale, the UI shows a warning asking for addon disable/enable

### Output Section

Current fields:

- `Output Folder`
- `Export JSON`
- `Export FBX`

### Export Section

Current action:

- `Export` button bound to `unreal_vat_bridge.export`
- `Copy JSON` button next to the export button
- one-line export summary above the button

Current summary text:

- Vertex mode: `Exports Position + Normal, plus optional JSON and FBX.`
- RBD mode: `Exports Position only, plus optional JSON and FBX.`

Current button behavior:

- `Copy JSON` copies the last exported metadata JSON from memory
- `Copy JSON` is disabled until a JSON export has been produced in the current session

Current runtime behavior:

- while export is running, the Blender footer/status bar shows stage-based progress with a percentage via `window_manager.progress_*`
- the footer text is updated with the current phase, such as validation, temp scene creation, bake, texture export, JSON export, or FBX export

### UI Version Label

- the small version text in the main panel is sourced from `constants.UI_VERSION_LABEL`
- when the visible UI version changes, `constants.UI_VERSION_LABEL` must be updated so the panel stays in sync

### Export Settings Section

Inline box section drawn directly in `UVB_PT_main`. Always visible for both modes and always expanded (not a child panel). Placed between `Output` and `Export`.

In `VERTEX` mode:

- `UV Index`

In `RBD` mode:

- `Skip Scale`
- `Packing Method` — expanded (radio button / segment control) style, three options:
  - `UV Packing` (internal value `UV_PACKING`, GN index `0`)
  - `Vertex Color Packing` (internal value `VERTEX_COLOR_PACKING`, GN index `1`)
  - `Object Index Packing` (internal value `OBJECT_INDEX_PACKING`, GN index `2`)
- `Objects in collection` — read-only label showing live object count from `rbd_source_collection`
- When `Vertex Color Packing` is selected: note "Vertex Color Packing: max 1020 objects". Turns red with ERROR icon if count exceeds 1020.
- `UV Index` — shown only when `Packing Method` is `UV Packing`

Note: The GN `Packing Method` socket is an Int type (not a Menu type). Values 0 / 1 / 2 are sent directly as integers.

### Advanced Panel

The `Advanced` child panel currently exposes:

- `Texture Prefix`
- `Texture Naming Pattern`

The fixed EXR format rows are intentionally hidden from the UI for now even though the runtime export format remains fixed by implementation.

### Debug Panel

This child panel is collapsed by default.

Current field:

- `Keep Temporary Scene`

Behavior:

- this panel is nested under the main add-on panel
- it uses the normal Blender property checkbox UI
- when enabled, the temp export scene is retained for debugging
- when disabled, the add-on removes the temp scene at the end and also removes the related orphaned data-blocks created for that export when possible
- cleanup now uses conservative per-ID removal (instead of aggressive `batch_remove`) to avoid Blender instability/crashes during teardown
- the panel includes a short one-line explanation of the on/off behavior

### Tooltip Policy

Primary control explanations should live in property hover tooltips, not as persistent UI text.

Important fields that should keep short tooltip text:

- `Export Mode`
- `Source Object`
- `Source Collection`
- `Reference Frame`
- `Frame Step`
- `UV Index`
- `Skip Scale`
- `Export JSON`
- `Export FBX`
- `Keep Temporary Scene`

### Current UI Property Set

Important settings currently exposed somewhere in the UI:

- `Export Mode`
- source object or source collection
- `Start Frame`
- `End Frame`
- `Reference Frame`
- `Frame Step`
- `Packing Method` (RBD mode only)
- `UV Index` (always in Vertex mode; in RBD mode only when Packing Method is UV Packing)
- `Output Folder`
- `Export JSON`
- `Export FBX`
- `Texture Prefix`
- `Texture Naming Pattern`
- `Keep Temporary Scene`

## High-Level Runtime Flow

The main entry point is `UVB_OT_export.execute()` in [operators.py](unreal_vat_bridge/operators.py), which reloads [workflow.py](unreal_vat_bridge/workflow.py) and calls `run_export(...)`.

`run_export(...)` currently performs:

1. clear old runtime-appended Geometry Nodes groups
2. validate UI settings and prepare export metadata
3. create a temporary compute scene
4. duplicate source objects into the temp scene
5. generate a static reference-frame object
6. create a helper plane with a Geometry Nodes modifier
7. push UI values into the Geometry Nodes modifier inputs
8. run the Geometry Nodes bake operator
9. move to the end frame and read export-critical attributes
10. export VAT textures
11. optionally export JSON metadata
12. optionally export FBX mesh

Key functions:

- `run_export`: [workflow.py](unreal_vat_bridge/workflow.py#L396)
- `_validate_and_prepare`: [workflow.py](unreal_vat_bridge/workflow.py#L495)
- `_create_temporary_scene`: [workflow.py](unreal_vat_bridge/workflow.py#L550)
- `_apply_ui_values_to_gn_transfer_layer`: [workflow.py](unreal_vat_bridge/workflow.py#L1120)
- `_bake_geometry_nodes_animation_node`: [workflow.py](unreal_vat_bridge/workflow.py#L1405)
- `_apply_end_frame_export_settings`: [workflow.py](unreal_vat_bridge/workflow.py#L1570)
- `_export_rendered_textures`: [workflow.py](unreal_vat_bridge/workflow.py#L2571)
- `_export_json_metadata`: [workflow.py](unreal_vat_bridge/workflow.py#L4206)
- `_export_mesh_fbx`: [workflow.py](unreal_vat_bridge/workflow.py#L4271)

## Temporary Scene System

The add-on builds a dedicated compute scene for export.

Important temporary objects:

- temp scene name: `VAT_Compute_Scene`
- camera: `VAT_ComputeCamera`
- helper plane: `VAT_HelperPlane`
- Geometry Nodes modifier: `VAT_GeometryNodes`
- reference object name pattern: `VAT_RefFrameOBJ`
- RBD reference collection name pattern: `VAT_RefFrameCollection`

Whether the temp scene is retained or deleted is controlled by the `Keep Temporary Scene` checkbox in the `Debug` section.

## Geometry Nodes Contract

The Python side expects specific GN group names from [vat_nodes.blend](unreal_vat_bridge/assets/vat_nodes.blend):

- `ComputeVertexVAT`
- `ComputeRBDtoVAT`

Current important input sockets on `ComputeVertexVAT`:

- `WhatToShow`
- `UseCollection`
- `ShowExportMesh`
- `SkipGridTransfer`
- `Start Frame`
- `End Frame`
- `Reference Frame`
- `FrameDropStep`
- `UVName`
- `RefFrameOBJ`
- `Collection`
- `Object`

Current important input sockets on `ComputeRBDtoVAT`:

- `ShowExportMesh`
- `ShowAlph`
- `SkipGridTransfer`
- `SkipScale`
- `PackingMethod` — menu socket (integer index); Python resolves it via `_find_packing_method_identifier` with alias names `("PackingMethod", "Packing Method", "PackMethod", "Pack Method")`
- `Start Frame`
- `End Frame`
- `Reference Frame`
- `FrameDropStep`
- `UVName`
- `RefFrameCollection`
- `Collection`

Python side currently resolves socket identifiers by normalized names instead of hardcoding exact identifiers, so small naming variants are tolerated as long as the normalized label remains compatible.

Relevant helpers:

- `_resolve_input_socket_identifiers`
- `_find_socket_identifier`
- `_set_what_to_show_mode`
- `_set_uv_name_input`
- `_set_show_export_mesh`
- `_set_skip_grid_transfer`
- `_set_packing_method` (RBD only)

## Attribute And Bake Contract

The workflow reads these Geometry Nodes outputs/attributes during export:

- `TextureSize`
- `cameraorthoscale` or `cameraortho`
- `cameralocation` or `cameraposition`
- `DisplayAttribute`
- `PosDefrence`
- `UVMap`

Mode-specific range attributes (returned by `handler.position_range_attr_names()` / `handler.size_range_attr_names()`):

- Vertex: `min`, `max` (no size range)
- RBD: `Pos_Min`, `Pos_Max`, `Size_Min`, `Size_Max`

Common attributes read in both modes:

- `TextureSize`
- `V_RepeatAmount` — optional; written to JSON as `Vat_V_RepeatAmount` if present. First attempted with `SkipGridTransfer=True` in `_apply_end_frame_export_settings`. If not captured there, it is also read during the `_ensure_end_frame_min_max_attributes` SkipGridTransfer=False retry pass. The early-return condition in that function checks whether `V_RepeatAmount` is already populated before skipping the retry, so the retry always fires if the attribute was not captured in the first pass.

The code intentionally toggles `SkipGridTransfer` during different phases:

- `True` during attribute-safe readback where possible
- `False` when GN attribute fallback is needed

This is sensitive and should be changed carefully.

## Texture Export Spec

### Current Behavior

Texture export no longer depends on final render output in the normal path.

The current preferred path is:

1. set `WhatToShow`
2. realize helper output to mesh through a probe GN group
3. read `DisplayAttribute` directly from realized geometry
4. rasterize the realized quads into pixels in Python, preferring the selected UV layer and falling back to helper-plane XY positions only if needed
5. save a float EXR image directly

This direct texel path was introduced to avoid black edge artifacts caused by render/compositor sampling.
In RBD mode, sparse occupancy is allowed in this direct path, so unused texels are left at zero instead of treating the export as failed.
Current debug logging reports realized-mesh counts, available/selected UV layers, attribute domain/type, per-attempt texel coverage, and temporary EXR file state when render fallback is used.

If direct texel export fails, the code still has a render-based fallback path.
The render fallback uses `DisplayAttribute` RGB for emission and `DisplayAttribute` alpha for material transparency so the EXR alpha channel is preserved.
For RBD, `ShowAlph` is toggled `OFF` and `ON` to create two passes, and the second pass is merged into the final EXR alpha channel.
The current RBD render fallback first tries Blender's in-memory `Render Result`, and if that is unavailable in the running Blender build, it falls back to temporary EXR files on disk before alpha composition.
When reading those temporary EXR files back, the add-on waits briefly for the files to become readable before treating the fallback as failed.

### Current Export Outputs

- Vertex mode:
  Position texture suffix: `_Position.exr`
- Vertex mode:
  Normal texture suffix: `_Normal.exr`
- RBD mode:
  Position texture suffix: `_Position.exr`
  Normal texture is not exported as a separate file

RBD mode stores its auxiliary data in the position texture alpha instead of exporting a separate normal texture.

### Current Image Settings

The intended EXR output settings are:

- file format: `OPEN_EXR`
- color: `RGBA`
- color depth: `16-bit float`
- codec: `ZIP`

The direct image save path currently produces standard `R/G/B/A` EXR channels, not Blender multipart-style `Image.R` channel names.

## UV Handling Spec

### UI Setting

`UV Index` is an integer input with:

- default: `0`
- allowed range: `0-3`

### Current Behavior

Before export, the temp duplicated source meshes are normalized so the selected UV slot exists.

Rules:

- if the requested UV layer index does not exist, missing UV layers are created
- the selected layer name becomes the canonical UV name for the export
- all temp duplicated source meshes are aligned to that UV name
- the reference frame mesh is also ensured to carry that UV layer
- Geometry Nodes `UVName` input is filled with the resolved UV layer name

If an old Blender session still holds a stale property group without `uv_map_index`, Python falls back to `0` instead of crashing.

## FBX Export Spec

### Current Intended Behavior

The FBX export should use the helper-plane-based Geometry Nodes output, not the reference object.

Current implementation:

1. go to end frame
2. set helper modifier inputs:
   - `ShowExportMesh = True`
   - `SkipGridTransfer = False`
   - `UVName = selected UV name`
3. Vertex mode:
   duplicate `VAT_HelperPlane`
4. Vertex mode:
   apply all modifiers on the duplicated helper object
5. RBD mode:
   snapshot the evaluated mesh from `VAT_HelperPlane` while `ShowExportMesh = True`
   if the helper still evaluates to instances, add a temporary `Realize Instances` probe before snapshotting
6. ensure the resulting mesh contains the requested export UV layer
7. set the active Color Attribute (index 0, both `active_color_index` and `render_color_index`) so the GN-output Color Attribute is guaranteed to be used — see `_ensure_active_color_attribute`
8. export that helper-derived mesh as FBX with `colors_type="LINEAR"` to preserve raw linear data without sRGB gamma correction

This means the FBX should represent the final Geometry Nodes export mesh, not the original source mesh and not `VAT_RefFrameOBJ`.

### Vertex Color Export

For RBD mode with `VERTEX_COLOR_PACKING`, the GN outputs a Color Attribute on the POINT (vertex) domain. The FBX exporter converts POINT domain to face-corner automatically.

Key requirements:
- The GN socket must output a **Color type** attribute (FLOAT_COLOR or BYTE_COLOR), not FLOAT3/VECTOR — only Color type attributes go into `mesh.color_attributes` and are exported as vertex colors by the FBX exporter.
- `colors_type="LINEAR"` is required because the packed ID data is linear; SRGB gamma correction would corrupt the values.
- `_ensure_active_color_attribute` explicitly sets `active_color_index = 0` and `render_color_index = 0` before export, guaranteeing the GN-created attribute is used even if multiple color attributes exist.

Relevant function:

- `_export_mesh_fbx`: [workflow.py](unreal_vat_bridge/workflow.py#L4271)

## JSON Export Spec

Optional JSON export includes:

- `Vat_NumFrames`
- `VatMode`
  `0` = Vertex, `1` = RBD
- `Vat_UVIndex`
- `Vat_OriginalFPS`
- `Vat_FrameStepping`
- exported texture names
- exported object names
- `Vat_TextureSize`
- `Vat_PositionRangeMin`
- `Vat_PositionRangeMax`
- `Vat_V_RepeatAmount` — float scalar read from GN Point attribute `V_RepeatAmount`. Omitted from JSON if the attribute is not present on the helper geometry.

RBD mode also includes:

- `Vat_SkipScale`
- `Vat_SizeRangeMin`
- `Vat_SizeRangeMax`
- `Vat_PackingMethod` — integer matching the selected packing mode: `0` = UV Packing, `1` = Vertex Color Packing, `2` = Object Index Packing

`Vat_TextureSize` is exported as a single float scalar, not a `{width, height}` object, because the workflow validates square textures before export.

Example:

```json
"Vat_TextureSize": 256.0
```

Vector fields in JSON use Unreal-friendly object form instead of arrays:

```json
"Vat_PositionRangeMin": { "X": -1.0, "Y": -1.0, "Z": -1.0 },
"Vat_PositionRangeMax": { "X": 1.0, "Y": 1.0, "Z": 1.0 }
```

This is intended to map cleanly onto Unreal-side `FVector` parsing without needing array-to-struct conversion first.

## Preview / Sizing Logic

Preview and texture sizing logic are currently simple and live in [preview.py](unreal_vat_bridge/preview.py).

Current logic:

- count exported elements
- compute sampled frame count from frame range and `frame_drop_step`
- estimate width and height as a near-square texture

This is estimation only. Actual final resolution is driven by Geometry Nodes `TextureSize` when available.

## Packaging And Installation

Create the distributable zip by running:

```bash
./package.sh
```

It produces:

- [dist/unreal_vat_bridge.zip](dist/unreal_vat_bridge.zip)

Important packaging constraints:

- zip the `unreal_vat_bridge` folder, not the whole repo
- exclude cache files and temporary macOS files
- keep Python files as normal UTF-8 without BOM

## Recent Implementation Decisions

These are important because they explain why the code looks the way it does now.

### Add-on Detection

The add-on previously failed to appear in Blender because of UTF-8 BOM issues in Python files. This was fixed by stripping BOM.

### Module Reloading

Blender can keep stale Python modules cached across addon reinstalls or reloads.

Current mitigation:

- [__init__.py](unreal_vat_bridge/__init__.py) reloads submodules when the addon is reloaded
- [operators.py](unreal_vat_bridge/operators.py) reloads `constants`, `preview`, and `workflow` before export
- [properties.py](unreal_vat_bridge/properties.py) defensively re-registers the scene property group on addon enable
- mode helpers tolerate legacy `RIGID` values and missing newer constants during transition

This reduces mixed-version errors such as a new `workflow.py` reading an old cached `constants.py`.

### Blender 5.1 Compositor API

There were API differences around compositor file output nodes in Blender 5.1. Compatibility helpers were added for old and new APIs.

### Black Edge Artifact

Render/compositor-based texture generation caused visible black lines. Direct texel export from realized GN geometry is now the preferred path to avoid sampling artifacts.

### `WhatToShow` Contract

Texture export relies on `WhatToShow`:

- `Position`
- `Normal/UVOffset`

Vertex uses both entries.

RBD currently exports only the `Position` pass to disk and may not expose `WhatToShow` at all.

### Helper-Based FBX Export

FBX export now targets the helper object path, because that is the object actually carrying the final Geometry Nodes export mesh.

### `allow_partial` Parameter on Direct Texel Writer

`_write_display_attribute_texture_from_geometry` accepts an explicit `allow_partial=False` parameter. `_export_textures_from_realized_geometry` computes this value once via `_get_mode_handler(settings).allow_partial_coverage()` and passes it through to each call site.

Rule: `allow_partial=True` only for RBD mode, where sparse texel occupancy is expected. Vertex mode must remain `allow_partial=False` so incomplete UV coverage is caught and triggers the render fallback.

### ModeHandler Registry

All per-mode branching logic has been consolidated into a `ModeHandler` class hierarchy in [workflow.py](unreal_vat_bridge/workflow.py). Instances are registered in a `_MODE_REGISTRY` dict keyed by mode string.

```python
_MODE_REGISTRY = {
    EXPORT_MODE_VERTEX: _VertexModeHandler(),
    EXPORT_MODE_RBD:    _RBDModeHandler(),
}
```

`_get_mode_handler(settings)` looks up the active mode and returns the handler. If the mode is unknown, it raises `ExportWorkflowError`.

**Adding a new mode** requires only:
1. Subclass `ModeHandler` in `workflow.py` and override the relevant methods
2. Call `_register_mode(YourModeHandler())` at module level
3. Add the mode to `EXPORT_MODE_ITEMS` in `constants.py`
4. Add mode-specific properties to `properties.py` and UI in `ui.py`

`_is_rbd_mode(settings)` no longer exists. Mode detection is always via `_get_mode_handler`.

### ABC Import Path for Source Objects (`use_abc_import`) — Vertex Mode Only

In **Vertex mode**, `_create_temporary_scene` can duplicate source objects in two ways, selectable via the **Use ABC Import** checkbox in the Mode panel (`settings.use_abc_import`, default `True`):

| Path | How |
|---|---|
| **ABC (default)** | Exports source objects to a temp `.abc` via `bpy.ops.wm.alembic_export`, imports into `VAT_Compute_Scene` via `bpy.ops.wm.alembic_import`. Imported objects get `MESH_SEQUENCE_CACHE` modifiers so GN evaluates them correctly per-frame. |
| **Direct copy** | `source.copy()` + `source.data.copy()` — the legacy path. |

If ABC fails, the code automatically falls back to direct copy and logs a WARNING.

`_cleanup_temporary_scene` collects `CacheFile` data blocks from `MESH_SEQUENCE_CACHE` modifiers before removing objects so Alembic cache data is properly freed.

`use_abc_import` is registered in `UVB_PG_settings` (properties.py). It has no effect in RBD mode.

### RBD Transform Bake (Replacing ABC for RBD)

RBD mode no longer uses ABC export/import. Instead, `_bake_source_transforms_to_keyframes` is called:

1. Source objects are directly copied into `VAT_Compute_Scene` (mesh only, no simulation state)
2. For each frame in `[start_frame, end_frame, step=frame_drop_step]`, the depsgraph-evaluated `matrix_world` is decomposed and inserted as keyframes (`location`, `rotation_quaternion`, `scale`) on the copy
3. All keyframe interpolation is set to `CONSTANT` so GN reads the exact baked transform at each frame without drift
4. No disk I/O is required

This correctly captures rigid body and any other object-level animation that the depsgraph evaluates, without needing to serialize full mesh data to disk per frame. Cleanup requires no special handling: the objects carry only standard keyframe animation data, no `MESH_SEQUENCE_CACHE` or `CacheFile`.

### Blender 5.x Layered Action API (`_iter_action_fcurves`)

Blender 4.4+ changed the `Action` datablock to a layered structure. `action.fcurves` no longer exists directly on new-style actions. A helper `_iter_action_fcurves(action)` was added to `workflow.py` to yield all FCurves from either a legacy flat action or a layered action transparently:

- if `action.fcurves` exists → legacy path (Blender < 4.4)
- otherwise → walks `action.layers → strips → channel_bags → fcurves`

All code that previously iterated `action.fcurves` directly must use this helper instead.

### `PreparedExport.end_frame_attributes`

`end_frame_attributes` is a `dict` field on `PreparedExport` (declared with `field(default_factory=dict)`). It is populated by `_apply_end_frame_export_settings` and may be patched in by `_ensure_end_frame_min_max_attributes`. All code accessing it uses `getattr(prepared, "end_frame_attributes", {}) or {}` for compatibility with older serialized state, but the field is now properly declared in the dataclass.

### RBD Packing Method

A new per-scene property `rbd_packing_method` (EnumProperty) was added to `UVB_PG_settings`. It controls how per-object ID data is packed into the VAT texture in RBD mode.

Constants defined in `constants.py`:

```python
RBD_PACKING_METHOD_UV           = "UV_PACKING"         # GN index 0
RBD_PACKING_METHOD_VERTEX_COLOR = "VERTEX_COLOR_PACKING"  # GN index 1
RBD_PACKING_METHOD_OBJECT_INDEX = "OBJECT_INDEX_PACKING"   # GN index 2
RBD_PACKING_METHOD_GN_INDEX     = { ... }  # maps string → int
```

At export time, `_RBDModeHandler.wire_extra_sockets` calls `_set_packing_method(...)` to push the integer index into the GN modifier's `Packing Method` menu socket. The socket is optional (`allow_missing=True`) so missing it only logs a WARNING.

The value is also written to the JSON output as `Vat_PackingMethod` (integer 0–2) when JSON export is enabled.

### `ShowAlpha = False` Before Normal Render in Vertex Mode

In `_export_rendered_textures_via_render`, the render-fallback path for Vertex mode (`else` branch, i.e. `use_show_alph_composite = False`) now explicitly sets `ShowAlpha = False` before rendering the Normal texture (line ~2574). This is done via the shared `show_alph_identifier` / `previous_show_alph` variables, which the existing `finally` block already restores. The `allow_missing=False` call (without the flag) is used intentionally here so a missing `ShowAlpha` socket is not silently ignored in future GN group revisions.

Rule: When rendering Normal in Vertex mode, `ShowAlpha` must be `False`. The alpha channel encodes RBD occupancy data; leaving it `True` would corrupt the Normal EXR with alpha-composite garbage.

## Known Sensitive Areas

These areas are easy to break:

- normalized GN socket-name matching
- Geometry Nodes bake invocation
- toggling `SkipGridTransfer`
- reading realized attributes from helper output
- direct texel rasterization assumptions
- UV layer name propagation
- helper-based FBX export after enabling `ShowExportMesh`

When changing any of these, test inside Blender instead of relying only on static reasoning.

### RBD Reference Data

RBD does not use a single joined reference mesh object.

Current RBD behavior:

- bake depsgraph-evaluated `matrix_world` transforms as keyframes on source copies (`_bake_source_transforms_to_keyframes`)
- create a second reference collection at the chosen reference frame (`_create_reference_frame_collection`)
- freeze each object in that reference collection at the reference frame, clear animation/modifiers/constraints
- pass that frozen collection into the GN socket `RefFrameCollection`

Vertex mode still uses the single joined `VAT_RefFrameOBJ` path.

## Known Non-Source Files

These exist in the repo but are not the main source of truth:

- `__pycache__` files
- generated zip in `dist/`

Avoid treating those as the primary implementation unless doing recovery work.

## Update Policy For Future Changes

Whenever behavior changes, update this file in the same task.

At minimum, update:

- current behavior description
- any changed workflow steps
- any changed GN socket or attribute contract
- any changed export format or file naming
- any new caveat or limitation
- any new debugging or packaging rule

## Suggested Validation Checklist

When making workflow changes, validate at least these:

1. `python3 -m compileall unreal_vat_bridge`
2. install or reload the add-on in Blender 5.1
3. run a basic texture export
4. verify expected textures are generated for the active mode
5. Vertex should export Position and Normal, while RBD should export Position only
6. if FBX path changed, verify FBX export still succeeds
7. if UV logic changed, test with missing UVs and non-zero UV index
8. if GN socket logic changed, confirm the expected sockets still resolve
