from __future__ import annotations

from typing import Any, Dict, List, Tuple

import dearpygui.dearpygui as dpg

from core.module_registry import get_available_modules
from core.search_utils import fuzzy_score


class NodePopupMixin:
    """
    Mixin that handles the module-search popup list: building the registry,
    populating the UI list, and filtering items on search input.
    Assumes NodeEditor.__init__ has set self.popup_tag and self.grouped_modules.
    """

    popup_tag: str | int
    grouped_modules: Dict[str, Any]
    all_modules: List[Tuple[str, str, Any]]
    _popup_buttons: List[Dict[str, Any]]
    _popup_headers: List[str | int]

    def _build_module_registry(self) -> None:
        """Build the grouped module registry for the search popup."""

        def new_node() -> Dict[str, Any]:
            return {"__classes__": [], "subfolders": {}}

        self.grouped_modules = new_node()
        self.all_modules = []

        for name, cls in get_available_modules().items():
            if name.startswith("modules."):
                name = name[len("modules.") :]

            parts = name.split(".")
            display_name = parts[-1]
            folders = parts[:-1]

            curr = self.grouped_modules
            for folder in folders:
                if folder not in curr["subfolders"]:
                    curr["subfolders"][folder] = new_node()
                curr = curr["subfolders"][folder]

            curr["__classes__"].append((display_name, cls))

            folder_path = "/".join(folders) if folders else "other"
            self.all_modules.append((folder_path, display_name, cls))

    def _populate_module_list(self) -> None:
        """Create the module list items once (called at init only)."""
        list_tag = f"{self.popup_tag}_list"
        self._popup_buttons = []
        self._popup_headers = []

        def build_ui(
            node: Dict[str, Any],
            parent_tag: str | int,
            parent_headers: List[str | int],
            path_names: List[str],
        ) -> None:
            for folder_name, subnode in sorted(node["subfolders"].items()):
                with dpg.collapsing_header(label=folder_name, default_open=False, parent=parent_tag) as header:
                    self._popup_headers.append(header)
                    grp = dpg.add_group(indent=15, parent=header)
                    build_ui(subnode, grp, parent_headers + [header], path_names + [folder_name])

            for module_name, cls in sorted(node["__classes__"], key=lambda x: x[0]):
                btn = dpg.add_button(
                    label=module_name,
                    callback=getattr(self, "add_node", None),
                    user_data=cls,
                    width=-1,
                    parent=parent_tag,
                )
                self._popup_buttons.append(
                    {
                        "id": btn,
                        "name_lower": module_name.lower(),
                        "path_lower": " ".join(path_names + [module_name]).lower(),
                        "headers": parent_headers,
                    }
                )

                description = getattr(cls, "description", "") or getattr(cls, "DESCRIPTION", "")
                if not description:
                    description = cls.__doc__ or ""
                if description.strip():
                    with dpg.tooltip(parent=btn):
                        dpg.add_text(description.strip(), wrap=300)

        build_ui(self.grouped_modules, list_tag, [], [])

    def _filter_module_list(self, filter_text: str = "") -> None:
        """Show/hide module list items based on search text (no recreation)."""
        filter_lower = filter_text.lower().strip()

        builtin_header = f"{self.popup_tag}_builtin_header"
        btn_lo = f"{self.popup_tag}_btn_link_out"
        btn_li = f"{self.popup_tag}_btn_link_in"

        if filter_lower:
            lo_score = fuzzy_score(filter_lower, "link out")
            li_score = fuzzy_score(filter_lower, "link in")
            bi_score = fuzzy_score(filter_lower, "built-in")

            if lo_score > 0 or bi_score > 50:
                dpg.show_item(btn_lo)
            else:
                dpg.hide_item(btn_lo)

            if li_score > 0 or bi_score > 50:
                dpg.show_item(btn_li)
            else:
                dpg.hide_item(btn_li)

            if lo_score > 0 or li_score > 0 or bi_score > 50:
                dpg.show_item(builtin_header)
                dpg.set_value(builtin_header, True)
            else:
                dpg.hide_item(builtin_header)
        else:
            dpg.show_item(builtin_header)
            dpg.show_item(btn_lo)
            dpg.show_item(btn_li)
            dpg.set_value(builtin_header, False)

        for h in self._popup_headers:
            if filter_lower:
                dpg.hide_item(h)
                dpg.set_value(h, True)
            else:
                dpg.show_item(h)
                dpg.set_value(h, False)

        for item in self._popup_buttons:
            btn_id = item["id"]
            if not filter_lower:
                dpg.show_item(btn_id)
            else:
                score = fuzzy_score(filter_lower, item["name_lower"])
                path_score = fuzzy_score(filter_lower, item["path_lower"])
                if score > 0 or path_score > 50:
                    dpg.show_item(btn_id)
                    for h in item["headers"]:
                        dpg.show_item(h)
                else:
                    dpg.hide_item(btn_id)

    def _on_search_change(self, sender: Any, app_data: str, user_data: Any = None, *args: Any) -> None:
        """Handle search input changes."""
        self._filter_module_list(app_data)

