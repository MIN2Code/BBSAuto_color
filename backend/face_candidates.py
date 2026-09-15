"""Collect per-face color evidence from multi-view render buffers."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

import numpy as np

from backend.colorize import lab_hex, srgb8_to_lab, weighted_median
from backend.mesh_edges import build_face_adjacency, grow_region
from backend.meshpack import face_geometry

_REGION_PIX_FULL = 400        # 可见像素满置信预算（≈20×20 色块）
_REGION_SPREAD_FULL = 6.0     # 区域内代表色 ΔE 离散度饱和点


@dataclass(frozen=True)
class FaceEvidence:
    """One view's trustworthy color observation for a mesh face."""

    view_index: int
    lab: tuple[float, float, float]
    visibility: float
    pixel_count: int


@dataclass(frozen=True)
class CandidateRegion:
    """A connected group of faces whose color departs from the part base."""

    region_id: int
    faces: frozenset[int]
    color_hex: str
    support_views: int
    confidence: float
    accepted: bool
    reason: str
    slot: int = 0               # 色板槽号（0=未分配），由 API/导出层赋值


_DEPTH_REL_TOLERANCE = 0.01
_DEPTH_ABS_TOLERANCE = 0.02
_SAMPLE_RADIUS = 2
_FACE_CHUNK = 65536        # 面分块：限制 (F, 25) 邻域数组的峰值内存


def collect_face_candidates(
    part: Mapping[str, Any],
    views: list[Mapping[str, Any]],
    base_color: str,
) -> dict[int, list[FaceEvidence]]:
    """Collect visible face evidence without applying any face colors.

    Each view is a mapping containing ``camera``, ``image``, ``id_buffer`` and
    ``depth``.  The ID buffer may be a 2-D integer array or an RGB buffer using
    the existing red/green 16-bit part-ID encoding.  Depth values are positive
    camera-forward distances, matching the projection code in ``routes.py``.
    """
    del base_color  # ID/depth masks, rather than a guessed color, define evidence.
    centroids, normals = face_geometry(part)
    candidates = {face_index: [] for face_index in range(len(centroids))}

    for view_index, view in enumerate(views):
        image = _array_from_view(view, "image", "render", "rgb")
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("view image must have shape (height, width, 3)")
        image = image[..., :3]
        height, width = image.shape[:2]

        id_buffer = _array_from_view(view, "id_buffer", "idbuf", "ids")
        if id_buffer.shape[:2] != (height, width):
            raise ValueError("view ID buffer must match image dimensions")
        ids = _decode_ids(id_buffer)

        depth_value = _optional_array_from_view(view, "depth", "depth_buffer", "zbuffer")
        if depth_value is not None:
            if depth_value.shape[:2] != (height, width):
                raise ValueError("view depth buffer must match image dimensions")
            depth = np.asarray(depth_value, dtype=float)
            if depth.ndim != 2:
                raise ValueError("view depth buffer must be a 2-D array")
        else:
            depth = None

        camera = _view_value(view, "camera")
        xs, ys, face_depth, gate = _project_visible(
            centroids, normals, camera, width, height)
        part_id = _part_id(part)
        gate &= ids[ys, xs] == part_id
        if depth is not None:
            tol = np.maximum(_DEPTH_ABS_TOLERANCE,
                             np.abs(face_depth) * _DEPTH_REL_TOLERANCE)
            gate &= np.abs(depth[ys, xs] - face_depth) <= tol

        face_ids = np.flatnonzero(gate)
        if not len(face_ids):
            continue
        offs = np.arange(-_SAMPLE_RADIUS, _SAMPLE_RADIUS + 1)
        dy = np.repeat(offs, len(offs))
        dx = np.tile(offs, len(offs))
        denom = float(len(offs) ** 2)
        for start in range(0, len(face_ids), _FACE_CHUNK):
            block = face_ids[start:start + _FACE_CHUNK]
            ys_n = ys[block][:, None] + dy[None, :]
            xs_n = xs[block][:, None] + dx[None, :]
            inside = ((ys_n >= 0) & (ys_n < height)
                      & (xs_n >= 0) & (xs_n < width))
            ys_c = np.clip(ys_n, 0, height - 1)
            xs_c = np.clip(xs_n, 0, width - 1)
            valid = inside & (ids[ys_c, xs_c] == part_id)
            if depth is not None:
                tol_n = np.maximum(
                    _DEPTH_ABS_TOLERANCE,
                    np.abs(face_depth[block])[:, None] * _DEPTH_REL_TOLERANCE)
                valid &= np.abs(depth[ys_c, xs_c] - face_depth[block][:, None]) <= tol_n

            flat = image[ys_c, xs_c].reshape(-1, 3)
            lab = (srgb8_to_lab(flat).reshape(ys_n.shape + (3,)).astype(float))
            lab = np.where(valid[..., None], lab, np.nan)
            counts = valid.sum(axis=1)
            ok = counts > 0
            if not ok.any():
                continue
            sub = lab[ok]
            med = np.nanmedian(sub, axis=1)
            dist = np.sqrt(((sub - med[:, None, :]) ** 2).sum(axis=2))
            # 80% 截尾阈值：np.nanquantile 逐行走 Python 太慢，改顺序统计
            # （NaN→inf 后按行排序，取第 ceil(0.8·(n-1)) 个有序值）
            counts_ok = valid[ok]
            dist_f = np.where(np.isfinite(dist), dist, np.inf)
            order = np.argsort(dist_f, axis=1)
            q_idx = np.ceil(0.8 * (counts_ok.sum(axis=1) - 1)).astype(np.int64)
            q = np.take_along_axis(dist_f, order, axis=1)[np.arange(len(q_idx)), q_idx]
            keep = dist <= q[:, None]
            kept = np.where(keep[..., None], sub, np.nan)
            trimmed = np.where(
                np.isfinite(kept).any(axis=1), np.nanmedian(kept, axis=1), med)

            for local, lab_value, pixel_count in zip(np.flatnonzero(ok), trimmed, counts[ok]):
                candidates[int(block[local])].append(
                    FaceEvidence(
                        view_index=view_index,
                        lab=tuple(float(v) for v in lab_value),
                        visibility=float(pixel_count) / denom,
                        pixel_count=int(pixel_count),
                    ))

    return candidates


