# ComfyUI Save Separate Metadata

https://github.com/user-attachments/assets/a3fc8205-fa33-42da-bb3f-99dfdd886967

Save images from ComfyUI as clean JPG, WebP, or PNG files while keeping the
workflow metadata in separate sidecar JSON files.

The image stays an ordinary image. The workflow lives next to it.

## Concept

ComfyUI normally embeds workflow data inside PNG files. That is convenient, but
it also means the image file carries hidden workflow data with it.

This custom node uses a sidecar layout instead:

```text
output/
└── 2026-05/
    ├── image_00001_.webp
    ├── image_00002_.jpg
    ├── image_00003_.png
    └── .meta/
        ├── image_00001_.json
        ├── image_00002_.json
        └── image_00003_.json
```

Each `.meta/*.json` file contains the ComfyUI `prompt` and `workflow` data for
the image with the same stem.

## Nodes

| Node | Output format | Options |
|---|---|---|
| `Save JPG (Separate Meta)` | `.jpg` | Quality, default `95` |
| `Save WebP (Separate Meta)` | `.webp` | Quality, default `90` |
| `Save PNG (Separate Meta)` | `.png` | Clean PNG, no embedded workflow |

All nodes support the usual ComfyUI `filename_prefix` behavior, including date
variables, width/height variables, and subfolders.

## Why Use This?

### Advantages

- Save workflow metadata for JPG and WebP outputs.
- Keep PNG files free of embedded workflow chunks.
- Share an image without automatically sharing the workflow.
- Keep workflow metadata even if an image editor strips embedded PNG text chunks.
- Store metadata in a readable JSON file that can be archived, inspected, or
  versioned separately.

### Tradeoffs

- The image and its `.meta/*.json` sidecar need to stay together if you want
  workflow restore to keep working.
- Moving files outside ComfyUI or Drawer may leave sidecars behind.
- Some tools hide dot-folders such as `.meta`.
- External websites and image viewers will not know about the sidecar metadata.

## Restore Workflows

You can restore a workflow in two ways:

- Drag a saved JPG, WebP, or PNG onto the ComfyUI canvas.
- Drag the matching `.meta/*.json` file onto the ComfyUI canvas.

Sidecars are matched by filename stem, including spaces. For example,
`embedded metadata.jpg` matches `.meta/embedded metadata.json`.

If both a sidecar and embedded workflow metadata exist for an image, this
extension uses the sidecar first. If no sidecar is found, ComfyUI's normal file
handling can still run.

For image drag and drop, sidecar lookup is limited to ComfyUI's `output`
directory. For exact restores outside that lookup scope, drag the
`.meta/*.json` file directly. Drawer Gallery integration uses the selected
gallery item's folder, so it can resolve the exact sidecar for that item.

When an image is dragged in from outside ComfyUI, the browser only provides the
filename, not the original folder. If more than one sidecar with the same
filename exists under `output`, this extension refuses to guess. In that case,
drag the exact `.meta/*.json` file or use Drawer Gallery.

## Drawer Integration

This extension works without ComfyUI-Drawer. The save nodes and basic drag and
drop restore do not require Drawer.

If [ComfyUI-Drawer](https://github.com/Kuroi961/ComfyUI-Drawer) is installed,
the sidecars become easier to manage:

- Drawer Gallery can read sidecar metadata for workflow restore and metadata
  viewing.
- Drawer Gallery search indexes sidecar metadata through Drawer's Python raw
  metadata provider API. Rebuild the Gallery search index, or run Drawer's
  metadata refresh, after installing or updating this extension so existing
  sidecars become searchable.
- Gallery move, rename, conflict-rename, delete, and root-crossing move events
  keep `.meta/*.json` files in sync.

Drawer is optional. If it is not installed, Drawer-specific integration simply
does nothing.

## Scope And Notes

- `LoadImage` context-menu actions are not sidecar-copy operations. They upload
  an image into ComfyUI's `input` folder as an input asset, and do not copy the
  workflow sidecar.
- File operations performed outside ComfyUI/Drawer are not monitored.
- Sidecars are matched by filename stem. In one folder, do not keep multiple
  images with the same stem and different extensions, such as `image.jpg` and
  `image.png`, unless you intentionally want them to share one sidecar.
- The cleanup setting can delete orphaned `.meta/*.json` files if external file
  operations leave metadata behind.
- If ComfyUI is started with metadata disabled, sidecar metadata is not written.
- Sidecar JSON is sanitized so `NaN` and `Infinity` are written as `null`, which
  keeps the JSON valid for browser parsing.

## Installation

### ComfyUI Manager

After publication, install from ComfyUI Manager by searching for:

```text
Save Separate Metadata
```

### Manual

Clone this repository into `ComfyUI/custom_nodes`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Kuroi961/ComfyUI-Save-Separate-Metadata.git
```

Restart ComfyUI after installation.

No extra pip dependencies are required.

## Developer API

These endpoints are provided for integration with other extensions:

| Endpoint | Method | Purpose |
|---|---|---|
| `/sepmeta/get_meta` | `GET` | Read sidecar metadata for an image. Supports `filename`, `subfolder`, and `exact=1`. |
| `/sepmeta/move_meta` | `POST` | Move or rename sidecars after a file move event. Supports `root`, `srcRoot`, and per-file source/destination fields. |
| `/sepmeta/delete_meta` | `POST` | Delete sidecars after file delete events. |
| `/sepmeta/cleanup_meta` | `POST` | Delete orphaned sidecars that no longer have matching image files. |

When ComfyUI-Drawer is installed, this extension also registers a Python
raw metadata provider named `save-separate-metadata`. Drawer reads standard
ComfyUI `prompt` and `workflow` data from that provider for Gallery workflow
loading and SQLite metadata search indexing.

The sidecar convention is:

```text
<image folder>/.meta/<image stem>.json
```

For example:

```text
output/2026-05/example_00001_.webp
output/2026-05/.meta/example_00001_.json
```

## License

MIT
