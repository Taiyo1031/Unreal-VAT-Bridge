# Unreal-VAT-Bridge
Blender-to-Unreal pipeline for baking and exporting Vertex Animation Textures.

## Project Spec
See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the current workflow, system notes, export contract, and maintenance rules.

## Install In Blender
1. Open `Edit > Preferences > Add-ons > Install...`
2. Select [dist/unreal_vat_bridge.zip](dist/unreal_vat_bridge.zip)
3. Enable `Unreal VAT Bridge`

## Packaging
- Run `./package.sh` from the repository root to create `dist/unreal_vat_bridge.zip`.
- The ZIP must contain `unreal_vat_bridge/` at the top level (not the whole repository).