def discover_regions(
    part: Mapping[str, Any],
    evidence_by_face: Mapping[int, Iterable[FaceEvidence]],
    base_lab: tuple[float, float, float],
    min_delta_e: float = 10.0,
    min_faces: int = 8,
) -> list[CandidateRegion]:
    """Discover connected secondary-color candidate regions inside a part.

    Faces whose cross-view representative Lab departs from the part base color
    by >= ``min_delta_e`` are grown into connected regions ("paint on the
    edge" semantics: BFS stops at color boundaries).  Regions smaller than
    ``min_faces`` are dropped as speckle noise.  Discovery only — acceptance
    is decided later by ``decide_regions``.
    """
    centroids, normals = face_geometry(part)
    adjacency = build_face_adjacency(np.asarray(part["faces"]))
    face_count = len(adjacency)

    base = np.asarray(base_lab, dtype=float)
    rep = np.tile(base, (face_count, 1))       # 无证据面回落基色：永不成为种子
    pixels = np.zeros(face_count)
    views: dict[int, set[int]] = {}
    for face_index, entries in evidence_by_face.items():
        entries = list(entries)
        if not entries:
            continue
        labs = np.array([e.lab for e in entries], dtype=float)
        weights = np.array([e.pixel_count for e in entries], dtype=float)
        rep[face_index] = weighted_median(labs, weights)
        pixels[face_index] = weights.sum()
        views[face_index] = {e.view_index for e in entries}

    delta = np.linalg.norm(rep - base, axis=1)
    secondary = [f for f in range(face_count) if delta[f] >= min_delta_e]

    # 法线约束关闭（180°）：涂装色块常跨折痕/硬边，颜色边界才是生长停止条件
    claimed = np.zeros(face_count, dtype=bool)
    regions = []
    for seed in secondary:
        if claimed[seed]:
            continue
        mask = grow_region([seed], adjacency, normals, rep, 180.0, min_delta_e)
        claimed |= mask
        faces = {int(f) for f in np.flatnonzero(mask)}
        if len(faces) < min_faces:
            continue
        regions.append((faces, views))

    # 确定性排序：大面积优先，同面积按最小面号；region_id 按此顺序分配
    regions.sort(key=lambda item: (-len(item[0]), min(item[0])))
    out = []
    for region_id, (faces, face_views) in enumerate(regions):
        face_list = sorted(faces)
        region_labs = rep[face_list]
        region_weights = pixels[face_list]
        color = weighted_median(region_labs, region_weights)
        # 置信度（初步，决策层 decide_regions 为最终裁决）：可见像素预算、
        # 独立视角数、区域内颜色一致性（跨视角逐面代表色的离散度）
        pix_factor = min(1.0, float(region_weights.sum()) / _REGION_PIX_FULL)
        view_factor = min(1.0, len(set().union(*face_views.values())) / 2.0)
        spread = float(np.linalg.norm(region_labs - color, axis=1).std())
        coherence = max(0.0, 1.0 - spread / _REGION_SPREAD_FULL)
        confidence = round(0.4 * pix_factor + 0.4 * view_factor + 0.2 * coherence, 3)
        out.append(CandidateRegion(
            region_id=region_id,
            faces=frozenset(faces),
            color_hex=lab_hex(color),
            support_views=len(set().union(*face_views.values())),
            confidence=confidence,
            accepted=False,
            reason="discovered",
        ))
    return out


