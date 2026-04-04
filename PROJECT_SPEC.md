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

- Add-on package: [unreal_vat_bridge](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge)
- Main workflow implementation: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py)
- Blender Geometry Nodes asset: [vat_nodes.blend](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/assets/vat_nodes.blend)
- Packaging script: [package.sh](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/package.sh)

## Current Target Environment

- Blender: `5.1.x`
- Add-on entry point: [__init__.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/__init__.py)
- Current workflow build id is defined in [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py)

## Repository Layout

- [README.md](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/README.md): very short project readme
- [PROJECT_SPEC.md](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/PROJECT_SPEC.md): this document
- [package.sh](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/package.sh): creates installable zip
- [unreal_vat_bridge/__init__.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/__init__.py): Blender add-on registration and `bl_info`
- [unreal_vat_bridge/constants.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/constants.py): names, modes, temp object identifiers, suffixes
- [unreal_vat_bridge/properties.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/properties.py): scene property group
- [unreal_vat_bridge/ui.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/ui.py): sidebar UI
- [unreal_vat_bridge/operators.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/operators.py): export operator and JSON copy operator
- [unreal_vat_bridge/preview.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/preview.py): preview math for texture sizing
- [unreal_vat_bridge/workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py): end-to-end export system
- [unreal_vat_bridge/assets/vat_nodes.blend](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/assets/vat_nodes.blend): Geometry Nodes groups used at runtime

## User-Facing UI

The add-on panel lives in:

- `View3D > Sidebar > Unreal VAT Bridge`

The UI implementation is part of the project spec and should be kept in sync with code changes.

The property definitions live in [properties.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/properties.py) and the drawing logic lives in [ui.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/ui.py).

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
6. `Export`

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
- `Skip Scale`

### Animation Section

Current fields:

- `Start Frame`
- `End Frame`
- `Reference Frame`
- `UV Index`
- `Source FPS` as read-only label
- `Frame Step`

Compatibility note:

- if an older Blender session still holds a stale property group and `uv_map_index` is missing, the UI currently shows `UV Index: 0 (reload addon)` instead of failing
- if `frame_drop_step` is missing, the UI shows `Frame Step: unavailable (reload addon)`
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
- `UV Index`
- `Output Folder`
- `Export JSON`
- `Export FBX`
- `Texture Prefix`
- `Texture Naming Pattern`
- `Keep Temporary Scene`

## High-Level Runtime Flow

The main entry point is `UVB_OT_export.execute()` in [operators.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/operators.py), which reloads [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py) and calls `run_export(...)`.

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

- `run_export`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L68)
- `_validate_and_prepare`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L140)
- `_create_temporary_scene`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L236)
- `_apply_ui_values_to_gn_transfer_layer`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L480)
- `_bake_geometry_nodes_animation_node`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L700)
- `_apply_end_frame_export_settings`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L865)
- `_export_rendered_textures`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L1888)
- `_export_json_metadata`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L2889)
- `_export_mesh_fbx`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L2945)

## Temporary Scene System

The add-on builds a dedicated compute scene for export.

Important temporary objects:

- temp scene name: `VAT_Compute_Scene`
- camera: `VAT_ComputeCamera`
- helper plane: `VAT_HelperPlane`
- Geometry Nodes modifier: `VAT_GeometryNodes`
- reference object name pattern: `VAT_RefFrameOBJ`
- RBD reference collection name pattern: `VAT_RefFrameCollection`

Whether the temp scene is retained or deleted is controlled by the `Keep Temporary Scene` checkbox in the `Deb` section.

## Geometry Nodes Contract

The Python side expects specific GN group names from [vat_nodes.blend](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/assets/vat_nodes.blend):

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

## Attribute And Bake Contract

The workflow reads these Geometry Nodes outputs/attributes during export:

- `TextureSize`
- `cameraorthoscale` or `cameraortho`
- `cameralocation` or `cameraposition`
- `DisplayAttribute`
- `PosDefrence`
- `UVMap`

Mode-specific range attributes:

- Vertex: `min`, `max`
- RBD: `Pos_Min`, `Pos_Max`, `Size_Min`, `Size_Max`

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
7. export that helper-derived mesh as FBX

This means the FBX should represent the final Geometry Nodes export mesh, not the original source mesh and not `VAT_RefFrameOBJ`.

Relevant function:

- `_export_mesh_fbx`: [workflow.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/workflow.py#L2945)

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

RBD mode also includes:

- `Vat_SkipScale`
- `Vat_SizeRangeMin`
- `Vat_SizeRangeMax`

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

Preview and texture sizing logic are currently simple and live in [preview.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/preview.py).

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

- [dist/unreal_vat_bridge.zip](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/dist/unreal_vat_bridge.zip)

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

- [__init__.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/__init__.py) reloads submodules when the addon is reloaded
- [operators.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/operators.py) reloads `constants`, `preview`, and `workflow` before export
- [properties.py](/Users/taiyoparent/Documents/GitHub/Unreal-VAT-Bridge/unreal_vat_bridge/properties.py) defensively re-registers the scene property group on addon enable
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

RBD no longer sends a single joined reference mesh object into Geometry Nodes.

Current RBD behavior:

- duplicate the selected source collection into the temporary scene
- create a second reference collection at the chosen reference frame
- freeze each object in that reference collection at the reference frame
- clear animation/keyframes from the frozen reference objects
- pass that collection into the GN socket `RefFrameCollection`

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
