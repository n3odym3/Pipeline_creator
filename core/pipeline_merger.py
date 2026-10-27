"""
Pipeline Merger module for Pipeline Creator.

Provides intelligent, surgical editing, merging, and updating of pipeline layouts (.json)
from active workspace flows (nodes, connections, gates, views) without
breaking or overwriting existing views, automation scripts, or custom metadata.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    if not hasattr(logger, "success"):
        logger.success = logger.info

from core.paths import PIPELINES_DIR


def analyze_pipeline_diff(
    target_filepath: str,
    instances: Optional[Any] = None,
    node_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    link_nodes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Compare the active workspace state with an existing pipeline JSON file.

    Returns a comprehensive diff summary describing modules added/updated/removed,
    flow connections, link nodes/gates, views, and custom metadata fields.
    """
    path_obj = Path(target_filepath)
    if not path_obj.exists() or not path_obj.is_file():
        return {
            "exists": False,
            "is_valid_json": False,
            "target_filepath": target_filepath,
            "target_name": path_obj.name,
            "error": f"File '{target_filepath}' does not exist.",
        }

    try:
        with open(path_obj, "r", encoding="utf-8") as f:
            target_data = json.load(f)
    except Exception as e:
        return {
            "exists": True,
            "is_valid_json": False,
            "target_filepath": target_filepath,
            "target_name": path_obj.name,
            "error": f"Cannot parse JSON: {e}",
        }

    if not isinstance(target_data, dict):
        return {
            "exists": True,
            "is_valid_json": False,
            "target_filepath": target_filepath,
            "target_name": path_obj.name,
            "error": "Root JSON element is not an object.",
        }

    # 1. Resolve live workspace modules
    if instances is None:
        try:
            from core.module_registry import MODULES_REGISTRY
            live_modules = list(MODULES_REGISTRY.values())
        except Exception:
            live_modules = []
    elif isinstance(instances, dict):
        live_modules = list(instances.values())
    else:
        live_modules = list(instances)

    live_by_uuid: Dict[str, Any] = {}
    for win in live_modules:
        uid = str(getattr(win, "UUID", ""))
        if uid:
            live_by_uuid[uid] = win

    # 2. Existing modules in target JSON
    existing_windows = target_data.get("windows", [])
    existing_by_uuid: Dict[str, Dict[str, Any]] = {}
    for w in existing_windows:
        uid = str(w.get("uuid", ""))
        if uid:
            existing_by_uuid[uid] = w

    live_uuids = set(live_by_uuid.keys())
    existing_uuids = set(existing_by_uuid.keys())

    added_uuids = live_uuids - existing_uuids
    removed_uuids = existing_uuids - live_uuids
    updated_uuids = live_uuids.intersection(existing_uuids)

    added_modules = [
        {
            "uuid": uid,
            "label": getattr(live_by_uuid[uid], "label", uid),
            "class_name": getattr(live_by_uuid[uid], "__class__", type(live_by_uuid[uid])).__name__,
        }
        for uid in added_uuids
    ]
    removed_modules = [
        {
            "uuid": uid,
            "label": existing_by_uuid[uid].get("params", {}).get("label")
            or existing_by_uuid[uid].get("class_name")
            or uid,
            "class_name": existing_by_uuid[uid].get("class_name", ""),
        }
        for uid in removed_uuids
    ]
    updated_modules = [
        {
            "uuid": uid,
            "label": getattr(live_by_uuid[uid], "label", uid),
            "class_name": existing_by_uuid[uid].get("class_name", ""),
        }
        for uid in updated_uuids
    ]

    # 3. Connections
    live_conn_count = 0
    for win in live_modules:
        for out, targets in getattr(win, "connections", {}).items():
            live_conn_count += len(targets)

    existing_conn_count = len(target_data.get("connections", []))

    # 4. Link nodes (Gates, Link In/Out, Working Dir)
    if link_nodes is None:
        try:
            from core.main_win import main_win
            if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "serialize_link_nodes"):
                live_link_nodes = main_win.node_editor.serialize_link_nodes()
            else:
                live_link_nodes = []
        except Exception:
            live_link_nodes = []
    else:
        live_link_nodes = link_nodes

    existing_link_nodes = target_data.get("link_nodes", [])
    existing_link_by_uuid = {str(n.get("uuid")): n for n in existing_link_nodes if "uuid" in n}
    live_link_by_uuid = {str(n.get("uuid")): n for n in live_link_nodes if "uuid" in n}

    live_link_uuids = set(live_link_by_uuid.keys())
    existing_link_uuids = set(existing_link_by_uuid.keys())

    added_link_nodes = [live_link_by_uuid[u] for u in (live_link_uuids - existing_link_uuids)]
    removed_link_nodes = [existing_link_by_uuid[u] for u in (existing_link_uuids - live_link_uuids)]
    updated_link_nodes = [live_link_by_uuid[u] for u in (live_link_uuids.intersection(existing_link_uuids))]

    # 5. Views
    existing_views = list(target_data.get("views", {}).keys())
    try:
        from core.module_registry import get_available_views
        avail = get_available_views()
        available_new_views = [v for v in avail.keys() if v not in existing_views and v != "Default"]
    except Exception:
        available_new_views = []

    # 6. Custom and metadata keys preserved intact
    standard_keys = {"is_relative", "windows", "connections", "views", "link_nodes"}
    preserved_custom_keys = [k for k in target_data.keys() if k not in standard_keys]

    return {
        "exists": True,
        "is_valid_json": True,
        "target_filepath": target_filepath,
        "target_name": path_obj.name,
        "added_modules": added_modules,
        "removed_modules": removed_modules,
        "updated_modules": updated_modules,
        "live_conn_count": live_conn_count,
        "existing_conn_count": existing_conn_count,
        "added_link_nodes": added_link_nodes,
        "removed_link_nodes": removed_link_nodes,
        "updated_link_nodes": updated_link_nodes,
        "existing_views": existing_views,
        "available_new_views": available_new_views,
        "preserved_custom_keys": preserved_custom_keys,
        "error": None,
    }