def decide_regions(
    regions: list[CandidateRegion],
    min_confidence: float = 0.75,
    min_views: int = 2,
) -> list[CandidateRegion]:
    """Globally decide region acceptance; input order never affects results.

    Acceptance needs both enough confidence and independent view support; a
    single weak view never paints (same discipline as views_disagree_hard in
    the part-level stage).  Returns new CandidateRegion objects — inputs are
    not mutated.
    """
    out = []
    for region in regions:
        if region.confidence >= min_confidence and region.support_views >= min_views:
            out.append(replace(region, accepted=True, reason="accepted"))
        elif region.support_views < min_views:
            out.append(replace(region, accepted=False, reason="few_views"))
        else:
            out.append(replace(region, accepted=False, reason="low_confidence"))
    return out


def apply_face_layers(
    base_slots: np.ndarray,
    accepted_regions: Iterable[CandidateRegion],
    face_overrides: Mapping[int, int],
) -> np.ndarray:
    """Compose per-face slots: base -> accepted regions -> manual overrides."""
    slots = np.asarray(base_slots, dtype=np.uint8).copy()
    for region in accepted_regions:
        if not region.accepted or region.slot == 0:
            continue
        for face in region.faces:
            slots[face] = region.slot
    for face, slot in face_overrides.items():
        slots[int(face)] = int(slot)
    return slots


def _array_from_view(view: Mapping[str, Any], *names: str) -> np.ndarray:
    value = _view_value(view, *names)
    if value is None:
        raise ValueError(f"view is missing {'/'.join(names)}")
    return np.asarray(value)


def _optional_array_from_view(view: Mapping[str, Any], *names: str) -> np.ndarray | None:
    value = _view_value(view, *names, default=None)
    return None if value is None else np.asarray(value)


def _view_value(view: Mapping[str, Any], *names: str, default: Any = ...):
    if isinstance(view, Mapping):
        for name in names:
            if name in view:
                return view[name]
    else:
        for name in names:
            if hasattr(view, name):
                return getattr(view, name)
    if default is not ...:
        return default
    raise ValueError(f"view is missing {'/'.join(names)}")


