"""
ComfyUI-Save-Separate-Metadata

Save images as JPG, WebP, or PNG with workflow metadata stored
in separate .meta/ sidecar JSON files instead of embedding it
in the image file itself.

Compatible with ComfyUI-Drawer for gallery browsing, metadata
search, and drag-and-drop workflow restoration.
"""

import json
import math
import os
import threading
import time
import numpy as np
from PIL import Image

import folder_paths
from comfy.cli_args import args

from aiohttp import web
import server

WEB_DIRECTORY = "./web"

META_DIR_NAME = ".meta"
_DRAWER_PROVIDER_NAME = "save-separate-metadata"
_drawer_provider_registered = False
_drawer_unregister_provider = None


# ── Utility helpers ──

def _sanitize_for_json(obj):
    """Recursively replace NaN/Infinity with None for valid JSON output.
    Python's json.dump outputs NaN as 'NaN' which is not valid JSON
    and causes SyntaxError in JavaScript's JSON.parse().
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


def _read_sidecar_json(meta_path):
    """Read a sidecar JSON file and return raw ComfyUI metadata."""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(meta, dict):
        return None
    if not (meta.get("prompt") or meta.get("workflow")):
        return None
    return meta


def _sidecar_path_for_media(root_path, subfolder, filename):
    base_name = os.path.splitext(filename)[0]
    return os.path.join(root_path, subfolder, META_DIR_NAME, f"{base_name}.json")


def _drawer_metadata_provider(ctx):
    """Read .meta sidecars for Drawer's raw metadata pipeline."""
    root_path = ctx.get("root_path") or folder_paths.get_output_directory()
    subfolder = (ctx.get("subfolder") or "").replace("\\", "/").strip("/")
    filename = ctx.get("name") or ctx.get("filename") or ""
    if not root_path or not filename:
        return None

    media_path = ctx.get("path") or os.path.join(root_path, subfolder, filename)
    if os.path.basename(os.path.dirname(media_path)) == META_DIR_NAME:
        return None

    meta_path = _sidecar_path_for_media(root_path, subfolder, filename)
    return _read_sidecar_json(meta_path)


def _register_drawer_provider():
    """Register the raw sidecar reader with Drawer when Drawer is available."""
    global _drawer_provider_registered, _drawer_unregister_provider
    if _drawer_provider_registered:
        return True

    register = getattr(
        server.PromptServer.instance,
        "comfy_drawer_register_metadata_provider",
        None,
    )
    if register is None:
        try:
            from comfyui_drawer import register_metadata_provider as register
        except Exception:
            register = None

    if register is None:
        return False

    try:
        _drawer_unregister_provider = register(
            _drawer_metadata_provider,
            name=_DRAWER_PROVIDER_NAME,
            priority=40,
        )
        _drawer_provider_registered = True
        print("[SepMeta] Registered Drawer metadata provider")
        return True
    except Exception as e:
        print(f"[SepMeta] Drawer metadata provider registration failed: {e}")
        return False


def _retry_register_drawer_provider():
    for _ in range(120):
        if _register_drawer_provider():
            return
        time.sleep(0.5)


threading.Thread(target=_retry_register_drawer_provider, daemon=True).start()


def _save_sidecar_meta(full_output_folder, base_name, subfolder,
                       filename, prompt, extra_pnginfo):
    """Save workflow metadata as a .meta/ sidecar JSON file.

    Directory structure:
        output/
        ├── subfolder/
        │   ├── image_00001_.jpg          ← image file
        │   └── .meta/
        │       └── image_00001_.json     ← sidecar metadata
    """
    if args.disable_metadata:
        return

    meta_dir = os.path.join(full_output_folder, META_DIR_NAME)
    os.makedirs(meta_dir, exist_ok=True)

    meta = {}
    if prompt is not None:
        meta["prompt"] = prompt
    if extra_pnginfo is not None:
        for key in extra_pnginfo:
            meta[key] = extra_pnginfo[key]

    meta_path = os.path.join(meta_dir, f"{base_name}.json")
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(_sanitize_for_json(meta), f, ensure_ascii=False)
    _register_drawer_provider()


# ── API Endpoints ──

