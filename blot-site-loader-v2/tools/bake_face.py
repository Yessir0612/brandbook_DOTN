#!/usr/bin/env python3
"""
bake_face.py — bake a colour pass + depth pass (+ alpha/coverage) from a
3D character model (GLB), for the Phantom.land-style particle face system
used in objects.html.

WHAT THIS DOES
  1. Loads every mesh and figures out its correct WORLD-SPACE position —
     static meshes, bone-parented meshes and skinned meshes all store
     vertex data differently in glTF, and this resolves all three via the
     actual node hierarchy instead of naively trusting local coordinates.
  2. For meshes WITH a UV-mapped colour texture: samples real colour.
     For meshes WITHOUT one (no texture in the file at all — common for
     raw AI-generated geometry): falls back to synthetic 3-point studio
     lighting (key + fill + rim) shaded from vertex normals, either as
     neutral grayscale "clay" or an ink+accent-colour duotone.
  3. Densely samples the surface and splats it through a self-contained
     orthographic z-buffer projector (no GPU/Blender needed) into:
       - <name>_color.webp — RGBA colour pass. The ALPHA channel is a
         feathered coverage mask (1 = real geometry, fading to 0 outside
         the silhouette) — THIS is what stops the particle grid from
         showing a hard square background; without it every pixel outside
         the face still has some inpainted colour/depth and renders as a
         visible block.
       - <name>_depth.webp — greyscale depth pass (near=white, far=black).

USAGE
    python3 bake_face.py character.glb --out ../assets/faces/name
    # writes name_color.webp (RGBA) and name_depth.webp

  --face-mesh SUBSTRING   substring identifying the head mesh, used to
                           center the camera framing. Default: "head"
  --exclude SUBSTRING     comma-separated substrings of mesh names to
                           skip entirely (e.g. full-body rigs' arms/legs
                           if you only want a bust). Default: none
  --style grayscale|duotone   fallback shading style for untextured
                           meshes (default: grayscale)
  --duotone-shadow HEX     shadow colour for --style duotone (default
                           site ink #14151A)
  --duotone-highlight HEX  highlight colour for --style duotone (default
                           site accent #FF4310)
  --res N                  bake resolution before downsample (default 512)
  --out-res N              final saved resolution (default 256)
  --samples N              total surface samples (default 800000 — dense
                           sampling matters more now that edges are
                           feathered by real coverage, not just filled)
  --margin F               frame margin multiplier around head bounds
                           (default 1.3)
  --feather F              edge feather radius in output pixels
                           (default 6 — higher = softer/wider fade)

Requires: trimesh, numpy, pillow, scipy
"""
import argparse
import json
import os
import struct
import sys

import numpy as np
import trimesh
from PIL import Image
from scipy.ndimage import distance_transform_edt, median_filter, gaussian_filter


# ---------------------------------------------------------------------------
# glTF node-hierarchy world transforms
# ---------------------------------------------------------------------------
def compute_world_transforms(glb_path):
    with open(glb_path, 'rb') as f:
        data = f.read()
    magic, version, length = struct.unpack('<4sII', data[0:12])
    if magic != b'glTF':
        return {}, {}
    offset = 12
    chunk_len, chunk_type = struct.unpack('<II', data[offset:offset + 8])
    gltf = json.loads(data[offset + 8:offset + 8 + chunk_len])

    nodes = gltf.get('nodes', [])
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            parent[c] = i

    def local_matrix(n):
        if 'matrix' in n:
            return np.array(n['matrix'], dtype=np.float64).reshape(4, 4).T
        t = np.array(n.get('translation', [0, 0, 0]), dtype=np.float64)
        r = n.get('rotation', [0, 0, 0, 1])
        s = np.array(n.get('scale', [1, 1, 1]), dtype=np.float64)
        x, y, z, w = r
        R = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        M = np.eye(4)
        M[:3, :3] = R @ np.diag(s)
        M[:3, 3] = t
        return M

    _cache = {}
    def world_matrix(idx):
        if idx in _cache:
            return _cache[idx]
        m = local_matrix(nodes[idx])
        p = parent.get(idx)
        if p is not None:
            m = world_matrix(p) @ m
        _cache[idx] = m
        return m

    mesh_name_to_transform = {}
    mesh_name_to_skinned = {}
    meshes = gltf.get('meshes', [])
    for i, n in enumerate(nodes):
        if 'mesh' in n:
            mesh_name = meshes[n['mesh']].get('name')
            if mesh_name is None:
                continue
            mesh_name_to_transform[mesh_name] = world_matrix(i)
            mesh_name_to_skinned[mesh_name] = 'skin' in n
    return mesh_name_to_transform, mesh_name_to_skinned


def get_texture_image(mesh):
    try:
        mat = mesh.visual.material
        img = getattr(mat, 'baseColorTexture', None)
        if img is None:
            img = getattr(mat, 'image', None)
        if img is None:
            return None
        return img.convert('RGB')
    except Exception:
        return None