def surgical_update_pipeline(
    target_filepath: str,
    instances: Optional[Any] = None,
    node_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    link_nodes: Optional[List[Dict[str, Any]]] = None,
    new_view_name: Optional[str] = None,
    new_view_data: Optional[Dict[str, Any]] = None,
    update_default_view: bool = True,
    create_backup: bool = True,
    is_relative: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Surgically update an existing pipeline JSON file.

    Merges node additions, removals, parameter updates, connections, link nodes,
    and optional new views into target_filepath while preserving:
      - All existing views and their window positions/sizes/gates.
      - Prunes deleted module/gate references from existing views so they stay valid.
      - All custom top-level fields (e.g. 'automation_script', 'metadata', etc.).
      - Preserves layout coordinates with relative/absolute consistency.
    """
    path_obj = Path(target_filepath)
    if not path_obj.exists():
        msg = f"Target pipeline file '{target_filepath}' does not exist."
        logger.error(msg)
        return {"success": False, "error": msg}

    try:
        with open(path_obj, "r", encoding="utf-8") as f:
            pipeline_data = json.load(f)
    except Exception as e:
        msg = f"Failed to read target pipeline '{target_filepath}': {e}"
        logger.error(msg)
        return {"success": False, "error": msg}

    if not isinstance(pipeline_data, dict):
        msg = f"Target file '{target_filepath}' is not a valid JSON dictionary."
        logger.error(msg)
        return {"success": False, "error": msg}

    # Create backup if requested
    backup_path = None
    if create_backup:
        try:
            backup_path = str(path_obj.with_suffix(path_obj.suffix + ".bak"))
            shutil.copy2(target_filepath, backup_path)
            logger.info(f"Created pipeline backup: {backup_path}")
        except Exception as e:
            logger.warning(f"Could not create backup for '{target_filepath}': {e}")

    # Determine coordinate mode
    if is_relative is None:
        is_rel = pipeline_data.get("is_relative", True)
    else:
        is_rel = is_relative
    pipeline_data["is_relative"] = is_rel

    # Try viewport dimensions
    viewport_width = None
    viewport_height = None
    try:
        import dearpygui.dearpygui as dpg
        viewport_width = dpg.get_viewport_client_width()
        viewport_height = dpg.get_viewport_client_height()
    except Exception:
        pass

    # 1. Resolve live instances
    if instances is None:
        try:
            from core.module_registry import MODULES_REGISTRY
            live_modules = list(MODULES_REGISTRY.values())
        except Exception:
            live_modules = []
    elif isinstance(instances, dict):
        live_modules = list(instances.values())
    else:
        live_modules = list(instances)

    live_by_uuid: Dict[str, Any] = {}
    for win in live_modules:
        uid = str(getattr(win, "UUID", ""))
        if uid:
            live_by_uuid[uid] = win

    # Resolve node positions
    if node_positions is None:
        try:
            from core.main_win import main_win
            if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "get_node_positions"):
                n_positions = main_win.node_editor.get_node_positions()
            else:
                n_positions = {}
        except Exception:
            n_positions = {}
    else:
        n_positions = node_positions

    # 2. Existing modules
    existing_windows = pipeline_data.get("windows", [])
    existing_uuids = {str(w.get("uuid")) for w in existing_windows if "uuid" in w}
    live_uuids = set(live_by_uuid.keys())

    removed_uuids = existing_uuids - live_uuids
    added_uuids = live_uuids - existing_uuids

    new_windows: List[Dict[str, Any]] = []

    # Update retained modules (preserve ordering and custom properties)
    for wdata in existing_windows:
        uid = str(wdata.get("uuid"))
        if uid in removed_uuids:
            continue  # Module was deleted

        live_win = live_by_uuid.get(uid)
        if live_win:
            # Update node_pos
            if uid in n_positions:
                wdata["node_pos"] = list(n_positions[uid])

            # Update visible state
            if hasattr(live_win, "visible"):
                wdata["visible"] = live_win.visible

            # Update live window geometry if available in DPG
            try:
                import dearpygui.dearpygui as dpg
                if hasattr(live_win, "winID") and dpg.does_item_exist(live_win.winID):
                    pos = dpg.get_item_pos(live_win.winID)
                    w = dpg.get_item_width(live_win.winID)
                    h = dpg.get_item_height(live_win.winID)
                    if is_rel and viewport_width and viewport_height:
                        wdata["pos"] = [(pos[0] / viewport_width) * 100.0, (pos[1] / viewport_height) * 100.0]
                        wdata["size"] = [
                            (w / viewport_width) * 100.0 if w != -1 else -1,
                            (h / viewport_height) * 100.0 if h != -1 else -1,
                        ]
                    else:
                        wdata["pos"] = list(pos)
                        wdata["size"] = [w, h]
            except Exception:
                pass

            # Update params (deep merge)
            if hasattr(live_win, "serialize"):
                try:
                    ser = live_win.serialize()
                    live_params = ser.get("params", {})
                    if "params" not in wdata:
                        wdata["params"] = {}
                    wdata["params"].update(live_params)
                except Exception:
                    pass

            # Update hidden items
            try:
                from core.module_item_inspector import ModuleItemInspector
                hidden = ModuleItemInspector.get_hidden_items(live_win)
                if hidden is not None:
                    wdata["hidden_items"] = hidden
            except Exception:
                pass

        new_windows.append(wdata)

    # Append newly added modules
    for uid in added_uuids:
        live_win = live_by_uuid[uid]
        if hasattr(live_win, "serialize"):
            w_dict = live_win.serialize()
        else:
            w_dict = {"uuid": uid, "class_name": type(live_win).__name__, "params": {}}

        if uid in n_positions:
            w_dict["node_pos"] = list(n_positions[uid])

        if getattr(live_win, "node_pinned", False):
            w_dict["pinned"] = True

        if is_rel and viewport_width and viewport_height:
            px, py = w_dict.get("pos", [0, 0])
            sx, sy = w_dict.get("size", [-1, -1])
            w_dict["pos"] = [(px / viewport_width) * 100.0, (py / viewport_height) * 100.0]
            w_dict["size"] = [
                (sx / viewport_width) * 100.0 if sx != -1 else -1,
                (sy / viewport_height) * 100.0 if sy != -1 else -1,
            ]

        new_windows.append(w_dict)

    pipeline_data["windows"] = new_windows

    # 3. Connections
    pipeline_data["connections"] = [
        {"from": str(win.UUID), "output": out, "to": str(tgt.UUID)}
        for win in live_modules
        for out, targets in getattr(win, "connections", {}).items()
        for tgt in targets
        if hasattr(win, "UUID") and hasattr(tgt, "UUID")
    ]

    # 4. Link nodes (Gates, Link In/Out, Working Dir)
    if link_nodes is None:
        try:
            from core.main_win import main_win
            if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "serialize_link_nodes"):
                live_link_nodes = main_win.node_editor.serialize_link_nodes()
            else:
                live_link_nodes = []
        except Exception:
            live_link_nodes = []
    else:
        live_link_nodes = link_nodes

    existing_link_nodes = pipeline_data.get("link_nodes", [])
    existing_gate_uuids = {
        str(n.get("uuid")) for n in existing_link_nodes if n.get("kind") == "gate" and "uuid" in n
    }
    live_gate_uuids = {
        str(n.get("uuid")) for n in live_link_nodes if n.get("kind") == "gate" and "uuid" in n
    }
    removed_gate_uuids = existing_gate_uuids - live_gate_uuids

    pipeline_data["link_nodes"] = live_link_nodes

    # 5. Views surgical update
    if "views" not in pipeline_data or not isinstance(pipeline_data["views"], dict):
        pipeline_data["views"] = {}

    views_dict = pipeline_data["views"]

    # Process existing views
    for vname, vdata in list(views_dict.items()):
        if not isinstance(vdata, dict):
            continue

        if vname == "Default" and update_default_view:
            # Synchronize Default view with updated windows
            vdata["windows"] = copy.deepcopy(new_windows)
            vdata["is_relative"] = is_rel
        else:
            # Custom view: PRUNE removed modules without touching positions of retained ones
            if "windows" in vdata and isinstance(vdata["windows"], list):
                vdata["windows"] = [
                    w for w in vdata["windows"] if str(w.get("uuid", "")) not in removed_uuids
                ]
                # Update module labels if renamed
                for w in vdata["windows"]:
                    w_uid = str(w.get("uuid", ""))
                    if w_uid in live_by_uuid:
                        lwin = live_by_uuid[w_uid]
                        if hasattr(lwin, "label"):
                            w["label"] = lwin.label

            # Gate states inside view: PRUNE deleted gates, update labels
            if "gates" in vdata and isinstance(vdata["gates"], dict):
                vdata["gates"] = {
                    gid: gcfg for gid, gcfg in vdata["gates"].items() if str(gid) not in removed_gate_uuids
                }
                live_gate_map = {
                    str(n.get("uuid")): n.get("label") for n in live_link_nodes if n.get("kind") == "gate"
                }
                for gid, gcfg in vdata["gates"].items():
                    if gid in live_gate_map and live_gate_map[gid]:
                        gcfg["label"] = live_gate_map[gid]

    # Add / capture new view if requested
    if new_view_name and new_view_name.strip():
        cleaned_view_name = new_view_name.strip()
        if new_view_data is not None:
            views_dict[cleaned_view_name] = copy.deepcopy(new_view_data)
        else:
            try:
                from core.module_registry import export_view
                views_dict[cleaned_view_name] = export_view(is_relative=is_rel)
            except Exception as e:
                logger.warning(f"Could not export new view '{cleaned_view_name}': {e}")

    # Merge any views active in session (e.g. from export_view)
    try:
        from core.module_registry import AVAILABLE_VIEWS
        for av_name, av_data in AVAILABLE_VIEWS.items():
            if av_name not in views_dict and av_name != "Default":
                views_dict[av_name] = copy.deepcopy(av_data)
    except Exception:
        pass

    # 6. Save back to disk
    try:
        with open(target_filepath, "w", encoding="utf-8") as f:
            json.dump(pipeline_data, f, indent=4)
    except Exception as e:
        msg = f"Failed to write updated pipeline to '{target_filepath}': {e}"
        logger.error(msg)
        return {"success": False, "error": msg}

    # 7. Sync in-memory global state
    try:
        import core.module_registry as mr
        mr.LAST_LOADED_WORKSPACE = str(Path(target_filepath).resolve())
        mr.register_views(views_dict, merge=False)
    except Exception as e:
        logger.debug(f"Could not sync global module registry state: {e}")

    # 8. Refresh View menus
    try:
        from core.main_win import main_win
        if hasattr(main_win, "refresh_view_menu"):
            main_win.refresh_view_menu()
        if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "refresh_view_menu"):
            main_win.node_editor.refresh_view_menu()
    except Exception:
        pass

    # 9. Sync Pipeline Editor if running
    try:
        from core.pipeline_editor import pipeline_editor
        if pipeline_editor and hasattr(pipeline_editor, "current_filepath"):
            if pipeline_editor.current_filepath == str(Path(target_filepath).resolve()):
                pipeline_editor.load_file(target_filepath)
    except Exception:
        pass

    summary = (
        f"Pipeline '{path_obj.name}' updated: "
        f"{len(added_uuids)} added, {len(removed_uuids)} removed, {len(new_windows)} total modules, "
        f"{len(pipeline_data['connections'])} connections, {len(live_link_nodes)} link nodes, "
        f"{len(views_dict)} views preserved."
    )
    logger.success(summary)

    return {
        "success": True,
        "summary": summary,
        "target_filepath": target_filepath,
        "backup_path": backup_path,
        "added_modules": list(added_uuids),
        "removed_modules": list(removed_uuids),
        "total_modules": len(new_windows),
        "connections_count": len(pipeline_data["connections"]),
        "link_nodes_count": len(live_link_nodes),
        "views_count": len(views_dict),
        "views": list(views_dict.keys()),
    }


def open_edit_pipeline_dialog(parent_editor: Optional[Any] = None) -> None:
    """
    Open the interactive 'Edit / Update Pipeline' modal dialog in DearPyGui.

    Allows the user to select the target pipeline, preview the surgical diff,
    optionally capture the current workspace layout as a new view, and apply the merge.
    """
    import dearpygui.dearpygui as dpg
    import core.module_registry as mr
    from config.display_scaling import display_scaling
    from core.file_explorer import file_explorer

    popup_id = "edit_pipeline_modal_dialog"
    if dpg.does_item_exist(popup_id):
        dpg.delete_item(popup_id)

    # 1. Discover pipeline files
    layout_files = (
        sorted([f for f in PIPELINES_DIR.glob("*.json") if f.is_file()], key=lambda p: p.stem.lower())
        if PIPELINES_DIR.exists()
        else []
    )
    file_map = {f.stem: str(f.resolve()) for f in layout_files}
    combo_items = list(file_map.keys())

    # Pre-select active loaded workspace if available
    default_file = ""
    if mr.LAST_LOADED_WORKSPACE and Path(mr.LAST_LOADED_WORKSPACE).exists():
        default_file = str(Path(mr.LAST_LOADED_WORKSPACE).resolve())
    elif layout_files:
        default_file = str(layout_files[0].resolve())

    initial_stem = Path(default_file).stem if default_file else (combo_items[0] if combo_items else "")

    s = display_scaling.scale
    win_w = s(640)
    win_h = s(540)

    path_input_tag = f"{popup_id}_path_input"
    combo_tag = f"{popup_id}_combo"
    diff_text_tag = f"{popup_id}_diff_text"
    views_info_tag = f"{popup_id}_views_info"
    metadata_info_tag = f"{popup_id}_metadata_info"
    capture_view_chk_tag = f"{popup_id}_capture_view_chk"
    new_view_name_tag = f"{popup_id}_new_view_name"
    backup_chk_tag = f"{popup_id}_backup_chk"
    default_view_chk_tag = f"{popup_id}_default_view_chk"
    status_text_tag = f"{popup_id}_status_text"

    def _update_diff_display(filepath: str) -> None:
        if not dpg.does_item_exist(diff_text_tag):
            return

        diff = analyze_pipeline_diff(filepath)
        if not diff.get("is_valid_json", False):
            dpg.set_value(diff_text_tag, f"Error: {diff.get('error', 'Invalid pipeline file')}")
            dpg.configure_item(diff_text_tag, color=(255, 120, 120))
            dpg.set_value(views_info_tag, "")
            dpg.set_value(metadata_info_tag, "")
            return

        added = diff["added_modules"]
        removed = diff["removed_modules"]
        updated = diff["updated_modules"]
        added_txt = f"+{len(added)} added ({', '.join(m['label'] for m in added)})" if added else "0 added"
        removed_txt = (
            f"-{len(removed)} removed ({', '.join(m['label'] for m in removed)})" if removed else "0 removed"
        )
        updated_txt = f"{len(updated)} updated"

        diff_summary = (
            f"Modules:   {added_txt}  |  {updated_txt}  |  {removed_txt}\n"
            f"Flow:      {diff['live_conn_count']} active connections (replaces {diff['existing_conn_count']} in file)\n"
            f"Link Nodes: +{len(diff['added_link_nodes'])} added, -{len(diff['removed_link_nodes'])} removed, "
            f"{len(diff['updated_link_nodes'])} preserved"
        )
        dpg.set_value(diff_text_tag, diff_summary)
        dpg.configure_item(diff_text_tag, color=(200, 240, 255))

        # Views
        existing_views = diff.get("existing_views", [])
        if existing_views:
            views_str = (
                f"Views to preserve ({len(existing_views)}): {', '.join(existing_views)}\n"
                f"* Note: Custom window positions in views remain intact; deleted modules/gates will be cleanly pruned."
            )
        else:
            views_str = "No existing views in target file."
        dpg.set_value(views_info_tag, views_str)

        # Custom metadata / automation scripts
        custom_keys = diff.get("preserved_custom_keys", [])
        if custom_keys:
            meta_str = f"Preserved custom keys / scripts: {', '.join(custom_keys)} (100% intact)"
            dpg.set_value(metadata_info_tag, meta_str)
            dpg.configure_item(metadata_info_tag, color=(255, 215, 120))
        else:
            dpg.set_value(metadata_info_tag, "No custom metadata or embedded script found in target.")
            dpg.configure_item(metadata_info_tag, color=(160, 160, 160))

    def _on_combo_changed(sender=None, app_data=None, user_data=None, *args, **kwargs):
        stem = app_data if app_data is not None else sender
        if stem in file_map:
            chosen_path = file_map[stem]
            dpg.set_value(path_input_tag, chosen_path)
            _update_diff_display(chosen_path)

    def _on_browse_file(sender=None, app_data=None, user_data=None, *args, **kwargs):
        chosen = file_explorer.select_file(
            default_path=str(PIPELINES_DIR), extensions=[("JSON files", "*.json")]
        )
        if chosen:
            dpg.set_value(path_input_tag, chosen)
            stem = Path(chosen).stem
            if stem in combo_items and dpg.does_item_exist(combo_tag):
                dpg.set_value(combo_tag, stem)
            _update_diff_display(chosen)

    def _on_apply(sender=None, app_data=None, user_data=None, *args, **kwargs):
        target_path = dpg.get_value(path_input_tag).strip()
        if not target_path:
            dpg.set_value(status_text_tag, "Error: Please select a target pipeline file.")
            dpg.configure_item(status_text_tag, color=(255, 100, 100))
            return

        if not Path(target_path).exists():
            dpg.set_value(status_text_tag, f"Error: File '{target_path}' does not exist.")
            dpg.configure_item(status_text_tag, color=(255, 100, 100))
            return

        capture_view = dpg.get_value(capture_view_chk_tag)
        new_vname = dpg.get_value(new_view_name_tag).strip() if capture_view else None
        create_bak = dpg.get_value(backup_chk_tag)
        update_def = dpg.get_value(default_view_chk_tag)

        res = surgical_update_pipeline(
            target_filepath=target_path,
            new_view_name=new_vname,
            create_backup=create_bak,
            update_default_view=update_def,
        )

        if res.get("success", False):
            logger.success(f"Pipeline successfully updated: {target_path}")
            if dpg.does_item_exist(popup_id):
                dpg.delete_item(popup_id)
        else:
            err = res.get("error", "Unknown error")
            dpg.set_value(status_text_tag, f"Update failed: {err}")
            dpg.configure_item(status_text_tag, color=(255, 100, 100))

    vp_w = dpg.get_viewport_width() or 1200
    vp_h = dpg.get_viewport_height() or 800
    pos_x = max(0, (vp_w - win_w) // 2)
    pos_y = max(0, (vp_h - win_h) // 2)

    with dpg.window(
        label="Edit / Update Pipeline (Surgical Flow Merge)",
        tag=popup_id,
        modal=True,
        show=True,
        width=win_w,
        height=win_h,
        pos=(pos_x, pos_y),
    ):
        dpg.add_text("Surgically merge active node graph into an existing pipeline layout.", color=(240, 240, 240))
        dpg.add_text("Views, automation scripts, and custom metadata are protected and preserved.", color=(120, 230, 140))
        dpg.add_separator()

        # Pipeline selector
        dpg.add_text("Target Pipeline:")
        with dpg.group(horizontal=True):
            if combo_items:
                dpg.add_combo(
                    items=combo_items,
                    default_value=initial_stem,
                    tag=combo_tag,
                    callback=_on_combo_changed,
                    width=s(200),
                )
            dpg.add_input_text(
                tag=path_input_tag,
                default_value=default_file,
                width=-s(100),
                callback=lambda s, a: _update_diff_display(a),
            )
            dpg.add_button(label="Browse...", callback=_on_browse_file, width=s(85))

        dpg.add_spacer(height=s(6))

        # Diff and Analysis section
        with dpg.collapsing_header(label="Surgical Diff & Flow Preview", default_open=True):
            with dpg.child_window(height=s(180), border=True):
                dpg.add_text("Active Flow Changes:", color=(180, 200, 255))
                dpg.add_text("", tag=diff_text_tag)
                dpg.add_separator()
                dpg.add_text("View Layout Preservation:", color=(180, 255, 200))
                dpg.add_text("", tag=views_info_tag, wrap=s(580))
                dpg.add_separator()
                dpg.add_text("Custom Metadata & Automation Scripts:", color=(255, 220, 150))
                dpg.add_text("", tag=metadata_info_tag, wrap=s(580))

        dpg.add_spacer(height=s(6))

        # Options
        with dpg.collapsing_header(label="Options & Views", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Capture current layout as new View in pipeline:",
                    tag=capture_view_chk_tag,
                    default_value=False,
                )
                dpg.add_input_text(tag=new_view_name_tag, hint="e.g. Analysis Mode", width=s(200))

            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Create backup file (.bak) before writing",
                    tag=backup_chk_tag,
                    default_value=True,
                )
                dpg.add_checkbox(
                    label="Update 'Default' view with new modules",
                    tag=default_view_chk_tag,
                    default_value=True,
                )

        dpg.add_text("", tag=status_text_tag)
        dpg.add_separator()

        # Action buttons
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Apply Changes to Pipeline",
                callback=_on_apply,
                width=s(230),
            )
            dpg.add_button(
                label="Cancel",
                callback=lambda: dpg.delete_item(popup_id) if dpg.does_item_exist(popup_id) else None,
                width=s(100),
            )

        # Trigger initial diff calculation
        if default_file:
            _update_diff_display(default_file)