@server.PromptServer.instance.routes.get("/sepmeta/get_meta")
async def get_meta(request):
    """Retrieve sidecar metadata for a given image file.

    Query params:
        filename  - image filename (e.g. "image_00001_.jpg")
        subfolder - subfolder within output/ (optional)
        exact     - if "1", only search the specified subfolder
    """
    filename = request.query.get("filename", "")
    subfolder = request.query.get("subfolder", "")
    exact = request.query.get("exact", "")

    if not filename:
        return web.json_response({})

    output_dir = folder_paths.get_output_directory()
    base_name = os.path.splitext(filename)[0]

    search_dirs = []
    if subfolder or exact:
        search_dirs.append(os.path.join(output_dir, subfolder, META_DIR_NAME))
    else:
        for dirpath, dirnames, _ in os.walk(output_dir):
            if META_DIR_NAME in dirnames:
                search_dirs.append(os.path.join(dirpath, META_DIR_NAME))

    matches = []
    for meta_dir in search_dirs:
        meta_path = os.path.join(meta_dir, f"{base_name}.json")
        if os.path.isfile(meta_path):
            matches.append(meta_path)

    if not matches:
        return web.json_response({})

    if len(matches) > 1 and not (subfolder or exact):
        return web.json_response({
            "error": "ambiguous",
            "message": "Multiple sidecar metadata files match this filename.",
            "matches": len(matches),
        }, status=409)

    for meta_path in matches:
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    return web.json_response(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue

    return web.json_response({})


@server.PromptServer.instance.routes.post("/sepmeta/move_meta")
async def move_meta(request):
    """Move .meta sidecar files when images are moved (Drawer integration)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    root = body.get("root", "output")
    src_root = body.get("srcRoot", root)
    files = body.get("files", [])

    dst_base_dir = folder_paths.get_directory_by_type(root)
    src_base_dir = folder_paths.get_directory_by_type(src_root)
    if not dst_base_dir or not src_base_dir:
        return web.json_response({"error": "unknown root"}, status=400)

    moved = 0
    for entry in files:
        if entry.get("isFolder"):
            continue
        src_sub = entry.get("from_subfolder", entry.get("srcSubfolder", entry.get("subfolder", "")))
        dst_sub = entry.get("to_subfolder", entry.get("destSubfolder", src_sub))
        name = entry.get("name", "")
        new_name = entry.get("to_name", entry.get("newName", name))
        if not name:
            continue

        base = os.path.splitext(name)[0]
        new_base = os.path.splitext(new_name or name)[0]
        src_meta = os.path.join(src_base_dir, src_sub, META_DIR_NAME, f"{base}.json")
        if not os.path.isfile(src_meta):
            continue

        dst_meta_dir = os.path.join(dst_base_dir, dst_sub, META_DIR_NAME)
        os.makedirs(dst_meta_dir, exist_ok=True)
        dst_meta = os.path.join(dst_meta_dir, f"{new_base}.json")

        try:
            os.replace(src_meta, dst_meta)
            moved += 1
        except OSError:
            pass

    return web.json_response({"moved": moved})


@server.PromptServer.instance.routes.post("/sepmeta/delete_meta")
async def delete_meta(request):
    """Delete .meta sidecar files when images are deleted (Drawer integration)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    root = body.get("root", "output")
    files = body.get("files", [])

    base_dir = folder_paths.get_directory_by_type(root)
    if not base_dir:
        return web.json_response({"error": "unknown root"}, status=400)

    deleted = 0
    for entry in files:
        if entry.get("isFolder"):
            continue
        subfolder = entry.get("subfolder", entry.get("srcSubfolder", entry.get("from_subfolder", "")))
        name = entry.get("name", "")
        if not name:
            continue

        base = os.path.splitext(name)[0]
        meta_path = os.path.join(base_dir, subfolder, META_DIR_NAME, f"{base}.json")
        if not os.path.isfile(meta_path):
            continue

        try:
            os.remove(meta_path)
            deleted += 1
        except OSError:
            pass

    return web.json_response({"deleted": deleted})


@server.PromptServer.instance.routes.post("/sepmeta/cleanup_meta")
async def cleanup_meta(request):
    """Delete orphaned .meta JSON files (no corresponding image exists)."""
    output_dir = folder_paths.get_output_directory()
    deleted = 0

    for dirpath, _, filenames in os.walk(output_dir):
        if os.path.basename(dirpath) != META_DIR_NAME:
            continue
        parent = os.path.dirname(dirpath)
        for fname in filenames:
            if not fname.endswith(".json"):
                continue
            stem = os.path.splitext(fname)[0]
            has_image = any(
                os.path.isfile(os.path.join(parent, stem + ext))
                for ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp')
            )
            if not has_image:
                try:
                    os.remove(os.path.join(dirpath, fname))
                    deleted += 1
                except OSError:
                    pass

    return web.json_response({"deleted": deleted})


# ── Node Classes ──

class SaveJPGSeparateMeta:
    """Save images as JPG with workflow metadata in separate .meta/ files.

    The image is saved as a standard JPEG without any embedded metadata.
    Workflow data (prompt + workflow graph) is stored as a companion JSON
    file in a .meta/ subdirectory, linked by filename.
    """

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100,
                                    "tooltip": "JPG quality (1-100)."}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image"
    DESCRIPTION = "Save images as JPG. Workflow metadata is stored in separate .meta/ JSON files."

    def save_images(self, images, filename_prefix="ComfyUI", quality=95,
                    prompt=None, extra_pnginfo=None):
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir,
                images[0].shape[1], images[0].shape[0])

        results = []
        for batch_number, image in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            fname_batch = filename.replace("%batch_num%", str(batch_number))
            file = f"{fname_batch}_{counter:05}_.jpg"
            base_name = os.path.splitext(file)[0]

            img.save(os.path.join(full_output_folder, file),
                     format="JPEG", quality=quality, optimize=True)
            _save_sidecar_meta(full_output_folder, base_name, subfolder,
                               file, prompt, extra_pnginfo)

            results.append({"filename": file, "subfolder": subfolder,
                            "type": self.type})
            counter += 1

        return {"ui": {"images": results}}


