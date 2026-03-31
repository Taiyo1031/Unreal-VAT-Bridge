ADDON_NAME = "Unreal VAT Bridge"
PANEL_CATEGORY = "Unreal VAT Bridge"

TEMP_SCENE_BASENAME = "VAT_Compute_Scene"
TEMP_CAMERA_NAME = "VAT_ComputeCamera"
TEMP_HELPER_PLANE_NAME = "VAT_HelperPlane"
TEMP_GN_MODIFIER_NAME = "VAT_GeometryNodes"

EXPORT_MODE_VERTEX = "VERTEX"
EXPORT_MODE_RIGID = "RIGID"
RIGID_SOURCE_TYPE_SINGLE = "SINGLE_OBJECT"
RIGID_SOURCE_TYPE_COLLECTION = "COLLECTION"

EXPORT_MODE_ITEMS = (
    (EXPORT_MODE_VERTEX, "Vertex", "Mesh deformation workflows"),
    (EXPORT_MODE_RIGID, "Rigid", "Object-based motion workflows"),
)

RIGID_SOURCE_TYPE_ITEMS = (
    (RIGID_SOURCE_TYPE_SINGLE, "Single Object", "Use one source object"),
    (RIGID_SOURCE_TYPE_COLLECTION, "Collection", "Use a source collection"),
)

FRAME_DROP_ITEMS = (
    ("1", "Use Every Frame", "Use all source frames"),
    ("2", "Use Every 2nd Frame", "Sample one frame every 2 frames"),
    ("3", "Use Every 3rd Frame", "Sample one frame every 3 frames"),
    ("4", "Use Every 4th Frame", "Sample one frame every 4 frames"),
)

TEXTURE_FORMAT_ITEMS = (("EXR", "EXR", "Current default format"),)
PIXEL_TYPE_ITEMS = (("HALF_FLOAT", "Half Float", "Current default pixel type"),)
COMPRESSION_ITEMS = (("ZIP", "ZIP", "Current default compression"),)
ATTRIBUTE_OUTPUT_ITEMS = (("SEPARATE_FILES", "Separate Files", "Attribute textures are split"),)

GN_GROUP_BY_MODE = {
    EXPORT_MODE_VERTEX: "ComputeVertexVAT",
    EXPORT_MODE_RIGID: "ComputeRigidVAT",
}

POSITION_SUFFIX = "Position"
NORMAL_SUFFIX = "Normal"