def sample_textured(mesh, n):
    """Sample surface + look up real colour via UV."""
    pts, face_idx = trimesh.sample.sample_surface(mesh, n)
    tri = mesh.faces[face_idx]
    tri_verts = mesh.vertices[tri]
    bary = trimesh.triangles.points_to_barycentric(tri_verts, pts)
    uv = mesh.visual.uv
    tri_uv = uv[tri]
    pt_uv = (bary[:, :, None] * tri_uv).sum(axis=1)
    tex_arr = np.array(get_texture_image(mesh))
    th, tw = tex_arr.shape[0], tex_arr.shape[1]
    px = np.clip((pt_uv[:, 0] * tw).astype(int), 0, tw - 1)
    py = np.clip(((1.0 - pt_uv[:, 1]) * th).astype(int), 0, th - 1)
    colors = tex_arr[py, px, :3]
    return pts, colors


def hex_to_rgb(h):
    h = h.lstrip('#')
    return np.array([int(h[i:i+2], 16) for i in (0, 2, 4)], dtype=np.float64)


def sample_shaded(mesh, n, style, shadow_rgb, highlight_rgb):
    """Sample surface + synthesize colour from normal-based studio lighting
    (used for meshes with no colour texture at all)."""
    mesh = mesh.copy()
    mesh.fix_normals()
    pts, face_idx = trimesh.sample.sample_surface(mesh, n)
    tri = mesh.faces[face_idx]
    tri_verts = mesh.vertices[tri]
    bary = trimesh.triangles.points_to_barycentric(tri_verts, pts)
    vertex_normals = mesh.vertex_normals
    tri_normals = vertex_normals[tri]
    pt_normals = (bary[:, :, None] * tri_normals).sum(axis=1)
    pt_normals = pt_normals / (np.linalg.norm(pt_normals, axis=1, keepdims=True) + 1e-8)

    key_dir = np.array([0.45, 0.35, 0.82]); key_dir /= np.linalg.norm(key_dir)
    fill_dir = np.array([-0.5, 0.1, 0.6]); fill_dir /= np.linalg.norm(fill_dir)
    rim_dir = np.array([0.0, 0.3, -0.9]); rim_dir /= np.linalg.norm(rim_dir)

    key = np.clip(pt_normals @ key_dir, 0, 1) * 0.75
    fill = np.clip(pt_normals @ fill_dir, 0, 1) * 0.30
    rim = np.clip(pt_normals @ rim_dir, 0, 1) * 0.35
    shade = np.clip(key + fill + rim + 0.12, 0, 1)

    if style == 'duotone':
        t = shade[:, None]
        t_contrast = np.clip((t - 0.25) / 0.55, 0, 1) ** 1.3
        colors = (shadow_rgb * (1 - t_contrast) + highlight_rgb * t_contrast)
    else:
        colors = np.stack([shade * 255] * 3, axis=-1)
    return pts, colors.astype(np.uint8)


