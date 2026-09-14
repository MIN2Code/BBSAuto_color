"""Face adjacency and edge-aware mesh region growing."""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np


def build_face_adjacency(faces: np.ndarray) -> list[list[int]]:
    """Return face indices sharing an undirected mesh edge."""
    faces = np.asarray(faces)
    if faces.ndim != 2 or faces.shape[1] < 3:
        raise ValueError("faces must be a 2-D array with at least three vertices per face")

    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_index, face in enumerate(faces):
        for vertex_a, vertex_b in zip(face, np.roll(face, -1)):
            edge = tuple(sorted((int(vertex_a), int(vertex_b))))
            edge_faces[edge].append(face_index)

    adjacency = [set() for _ in range(len(faces))]
    for incident_faces in edge_faces.values():
        for face_index in incident_faces:
            adjacency[face_index].update(
                other for other in incident_faces if other != face_index
            )
    return [sorted(neighbors) for neighbors in adjacency]


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