def _part_id(part: Mapping[str, Any]) -> int:
    for key in ("part_id", "id", "index"):
        value = part.get(key)
        if value is not None:
            return int(value) + (1 if key == "index" else 0)
    return 1


def _decode_ids(id_buffer: np.ndarray) -> np.ndarray:
    if id_buffer.ndim == 2:
        return np.asarray(id_buffer, dtype=np.int64)
    if id_buffer.ndim == 3 and id_buffer.shape[2] >= 2:
        channels = np.asarray(id_buffer, dtype=np.int64)
        return channels[..., 0] + channels[..., 1] * 256
    raise ValueError("view ID buffer must be 2-D or RGB")


def _camera_vectors(camera: Mapping[str, Any]):
    eye = np.asarray(camera["eye"], dtype=float)
    target = np.asarray(camera["target"], dtype=float)
    up = np.asarray(camera.get("up", [0.0, 1.0, 0.0]), dtype=float)
    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    up_vector = np.cross(right, forward)
    return eye, forward, right, up_vector


def _project_visible(centroids, normals, camera, image_width, image_height):
    """向量化投影：返回像素坐标（越界安全整型）、前方深度与可见门控。

    门控 = 相机前方 + 正面朝向 + 帧内 + 坐标有限。像素坐标已按帧界裁剪，
    调用方可用它们安全索引缓冲后再收紧门控。
    """
    eye, forward, right, up = _camera_vectors(camera)
    fov = float(camera["fov"])
    viewport_width = float(camera.get("w", camera.get("width")))
    viewport_height = float(camera.get("h", camera.get("height")))
    aspect = viewport_width / viewport_height
    tangent = np.tan(np.radians(fov / 2.0))
    relative = centroids - eye
    depth = relative @ forward
    with np.errstate(divide="ignore", invalid="ignore"):
        ndc_x = ((relative @ right) / depth) / (tangent * aspect)
        ndc_y = ((relative @ up) / depth) / tangent

    # 渲染图以 object-fit:cover 显示在视口内（前端叠加层与 app.js drawImage 同
    # 语义）：图像等比放大至盖满视口、居中裁边。投影点先落到视口坐标，再逆
    # cover 映射回渲染图原始像素。纵横比一致时退化为纯等比缩放（与转台链路
    # 中前端按图像纵横比渲染 ID 缓冲的情形一致）。
    mapped_w = float(camera.get("image_width", image_width or viewport_width))
    mapped_h = float(camera.get("image_height", image_height or viewport_height))
    scale = max(viewport_width / mapped_w, viewport_height / mapped_h)
    offset_x = (viewport_width - mapped_w * scale) / 2.0
    offset_y = (viewport_height - mapped_h * scale) / 2.0
    px = ((ndc_x * 0.5 + 0.5) * viewport_width - offset_x) / scale
    py = ((1.0 - (ndc_y * 0.5 + 0.5)) * viewport_height - offset_y) / scale

    # NaN/inf 先归一到界外值，再取整裁剪：保证 ys/xs 恒为合法索引
    safe_px = np.nan_to_num(px, nan=-1.0, posinf=-1.0, neginf=-1.0)
    safe_py = np.nan_to_num(py, nan=-1.0, posinf=-1.0, neginf=-1.0)
    xs = np.clip(np.rint(safe_px).astype(np.int64), 0, int(image_width) - 1)
    ys = np.clip(np.rint(safe_py).astype(np.int64), 0, int(image_height) - 1)

    gate = (
        (depth > 0)
        & (safe_px >= 0) & (safe_px < mapped_w)
        & (safe_py >= 0) & (safe_py < mapped_h)
    )
    gate &= np.einsum("ij,ij->i", normals, eye - centroids) > 0.0
    return xs, ys, depth, gate