class SaveWebPSeparateMeta:
    """Save images as WebP with workflow metadata in separate .meta/ files.

    WebP typically offers ~30% smaller files than JPG at equivalent quality.
    """

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "quality": ("INT", {"default": 90, "min": 1, "max": 100,
                                    "tooltip": "WebP quality (1-100)."}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image"
    DESCRIPTION = "Save images as WebP. Workflow metadata is stored in separate .meta/ JSON files."

    def save_images(self, images, filename_prefix="ComfyUI", quality=90,
                    prompt=None, extra_pnginfo=None):
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir,
                images[0].shape[1], images[0].shape[0])

        results = []
        for batch_number, image in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            fname_batch = filename.replace("%batch_num%", str(batch_number))
            file = f"{fname_batch}_{counter:05}_.webp"
            base_name = os.path.splitext(file)[0]

            img.save(os.path.join(full_output_folder, file),
                     format="WEBP", quality=quality)
            _save_sidecar_meta(full_output_folder, base_name, subfolder,
                               file, prompt, extra_pnginfo)

            results.append({"filename": file, "subfolder": subfolder,
                            "type": self.type})
            counter += 1

        return {"ui": {"images": results}}


class SavePNGSeparateMeta:
    """Save images as clean PNG with workflow metadata in separate .meta/ files.

    Unlike the built-in Save Image node which embeds workflow data inside the
    PNG file, this node saves a standard PNG and stores metadata externally.

    Benefits over embedded metadata:
    - PNG file is a clean image without hidden data
    - Metadata survives image editing (Photoshop, GIMP strip PNG text chunks)
    - Images can be shared without leaking workflow details
    """

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image"
    DESCRIPTION = "Save images as clean PNG (no embedded metadata). Workflow is stored in separate .meta/ JSON files."

    def save_images(self, images, filename_prefix="ComfyUI",
                    prompt=None, extra_pnginfo=None):
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = \
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir,
                images[0].shape[1], images[0].shape[0])

        results = []
        for batch_number, image in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            fname_batch = filename.replace("%batch_num%", str(batch_number))
            file = f"{fname_batch}_{counter:05}_.png"
            base_name = os.path.splitext(file)[0]

            # Save clean PNG — no PngInfo metadata embedded
            img.save(os.path.join(full_output_folder, file),
                     compress_level=self.compress_level)
            _save_sidecar_meta(full_output_folder, base_name, subfolder,
                               file, prompt, extra_pnginfo)

            results.append({"filename": file, "subfolder": subfolder,
                            "type": self.type})
            counter += 1

        return {"ui": {"images": results}}


# ── Node Registration ──

NODE_CLASS_MAPPINGS = {
    "SaveJPGSeparateMeta": SaveJPGSeparateMeta,
    "SaveWebPSeparateMeta": SaveWebPSeparateMeta,
    "SavePNGSeparateMeta": SavePNGSeparateMeta,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveJPGSeparateMeta": "Save JPG (Separate Meta)",
    "SaveWebPSeparateMeta": "Save WebP (Separate Meta)",
    "SavePNGSeparateMeta": "Save PNG (Separate Meta)",
}