def fill_holes(img, mask):
    idx = distance_transform_edt(~mask, return_distances=False, return_indices=True)
    return img[tuple(idx)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('glb')
    ap.add_argument('--out', required=True)
    ap.add_argument('--face-mesh', default='head')
    ap.add_argument('--exclude', default='')
    ap.add_argument('--style', choices=['grayscale', 'duotone'], default='grayscale')
    ap.add_argument('--duotone-shadow', default='#14151A')
    ap.add_argument('--duotone-highlight', default='#FF4310')
    ap.add_argument('--res', type=int, default=512)
    ap.add_argument('--out-res', type=int, default=256)
    ap.add_argument('--samples', type=int, default=800000)
    ap.add_argument('--margin', type=float, default=1.3)
    ap.add_argument('--feather', type=float, default=6.0)
    args = ap.parse_args()

    exclude = [s.strip().lower() for s in args.exclude.split(',') if s.strip()]
    shadow_rgb = hex_to_rgb(args.duotone_shadow)
    highlight_rgb = hex_to_rgb(args.duotone_highlight)

    transforms, skinned = compute_world_transforms(args.glb)
    scene = trimesh.load(args.glb)
    geo = scene.geometry if isinstance(scene, trimesh.Scene) else {'mesh': scene}

    usable = [n for n in geo if not any(x in n.lower() for x in exclude)]
    if not usable:
        sys.exit('No usable meshes found (check --exclude).')

    head_meshes = [n for n in usable if args.face_mesh.lower() in n.lower()] or usable

    all_pts, all_colors = [], []
    per_mesh_n = max(1, args.samples // len(usable))

    for name in usable:
        mesh = geo[name].copy()
        if name in transforms and not skinned.get(name, False):
            mesh.apply_transform(transforms[name])
        has_tex = hasattr(mesh.visual, 'uv') and mesh.visual.uv is not None and get_texture_image(mesh) is not None
        if has_tex:
            pts, colors = sample_textured(mesh, per_mesh_n)
            mode = 'texture'
        else:
            pts, colors = sample_shaded(mesh, per_mesh_n, args.style, shadow_rgb, highlight_rgb)
            mode = f'synthetic-{args.style}'
        if len(pts) == 0:
            continue
        all_pts.append(pts)
        all_colors.append(colors)
        print(f'  "{name}": {len(pts)} pts [{mode}]')

    points = np.concatenate(all_pts, axis=0)
    colors = np.concatenate(all_colors, axis=0)
    print('total samples:', len(points))

    def mesh_center(n):
        m = geo[n]
        if n in transforms and not skinned.get(n, False):
            return m.apply_transform(transforms[n]).vertices.mean(axis=0)
        return m.vertices.mean(axis=0)

    head_center = np.mean([mesh_center(n) for n in head_meshes], axis=0)
    p = points - head_center

    x_lo, x_hi = np.percentile(p[:, 0], [1, 99])
    y_lo, y_hi = np.percentile(p[:, 1], [1, 99])
    half = max(x_hi - x_lo, y_hi - y_lo) / 2 * args.margin
    cx = (x_hi + x_lo) / 2
    cy = (y_hi + y_lo) / 2 - half * 0.06

    RES = args.res
    u = (p[:, 0] - (cx - half)) / (2 * half)
    v = (p[:, 1] - (cy - half)) / (2 * half)
    px = np.clip((u * RES).astype(int), 0, RES - 1)
    py = np.clip(((1 - v) * RES).astype(int), 0, RES - 1)
    z = p[:, 2]

    zbuffer = np.full((RES, RES), -1e9, dtype=np.float32)
    color_buffer = np.zeros((RES, RES, 3), dtype=np.uint8)
    mask = np.zeros((RES, RES), dtype=bool)

    order = np.argsort(z)
    px_o, py_o, z_o, c_o = px[order], py[order], z[order], colors[order]
    zbuffer[py_o, px_o] = z_o
    color_buffer[py_o, px_o] = c_o
    mask[py_o, px_o] = True

    valid_z = zbuffer[mask]
    zmin, zmax = valid_z.min(), valid_z.max()
    depth_norm = np.zeros((RES, RES), dtype=np.float32)
    depth_norm[mask] = (zbuffer[mask] - zmin) / (zmax - zmin + 1e-6)
    depth_img = (depth_norm * 255).astype(np.uint8)
    depth_img[~mask] = 0

    depth_filled = fill_holes(depth_img, mask).astype(np.float32)
    depth_filled = median_filter(depth_filled, size=5)
    depth_filled = gaussian_filter(depth_filled, sigma=0.6).astype(np.uint8)

    color_filled = np.zeros_like(color_buffer)
    for c in range(3):
        cf = fill_holes(color_buffer[:, :, c], mask).astype(np.float32)
        cf = median_filter(cf, size=5)
        cf = gaussian_filter(cf, sigma=0.6)
        color_filled[:, :, c] = cf.astype(np.uint8)

    # --- Coverage / alpha channel: THIS is what removes the hard square
    # background. mask = True only where real geometry actually splatted;
    # everything else is inpainted filler with no real meaning, so it must
    # fade to zero alpha rather than render as an opaque block.
    coverage = mask.astype(np.float32)
    # small dilation first so the true silhouette edge isn't clipped short,
    # and to close small pinhole gaps from sparse sampling
    coverage = gaussian_filter(coverage, sigma=2.2)
    coverage = (coverage > 0.35).astype(np.float32)
    # feather the edge outward into a soft falloff
    dist_outside = distance_transform_edt(coverage == 0)
    falloff = np.clip(1.0 - dist_outside / max(args.feather, 1e-3), 0, 1)
    alpha = np.maximum(coverage, falloff)
    alpha = gaussian_filter(alpha, sigma=max(args.feather * 0.35, 0.8))
    alpha_img = np.clip(alpha * 255, 0, 255).astype(np.uint8)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)

    depth_out = Image.fromarray(depth_filled, mode='L').resize((args.out_res, args.out_res), Image.LANCZOS)
    depth_out.save(f'{args.out}_depth.webp', 'WEBP', quality=88)

    rgba = np.dstack([color_filled, alpha_img])
    color_out = Image.fromarray(rgba, mode='RGBA').resize((args.out_res, args.out_res), Image.LANCZOS)
    color_out.save(f'{args.out}_color.webp', 'WEBP', quality=88)

    print(f'saved {args.out}_color.webp (RGBA, alpha=coverage) + {args.out}_depth.webp')
    print('Add/update the entry in assets/faces/manifest.json to use it.')


if __name__ == '__main__':
    main()
