ADDON_NAME = "Unreal VAT Bridge"
PANEL_CATEGORY = "Unreal VAT Bridge"
UI_VERSION_LABEL = "v12"

TEMP_SCENE_BASENAME = "VAT_Compute_Scene"
TEMP_CAMERA_NAME = "VAT_ComputeCamera"
TEMP_HELPER_PLANE_NAME = "VAT_HelperPlane"
TEMP_GN_MODIFIER_NAME = "VAT_GeometryNodes"

EXPORT_MODE_VERTEX = "VERTEX"
EXPORT_MODE_RBD = "RBD"

EXPORT_MODE_ITEMS = (
    (EXPORT_MODE_VERTEX, "Vertex", "Mesh deformation workflows"),
    (EXPORT_MODE_RBD, "RBD", "Object-based motion workflows"),
)

TEXTURE_FORMAT_ITEMS = (("EXR", "EXR", "Current default format"),)
PIXEL_TYPE_ITEMS = (("HALF_FLOAT", "Half Float", "Current default pixel type"),)
COMPRESSION_ITEMS = (("ZIP", "ZIP", "Current default compression"),)
ATTRIBUTE_OUTPUT_ITEMS = (("SEPARATE_FILES", "Separate Files", "Attribute textures are split"),)

GN_GROUP_BY_MODE = {
    EXPORT_MODE_VERTEX: "ComputeVertexVAT",
    EXPORT_MODE_RBD: "ComputeRBDtoVAT",
}

POSITION_SUFFIX = "Position"
NORMAL_SUFFIX = "Normal"

RBD_PACKING_METHOD_UV = "UV_PACKING"
RBD_PACKING_METHOD_VERTEX_COLOR = "VERTEX_COLOR_PACKING"
RBD_PACKING_METHOD_OBJECT_INDEX = "OBJECT_INDEX_PACKING"

RBD_PACKING_METHOD_ITEMS = (
    (RBD_PACKING_METHOD_UV, "UV Packing", "Use a UV channel to identify each object"),
    (RBD_PACKING_METHOD_VERTEX_COLOR, "Vertex Color Packing", "Use vertex color to identify each object"),
    (RBD_PACKING_METHOD_OBJECT_INDEX, "Object Index Packing", "Use a per-object index to identify each object"),
)

# Integer index sent to the Geometry Nodes 'Packing Method' menu socket.
RBD_PACKING_METHOD_GN_INDEX = {
    RBD_PACKING_METHOD_UV: 0,
    RBD_PACKING_METHOD_VERTEX_COLOR: 1,
    RBD_PACKING_METHOD_OBJECT_INDEX: 2,
}
