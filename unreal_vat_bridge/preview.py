import math
from dataclasses import dataclass

from . import constants


@dataclass
class ExportPreview:
    element_count: int
    sampled_frame_count: int
    texture_width: int
    texture_height: int
    estimated_aspect_ratio: str
    estimated_file_size_mb: float


def get_source_fps(scene):
    fps = float(scene.render.fps)
    fps_base = float(scene.render.fps_base) if scene.render.fps_base else 1.0
    return fps / fps_base if fps_base > 0.0 else fps


def get_frame_drop_step(settings):
    try:
        step = int(getattr(settings, "frame_drop_step", 1))
    except (TypeError, ValueError, AttributeError):
        step = 1
    return max(1, step)


def get_sampled_frame_count(settings):
    frame_span = (settings.end_frame - settings.start_frame) + 1
    frame_span = max(frame_span, 1)
    step = get_frame_drop_step(settings)
    sampled = int(math.ceil(frame_span / float(step)))
    return max(sampled, 1)


def get_element_count(settings):
    if settings.export_mode == constants.EXPORT_MODE_VERTEX:
        use_collection = bool(getattr(settings, "vertex_use_collection", False))
        if use_collection:
            collection = getattr(settings, "vertex_source_collection", None)
            if not collection:
                return 0
            total_vertices = 0
            for obj in collection.objects:
                if obj.type == "MESH" and obj.data:
                    total_vertices += len(obj.data.vertices)
            return total_vertices

        obj = getattr(settings, "vertex_source_object", None)
        if not obj or obj.type != "MESH" or not obj.data:
            return 0
        return len(obj.data.vertices)

    if settings.rigid_source_type == constants.RIGID_SOURCE_TYPE_SINGLE:
        return 1 if settings.rigid_source_object else 0

    collection = settings.rigid_source_collection
    if not collection:
        return 0
    return len(collection.objects)


def estimate_texture_dimensions(pixel_count):
    count = max(1, pixel_count)
    width = int(math.ceil(math.sqrt(count)))
    height = int(math.ceil(count / width))
    return width, height


def build_preview(context, settings):
    sampled_frame_count = get_sampled_frame_count(settings)
    element_count = get_element_count(settings)
    total_pixels = max(1, element_count * sampled_frame_count)
    texture_width, texture_height = estimate_texture_dimensions(total_pixels)

    ratio = float(texture_width) / float(texture_height)
    estimated_aspect_ratio = f"{ratio:.3f}:1"

    # Position and Normal textures, RGBA, half float (2 bytes per channel).
    bytes_per_texel = 4 * 2
    texture_count = 2
    estimated_bytes = texture_width * texture_height * bytes_per_texel * texture_count
    estimated_file_size_mb = estimated_bytes / (1024.0 * 1024.0)

    return ExportPreview(
        element_count=element_count,
        sampled_frame_count=sampled_frame_count,
        texture_width=texture_width,
        texture_height=texture_height,
        estimated_aspect_ratio=estimated_aspect_ratio,
        estimated_file_size_mb=estimated_file_size_mb,
    )
