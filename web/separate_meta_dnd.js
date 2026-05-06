import { app } from "../../scripts/app.js";

/**
 * ComfyUI-Save-Separate-Metadata — Frontend Extension
 *
 * Features:
 *   1. D&D handler:  Drag JPG/WebP/PNG onto canvas → fetch .meta sidecar → restore workflow
 *   2. Drawer Bus:   Register as meta:read provider so Drawer Gallery can load workflows
 *   3. fs:moved:     Move .meta sidecar files when Drawer moves images
 *   4. fs:deleted:   Delete .meta sidecar files when Drawer deletes images
 *   5. Text replace: Apply %date:...% replacements for filename_prefix widgets
 *   6. Cleanup:      Settings button to delete orphaned .meta files
 */

const SEPMETA_NODES = ["SaveJPGSeparateMeta", "SaveWebPSeparateMeta", "SavePNGSeparateMeta"];
const API_PREFIX = "/sepmeta";

app.registerExtension({
    name: "SeparateMeta.DragDrop",

    // Apply filename_prefix text replacements for our save nodes
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!SEPMETA_NODES.includes(nodeData.name)) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origOnNodeCreated?.apply(this, arguments);

            const widget = this.widgets?.find(w => w.name === "filename_prefix");
            if (widget) {
                widget.serializeValue = () => {
                    const applyTextReplacements =
                        window.comfyAPI?.utils?.applyTextReplacements;
                    if (applyTextReplacements) {
                        return applyTextReplacements(app.graph, widget.value);
                    }
                    return widget.value;
                };
            }

            return r;
        };
    },

    async setup() {
        // ── 1. D&D Handler ──
        const origHandleFile = app.handleFile;
        app.handleFile = async function (file) {
            const name = file.name.toLowerCase();

            // Handle JPG/WebP/PNG: fetch sidecar metadata from backend
            if (name.endsWith(".jpg") || name.endsWith(".jpeg") ||
                name.endsWith(".webp") || name.endsWith(".png")) {
                try {
                    const resp = await fetch(
                        `${API_PREFIX}/get_meta?filename=${encodeURIComponent(file.name)}`
                    );
                    if (resp.status === 409) {
                        console.warn(`[SepMeta] Multiple metadata sidecars match ${file.name}; drag the .meta JSON directly or open it from Drawer for an exact match.`);
                        return;
                    }
                    if (resp.ok) {
                        const meta = await resp.json();
                        if (meta.workflow) {
                            const baseName = file.name.replace(/\.[^.]+$/, '');
                            await app.loadGraphData(meta.workflow, true, true, baseName);
                            console.log(`[SepMeta] Workflow restored from ${file.name}`);
                            return;
                        }
                    }
                } catch (err) {
                    console.warn("[SepMeta] Error fetching metadata:", err);
                }
            }

            // Handle JSON: detect metadata wrapper { prompt, workflow }
            if (name.endsWith(".json")) {
                try {
                    const text = await file.text();
                    const data = JSON.parse(text);
                    if (data.workflow && data.prompt && !data.nodes) {
                        const baseName = file.name.replace(/\.[^.]+$/, '');
                        await app.loadGraphData(data.workflow, true, true, baseName);
                        console.log(`[SepMeta] Workflow restored from metadata JSON: ${file.name}`);
                        return;
                    }
                } catch (err) {
                    console.warn("[SepMeta] Error parsing JSON:", err);
                }
            }

            return origHandleFile.call(this, file);
        };

        console.log("[SepMeta] D&D handler registered (JPG/WebP/PNG/JSON)");

        // ── 2. Drawer Bus Integration ──
        const registerMetaProvider = () => {
            const drawer = window.ComfyDrawer;
            if (!drawer || !drawer.bus) return false;

            // Respond to metadata read requests from Gallery
            drawer.bus.respond('meta:read', async ({ subfolder, name }) => {
                try {
                    const r = await fetch(
                        `${API_PREFIX}/get_meta?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(subfolder || '')}&exact=1`
                    );
                    if (r.ok) return r.json();
                } catch { /* not available */ }
                return null;
            });

            // Move .meta sidecars when images are moved
            drawer.bus.on('fs:moved', async ({ root, srcRoot, files }) => {
                if (!files?.length) return;
                try {
                    await fetch(`${API_PREFIX}/move_meta`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ root, srcRoot, files }),
                    });
                } catch { /* backend may not be available */ }
            });

            // Delete .meta sidecars when images are deleted
            drawer.bus.on('fs:deleted', async ({ root, files }) => {
                if (!files?.length) return;
                try {
                    await fetch(`${API_PREFIX}/delete_meta`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ root, files }),
                    });
                } catch { /* backend may not be available */ }
            });

            return true;
        };

        if (!registerMetaProvider()) {
            window.addEventListener('comfy-drawer:ready', () => {
                if (registerMetaProvider()) {
                    console.log("[SepMeta] meta:read provider registered on Drawer Bus");
                }
            }, { once: true });
        } else {
            console.log("[SepMeta] meta:read provider registered on Drawer Bus");
        }

        // ── 5. Cleanup Button in Settings ──
        app.ui.settings.addSetting({
            id: "SepMeta.CleanupMeta",
            name: "🧹 Separate Meta: Clean up orphaned metadata",
            type: () => {
                const btn = document.createElement("button");
                btn.textContent = "Clean up now";
                btn.style.cssText = "padding: 6px 16px; cursor: pointer; border-radius: 4px; border: 1px solid #666;";
                btn.onclick = async () => {
                    btn.disabled = true;
                    btn.textContent = "Cleaning...";
                    try {
                        const resp = await fetch(`${API_PREFIX}/cleanup_meta`, { method: "POST" });
                        const data = await resp.json();
                        btn.textContent = `Done! Deleted ${data.deleted} file(s)`;
                        setTimeout(() => { btn.textContent = "Clean up now"; btn.disabled = false; }, 3000);
                    } catch (err) {
                        btn.textContent = "Error: " + err.message;
                        setTimeout(() => { btn.textContent = "Clean up now"; btn.disabled = false; }, 3000);
                    }
                };
                return btn;
            },
        });
    },
});
