"""Face adjacency and edge-aware mesh region growing."""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np


def vertex_weld_map(vertices: np.ndarray) -> np.ndarray:
    """Return remap[i]: canonical index of vertex i, merging by exact position.

    STL 是三角面片汤（每个三角形自带顶点副本，process=False 不合并），
    顶点索引层面的"共享边"不存在。导出器写出的同一位置顶点是位级相同的
    float32 → 转 float64 后按精确相等焊接即可，无需容差。
    """
    v = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64))
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError("vertices must have shape (N, 3)")
    view = v.view(dtype=[("x", np.float64), ("y", np.float64), ("z", np.float64)]).ravel()
    _, _, inverse = np.unique(view, return_index=True, return_inverse=True)
    return np.asarray(inverse, dtype=np.int64)


def build_face_adjacency(faces: np.ndarray) -> list[list[int]]:
    """Return face indices sharing an undirected mesh edge.

    向量化边分组（排序键 + 稳定排序），替代逐边 Python 字典：洛茜最大件
    50 万面 12.5s → 亚秒级。返回值与旧实现一致（每面升序邻居列表）。
    """
    faces = np.asarray(faces)
    if faces.ndim != 2 or faces.shape[1] < 3:
        raise ValueError("faces must be a 2-D array with at least three vertices per face")
    n_faces = len(faces)
    if not n_faces:
        return []

    fv = faces[:, :3].astype(np.int64)
    edges = np.concatenate([fv[:, [0, 1]], fv[:, [1, 2]], fv[:, [2, 0]]])
    edges.sort(axis=1)                                  # 无向边：小端在前
    n_vert = int(fv.max()) + 1
    keys = edges[:, 0] * n_vert + edges[:, 1]
    face_of_edge = np.tile(np.arange(n_faces, dtype=np.int64), 3)

    _, inv = np.unique(keys, return_inverse=True)
    order = np.lexsort((face_of_edge, inv))             # 组内按面号升序
    inv_s, face_s = inv[order], face_of_edge[order]
    boundaries = np.flatnonzero(np.concatenate(([True], inv_s[1:] != inv_s[:-1])))
    ends = np.concatenate((boundaries[1:], [len(inv_s)]))
    sizes = ends - boundaries
    starts2 = boundaries[sizes == 2]                    # 流形共享边（恰 2 面共边）
    if len(starts2):
        a = face_s[starts2]
        b = face_s[starts2 + 1]
        src = np.concatenate([a, b])
        dst = np.concatenate([b, a])
        order2 = np.lexsort((dst, src))
        src_s, dst_s = src[order2], dst[order2]
        pos = np.searchsorted(src_s, np.arange(n_faces), side="left")
        pos_end = np.searchsorted(src_s, np.arange(n_faces), side="right")
        adjacency = [dst_s[p:q].tolist() for p, q in zip(pos, pos_end)]
    else:
        adjacency = [[] for _ in range(n_faces)]
    # 非流形边（>2 面共边）罕见：逐组两两全连兜底
    for start, end in zip(boundaries[sizes > 2], ends[sizes > 2]):
        group = [int(x) for x in face_s[start:end]]
        for face_index in group:
            merged = set(adjacency[face_index])
            merged.update(x for x in group if x != face_index)
            adjacency[face_index] = sorted(merged)
    return adjacency


def grow_region(
    seed_faces,
    adjacency,
    normals,
    colors_lab,
    max_normal_deg,
    max_delta_e,
) -> np.ndarray:
    """Grow a connected face region constrained by normals and Lab color."""
    normals = np.asarray(normals, dtype=float)
    colors_lab = np.asarray(colors_lab, dtype=float)
    face_count = len(adjacency)
    if normals.ndim != 2 or normals.shape[0] != face_count:
        raise ValueError("normals must contain one vector per face")
    if colors_lab.ndim != 2 or colors_lab.shape[0] != face_count:
        raise ValueError("colors_lab must contain one color per face")

    seeds = list(seed_faces)
    if any(face < 0 or face >= face_count for face in seeds):
        raise IndexError("seed face index out of range")
    mask = np.zeros(face_count, dtype=bool)
    if not seeds:
        return mask

    seeds = list(dict.fromkeys(seeds))
    mask[seeds] = True
    representative_color = colors_lab[seeds].mean(axis=0)
    queue = deque(seeds)
    normal_limit = float(max_normal_deg)
    color_limit = float(max_delta_e)

    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if mask[neighbor]:
                continue
            color_delta = float(np.linalg.norm(colors_lab[neighbor] - representative_color))
            if color_delta > color_limit:
                continue
            current_normal = normals[current]
            neighbor_normal = normals[neighbor]
            current_length = np.linalg.norm(current_normal)
            neighbor_length = np.linalg.norm(neighbor_normal)
            if current_length == 0 or neighbor_length == 0:
                continue
            cosine = float(np.dot(current_normal, neighbor_normal) / (current_length * neighbor_length))
            angle = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
            if angle > normal_limit:
                continue
            mask[neighbor] = True
            queue.append(neighbor)
    return mask
