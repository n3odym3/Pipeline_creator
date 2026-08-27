import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes
from core.file_explorer import file_explorer
from loguru import logger
import csv
import uuid
import os

class Sample_container_win(WindowBase):
	def __init__(self,
				label="Sample Container",
				win_width=300,
				win_height=200,
				pos=(10, 10),
				uuid=None,
				outputs=None,
				visible=True,
				export_path="",
				duplicate="override"): 

		self._persistent_fields = ["label", "export_path", "duplicate"]
		self._duplicate = duplicate if duplicate in ("override", "append") else "override"
		super().__init__(label=label,pos=pos,win_width=win_width,win_height=win_height,uuid=uuid,outputs=outputs,visible=visible, export_path=export_path, duplicate=self._duplicate)

		self.export_path = export_path
		self.accepted_input_types = [IOTypes.SAMPLE, IOTypes.FOLDER_PATH, IOTypes.TRIGGER, IOTypes.CMD_DICT]  
		self.outputs = {
			"Data": IOTypes.SAMPLE,  
		}
		self.connections = {k: [] for k in self.outputs}
		
		self.clear_samples_tag = f"clear_samples_{self.UUID}"
		self.samples_group_tag = f"samples_group_{self.UUID}"

		self.samples_dict = {}  # Store samples by UUID
		self.samples_order = []  # Ordered list of sample UUIDs for display
		self._handle_to_uuid = {}  # For drop resolution

		# Export UI tags
		self.export_header_tag = f"export_header_{self.UUID}"
		self.x_axis_combo_tag = f"x_axis_combo_{self.UUID}"
		self.import_btn_tag = f"import_btn_{self.UUID}"
		self.export_btn_tag = f"export_btn_{self.UUID}"
		self.duplicate_mode_tag = f"duplicate_mode_{self.UUID}"
		self._combo_uuid_map = {"index": "index", "individual": "individual"}
		self._last_combo_items = ["index", "individual"]

		# Handle theme (grey → green on hover)
		self._handle_theme = dpg.add_theme()
		with dpg.theme_component(dpg.mvButton, parent=self._handle_theme):
			dpg.add_theme_color(dpg.mvThemeCol_Button,        (60,  60,  60,  180), category=dpg.mvThemeCat_Core)
			dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 180,  80,  220), category=dpg.mvThemeCat_Core)
			dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (40, 150,  60,  255), category=dpg.mvThemeCat_Core)
	
		with dpg.window(label=self.label,
						width=self.win_width,
						height=self.win_height,
						pos=self.pos,
						tag=self.winID,
						show=self.visible):

			# Import/Export section
			with dpg.collapsing_header(label="Import/export", tag=self.export_header_tag):
				dpg.add_text("X axis reference:")
				dpg.add_combo(items=["index", "individual"], default_value="index", tag=self.x_axis_combo_tag, width=-1)

				dpg.add_button(label="Import CSV", tag=self.import_btn_tag, callback=self._import_csv_cb, width=-1)

				dpg.add_button(label="Export CSV", tag=self.export_btn_tag, callback=self._export_csv_cb, width=-1)
	
				dpg.add_text("Duplicate:")
				dpg.add_combo(items=["override", "append"], default_value=self._duplicate, tag=self.duplicate_mode_tag, callback=self._on_duplicate_cb, width=-1)
				with dpg.tooltip(self.duplicate_mode_tag):
					dpg.add_text("Action when a sample has the same name:\n- 'override': replaces existing data\n- 'append': adds a new sample with a suffix")

			dpg.add_button(label="Clear samples", width=-1, tag=self.clear_samples_tag, callback=self.clear_samples_cb)

			self.select_all_btn_tag = f"select_all_btn_{self.UUID}"
			dpg.add_button(label="Select all", width=-1, callback=self.select_all_samples_cb, tag=self.select_all_btn_tag)

			self.deselect_all_btn_tag = f"deselect_all_btn_{self.UUID}"
			dpg.add_button(label="Deselect all", width=-1, callback=self.deselect_all_samples_cb, tag=self.deselect_all_btn_tag)

			dpg.add_group(tag=self.samples_group_tag)

	@property
	def duplicate(self) -> str:
		if hasattr(self, "duplicate_mode_tag") and dpg.does_item_exist(self.duplicate_mode_tag):
			return dpg.get_value(self.duplicate_mode_tag)
		return getattr(self, "_duplicate", "override")

	@duplicate.setter
	def duplicate(self, value: str) -> None:
		self._duplicate = value if value in ("override", "append") else "override"
		if hasattr(self, "duplicate_mode_tag") and dpg.does_item_exist(self.duplicate_mode_tag):
			dpg.set_value(self.duplicate_mode_tag, self._duplicate)

	def _on_duplicate_cb(self, sender, app_data, user_data=None):
		self._duplicate = app_data
	
	def _get_base_dir(self):
		"""Retrieve base directory from self.export_path."""
		if self.export_path:
			if os.path.isdir(self.export_path):
				return self.export_path
			else:
				try:
					return os.path.dirname(self.export_path)
				except Exception:
					pass
		return ""

	def add_or_update_sample(self, name, x_data, y_data, selected=False):
		"""Adds a sample or updates it if it exists under the same name."""
		# Determine duplicate mode
		mode = self.duplicate

		existing_uuid = None
		for k, v in self.samples_dict.items():
			if v["name"] == name:
				existing_uuid = k
				break

		if existing_uuid and mode == "override":
			# Update existing sample
			self.samples_dict[existing_uuid]["x"] = x_data
			self.samples_dict[existing_uuid]["y"] = y_data

			# Update the mini plot
			plot_tag = f"sample_plot_{existing_uuid}"
			if dpg.does_item_exist(plot_tag):
				if y_data is not None:
					dpg.set_value(plot_tag, y_data)

			# If the sample is currently selected, send the updated data
			checkbox_tag = f"sample_checkbox_{existing_uuid}"
			if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
				self._send_sample(self.samples_dict[existing_uuid], action="select")
			self._update_x_axis_combo()
			return existing_uuid

		# Generate new UUID for this container entry
		new_uuid = str(uuid.uuid4())

		# Resolve unique name if append mode and duplicate exists
		resolved_name = name
		if mode == "append" and existing_uuid:
			base = name
			counter = 1
			existing_names = {s["name"] for s in self.samples_dict.values()}
			while f"{base}_{counter}" in existing_names:
				counter += 1
			resolved_name = f"{base}_{counter}"

		# Store sample with new UUID
		self.samples_dict[new_uuid] = {
			"x": x_data,
			"y": y_data,
			"name": resolved_name,
			"uuid": new_uuid
		}

		self.samples_order.append(new_uuid)
		self._append_sample_row(new_uuid, selected=selected)
		self._update_x_axis_combo()
		if selected:
			self._send_sample(self.samples_dict[new_uuid], action="select")
		return new_uuid

	def input_cb(self, *args, **kwargs):
		"""Accept inputs and route them based on input types (SAMPLE, FOLDER_PATH, TRIGGER, or CMD_DICT)."""
		data_type = kwargs.get("data_type")
		data = kwargs.get("data") or kwargs.get("folder_path") or kwargs.get("path") or (args[0] if args else None)

		# 0. Check for CMD_DICT
		cmd = None
		if data_type == IOTypes.CMD_DICT:
			cmd = data
		elif isinstance(data, dict) and "y" not in data:
			cmd = data

		if cmd and isinstance(cmd, dict):
			if cmd.get("export"):
				self._auto_save_csv()
			return

		# 1. Check for FOLDER_PATH
		if data_type == IOTypes.FOLDER_PATH or (isinstance(data, str) and os.path.isdir(data)):
			self.export_path = data
			logger.info(f"[{self.label}] Export/import folder path configured to: {data}")
			return

		# 2. Check for TRIGGER (specifically trigger "save" or generic save trigger)
		if data_type == IOTypes.TRIGGER or (isinstance(data, str) and data.lower() == "save"):
			if isinstance(data, str) and data.lower() != "save":
				# Only trigger if it is "save", or if data is not string
				pass
			else:
				self._auto_save_csv()
				return

		# 3. Check for SAMPLE (the original input_cb logic)
		sample = kwargs.get("sample") or (data if isinstance(data, dict) and "y" in data else None)
		if not sample:
			return
		
		# Get name from input sample or generate default
		input_name = sample.get("name", "Sample")
		action = sample.get("action", "select")

		existing_uuid = None
		for k, v in self.samples_dict.items():
			if v["name"] == input_name:
				existing_uuid = k
				break

		if action in ["unselect", "delete"]:
			if existing_uuid:
				self.delete_sample_cb(None, None, existing_uuid)
			return

		self.add_or_update_sample(input_name, sample.get("x"), sample.get("y"), selected=True)

	def _append_sample_row(self, sample_uuid, selected=True):
		"""Add a single sample row widget at the bottom of the list."""
		group_tag = f"sample_group_{sample_uuid}"
		if dpg.does_item_exist(group_tag):
			return

		sample = self.samples_dict.get(sample_uuid)
		if not sample:
			return
		input_name = sample["name"]

		dpg.add_group(horizontal=True, tag=group_tag, parent=self.samples_group_tag)

		# Drag handle — drag SOURCE + drop TARGET
		handle_tag = f"sample_handle_{sample_uuid}"
		dpg.add_button(
			label="\uf0c9",
			tag=handle_tag,
			parent=group_tag,
			drop_callback=self._on_drop_cb,
			payload_type="SAMPLE_REORDER",
			user_data=sample_uuid,
		)
		dpg.add_drag_payload(
			parent=handle_tag,
			payload_type="SAMPLE_REORDER",
			drag_data=sample_uuid,
		)
		dpg.bind_item_theme(handle_tag, self._handle_theme)
		self._handle_to_uuid[handle_tag] = sample_uuid

		checkbox_tag = f"sample_checkbox_{sample_uuid}"
		dpg.add_checkbox(
			tag=checkbox_tag,
			default_value=selected,
			callback=self.sample_checkbox_cb,
			user_data=sample_uuid,
			parent=group_tag,
		)

		# Add tooltip with mini plot on checkbox
		with dpg.tooltip(checkbox_tag):
			y_data = sample.get("y")
			if y_data is None:
				y_data = []
			dpg.add_simple_plot(
				tag=f"sample_plot_{sample_uuid}",
				default_value=y_data,
				height=100,
				width=200
			)

		# Attach popup to checkbox explicitly
		with dpg.popup(checkbox_tag, mousebutton=dpg.mvMouseButton_Right):
			dpg.add_menu_item(label="Delete", user_data=sample_uuid, callback=self.delete_sample_cb)

		# Trash icon next to checkbox
		dpg.add_button(label="\uf2ed", callback=self.delete_sample_cb, user_data=sample_uuid, parent=group_tag)

		dpg.add_input_text(
			tag=f"sample_name_{sample_uuid}",
			default_value=input_name,
			callback=self.sample_name_cb,
			user_data=sample_uuid,
			parent=group_tag,
			width=-1,
		)
		with dpg.popup(dpg.last_item(), mousebutton=dpg.mvMouseButton_Right):
			dpg.add_menu_item(label="Delete", user_data=sample_uuid, callback=self.delete_sample_cb)

	def _rebuild_ui(self):
		"""Rebuild the entire sample list UI in current order."""
		self._handle_to_uuid.clear()
		state = {}
		for uid in list(self.samples_order):
			cb_tag = f"sample_checkbox_{uid}"
			name_tag = f"sample_name_{uid}"
			state[uid] = {
				"selected": dpg.get_value(cb_tag) if dpg.does_item_exist(cb_tag) else True,
				"name": dpg.get_value(name_tag) if dpg.does_item_exist(name_tag) else self.samples_dict[uid].get("name", ""),
			}
			group_tag = f"sample_group_{uid}"
			if dpg.does_item_exist(group_tag):
				dpg.delete_item(group_tag)

		for uid in list(self.samples_order):
			if uid not in self.samples_dict:
				continue
			s = state.get(uid, {})
			name = s.get("name", self.samples_dict[uid].get("name", ""))
			selected = s.get("selected", True)
			self.samples_dict[uid]["name"] = name
			self._append_sample_row(uid, selected=selected)
		self._update_x_axis_combo()

	def _on_drop_cb(self, sender, app_data, user_data):
		"""Handle drag and drop reordering: insert source before target."""
		# Resolve target UUID via dict
		target_uuid = self._handle_to_uuid.get(str(sender))
		if target_uuid is None:
			logger.warning(f"DROP: unknown handle sender={sender!r}")
			return

		source_uuid = app_data
		if source_uuid == target_uuid:
			return

		if source_uuid not in self.samples_order or target_uuid not in self.samples_order:
			logger.warning(f"DROP: UUID not in samples_order. source={source_uuid!r} target={target_uuid!r}")
			return

		# Insert source before target
		self.samples_order.remove(source_uuid)
		target_idx = self.samples_order.index(target_uuid)
		self.samples_order.insert(target_idx, source_uuid)

		self._rebuild_ui()

	def _update_x_axis_combo(self):
		"""Update the X axis dropdown with current sample names."""
		if not dpg.does_item_exist(self.x_axis_combo_tag):
			return
		items = ["index", "individual"]
		self._combo_uuid_map = {"index": "index", "individual": "individual"}
		for uid in self.samples_order:
			s = self.samples_dict.get(uid)
			if not s:
				continue
			# Use ##uuid to guarantee unique internal IDs even with duplicate names
			item_label = f"{s['name']}##xaxis_{s['uuid']}"
			items.append(item_label)
			self._combo_uuid_map[item_label] = s["name"]
		if items == self._last_combo_items:
			return
		self._last_combo_items = items
		current_val = dpg.get_value(self.x_axis_combo_tag)
		dpg.configure_item(self.x_axis_combo_tag, items=items)
		if current_val in items:
			dpg.set_value(self.x_axis_combo_tag, current_val)
		else:
			dpg.set_value(self.x_axis_combo_tag, "index")

	def _get_selected_samples(self):
		"""Return a list of samples whose checkbox is currently checked."""
		selected = []
		for sample_uuid, sample in self.samples_dict.items():
			checkbox_tag = f"sample_checkbox_{sample_uuid}"
			if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
				selected.append(sample)
		return selected

	def _write_csv(self, file_path):
		"""Writes the CSV file representing selected samples."""
		selected_samples = self._get_selected_samples()
		if not selected_samples:
			logger.warning("Export aborted: no samples selected.")
			return False

		x_ref_label = dpg.get_value(self.x_axis_combo_tag) if dpg.does_item_exist(self.x_axis_combo_tag) else "index"
		x_ref = self._combo_uuid_map.get(x_ref_label, x_ref_label)

		# Ensure parent directory exists
		parent_dir = os.path.dirname(file_path)
		if parent_dir:
			os.makedirs(parent_dir, exist_ok=True)

		if x_ref == "individual":
			headers = []
			for s in selected_samples:
				headers.extend([f"{s['name']}_X", f"{s['name']}_Y"])

			max_len = 0
			for s in selected_samples:
				x_raw = s.get("x")
				y_raw = s.get("y")
				len_x = len(x_raw) if x_raw is not None else 0
				len_y = len(y_raw) if y_raw is not None else 0
				max_len = max(max_len, len_x, len_y)

			with open(file_path, "w", newline="", encoding="utf-8") as f:
				writer = csv.writer(f)
				writer.writerow(headers)
				for i in range(max_len):
					row = []
					for s in selected_samples:
						x_raw = s.get("x")
						y_raw = s.get("y")
						
						x_list = list(x_raw) if x_raw is not None else []
						y_list = list(y_raw) if y_raw is not None else []
						
						x_val = x_list[i] if i < len(x_list) else ""
						y_val = y_list[i] if i < len(y_list) else ""
						row.extend([x_val, y_val])
					writer.writerow(row)
		else:
			# Determine X values
			x_values = None
			x_header = "Index"
			if x_ref != "index":
				for s in self.samples_dict.values():
					if s["name"] == x_ref:
						x_raw = s.get("x")
						if x_raw is not None:
							try:
								x_values = list(x_raw)
							except Exception:
								x_values = [x_raw]
						x_header = s["name"]
						break

			# Compute max row length from selected Y data
			max_len = 0
			for s in selected_samples:
				y_raw = s.get("y")
				if y_raw is not None:
					try:
						max_len = max(max_len, len(y_raw))
					except Exception:
						max_len = max(max_len, 1)

			if x_values is None:
				x_values = list(range(max_len))

			# Pad or trim x_values to match max_len
			if len(x_values) < max_len:
				last_val = x_values[-1] if x_values else 0
				x_values = x_values + [last_val] * (max_len - len(x_values))
			elif len(x_values) > max_len:
				x_values = x_values[:max_len]

			headers = [x_header] + [s["name"] for s in selected_samples]

			with open(file_path, "w", newline="", encoding="utf-8") as f:
				writer = csv.writer(f)
				writer.writerow(headers)
				for i in range(max_len):
					row = [x_values[i] if i < len(x_values) else ""]
					for s in selected_samples:
						y_raw = s.get("y")
						if y_raw is not None:
							try:
								y_list = list(y_raw)
								row.append(y_list[i] if i < len(y_list) else "")
							except Exception:
								row.append(y_raw if i == 0 else "")
						else:
							row.append("")
					writer.writerow(row)
		return True

	def _export_csv_cb(self, sender, app_data, user_data=None):
		"""Open file dialog and export selected samples to CSV."""
		selected_samples = self._get_selected_samples()
		if not selected_samples:
			logger.warning("Export aborted: no samples selected.")
			return

		def save_callback(file_path, selected_ext):
			if not file_path:
				return
			if not file_path.endswith(".csv"):
				file_path += ".csv"

			# Persist export path
			self.export_path = file_path

			try:
				if self._write_csv(file_path):
					logger.success(f"Sample Container exported to {file_path}")
			except Exception as e:
				logger.error(f"Failed to export CSV: {e}")

		# Get base directory for file dialog
		default_path = self._get_base_dir()
		default_name = "samples_export.csv"
		if self.export_path and not os.path.isdir(self.export_path):
			default_name = os.path.basename(self.export_path)

		file_explorer.save_file(
			default_path=default_path,
			default_name=default_name,
			extensions=[("CSV files", "*.csv")],
			callback=save_callback
		)

	def _auto_save_csv(self):
		"""Automatically export selected samples to the preconfigured folder/path without dialog."""
		if not self.export_path:
			logger.warning("Auto-save aborted: no import/export folder/path configured.")
			return

		# Determine the final file path
		if os.path.isdir(self.export_path):
			file_path = os.path.join(self.export_path, "samples_export.csv")
		else:
			file_path = self.export_path
			if not file_path.endswith(".csv"):
				file_path += ".csv"

		try:
			if self._write_csv(file_path):
				logger.success(f"Sample Container auto-saved to {file_path}")
		except Exception as e:
			logger.error(f"Failed to auto-save CSV: {e}")

	def _import_csv_cb(self, sender, app_data, user_data=None):
		"""Open file explorer to select a CSV and import samples."""
		def load_callback(file_path, selected_ext=None):
			if not file_path:
				return
			
			# Save last path
			self.export_path = file_path
			
			try:
				with open(file_path, "r", encoding="utf-8") as f:
					reader = csv.reader(f)
					header = next(reader, None)
					if not header:
						logger.warning("Import aborted: empty file.")
						return
					
					# Clean up header whitespace
					header = [h.strip() for h in header]
					
					# Detect format
					# Individual format: headers ending with _X and _Y
					is_individual = any(h.endswith("_X") or h.endswith("_Y") for h in header)
					
					if is_individual:
						sample_data = {}  # {name: {"x": [], "y": []}}
						col_mappings = {}  # col_idx -> (name, "x" or "y")
						for idx, h in enumerate(header):
							if h.endswith("_X"):
								name = h[:-2]
								col_mappings[idx] = (name, "x")
								if name not in sample_data:
									sample_data[name] = {"x": [], "y": []}
							elif h.endswith("_Y"):
								name = h[:-2]
								col_mappings[idx] = (name, "y")
								if name not in sample_data:
									sample_data[name] = {"x": [], "y": []}
							else:
								# Fallback
								col_mappings[idx] = (h, "y")
								if h not in sample_data:
									sample_data[h] = {"x": [], "y": []}
						
						for row in reader:
							if not row:
								continue
							for idx, val in enumerate(row):
								if idx in col_mappings:
									name, key = col_mappings[idx]
									if val.strip() == "":
										continue
									try:
										sample_data[name][key].append(float(val))
									except ValueError:
										pass
						
						for name, data in sample_data.items():
							if not data["y"]:
								continue
							# Recreate matching X length
							if not data["x"]:
								data["x"] = list(range(len(data["y"])))
							else:
								if len(data["x"]) < len(data["y"]):
									last_val = data["x"][-1] if data["x"] else 0
									data["x"] = data["x"] + [last_val] * (len(data["y"]) - len(data["x"]))
								elif len(data["x"]) > len(data["y"]):
									data["x"] = data["x"][:len(data["y"])]
							
							self.add_or_update_sample(name, data["x"], data["y"])
							
					else:
						# Standard format: first column is X, subsequent are Y for each sample
						sample_names = header[1:]
						x_values = []
						y_values = [[] for _ in sample_names]
						
						for row in reader:
							if not row:
								continue
							
							try:
								x_val = float(row[0]) if len(row) > 0 and row[0].strip() else 0.0
								x_values.append(x_val)
							except ValueError:
								x_values.append(0.0)
								
							for i, val in enumerate(row[1:]):
								if i < len(y_values):
									try:
										y_val = float(val) if val.strip() else 0.0
										y_values[i].append(y_val)
									except ValueError:
										y_values[i].append(0.0)
										
						for i, name in enumerate(sample_names):
							# Trim or pad x_values to match y_values length
							y_len = len(y_values[i])
							if y_len == 0:
								continue
							curr_x = x_values.copy()
							if len(curr_x) < y_len:
								last_val = curr_x[-1] if curr_x else 0.0
								curr_x = curr_x + [last_val] * (y_len - len(curr_x))
							elif len(curr_x) > y_len:
								curr_x = curr_x[:y_len]
								
							self.add_or_update_sample(name, curr_x, y_values[i])
							
				logger.success(f"Successfully imported samples from {file_path}")
			except Exception as e:
				logger.error(f"Failed to import CSV: {e}")

		# Use the same file_explorer object
		file_explorer.select_file(
			default_path=self._get_base_dir(),
			extensions=[("CSV files", "*.csv"), ("All files", "*.*")],
			callback=load_callback
		)

	def sample_checkbox_cb(self, sender, app_data, user_data, *args):
		"""When checkbox toggled, send select/unselect action."""
		sample_uuid = user_data
		sample = self.samples_dict.get(sample_uuid)
		
		if not sample:
			return

		if app_data:
			# Send SELECT action
			self._send_sample(sample, action="select")
		else:
			# Send UNSELECT action
			self._send_sample(sample, action="unselect")

	def sample_name_cb(self, sender, app_data, user_data, *args):
		"""Update sample name and send rename action."""
		sample_uuid = user_data
		new_name = app_data.strip()

		if new_name and sample_uuid in self.samples_dict:
			self.samples_dict[sample_uuid]["name"] = new_name
			
			# Always send RENAME action if checkbox is checked
			checkbox_tag = f"sample_checkbox_{sample_uuid}"

			if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
				self._send_sample(self.samples_dict[sample_uuid], action="rename")

			self._update_x_axis_combo()

	def _send_sample(self, sample: dict, action: str = "select"):
		"""Send SAMPLE with action to all connected outputs."""
		output_sample = {
			"name": sample["name"],
			"uuid": sample["uuid"],
			"x": sample["x"],
			"y": sample["y"],
			"action": action
		}
		
		for output_key in self.outputs:
			for module in self.connections.get(output_key, []):
				module.input_cb(sample=output_sample, data_type=IOTypes.SAMPLE)

	def delete_sample_cb(self, sender, app_data, user_data, *args):
		"""Delete a sample and send unselect if it was selected."""
		sample_uuid = user_data
		
		if sample_uuid in self.samples_dict:
			# If checkbox was checked, send unselect before deleting
			checkbox_tag = f"sample_checkbox_{sample_uuid}"

			if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
				self._send_sample(self.samples_dict[sample_uuid], action="unselect")
			
			# Delete from dict and UI
			del self.samples_dict[sample_uuid]
			if sample_uuid in self.samples_order:
				self.samples_order.remove(sample_uuid)
			dpg.delete_item(f"sample_group_{sample_uuid}")

		self._update_x_axis_combo()

	def select_all_samples_cb(self, sender, app_data, user_data=None, *args):
		"""Select all samples."""
		for sample_uuid in self.samples_dict:
			checkbox_tag = f"sample_checkbox_{sample_uuid}"
			if dpg.does_item_exist(checkbox_tag):
				dpg.set_value(checkbox_tag, True)
				self.sample_checkbox_cb(checkbox_tag, True, sample_uuid)

	def deselect_all_samples_cb(self, sender, app_data, user_data=None, *args):
		"""Deselect all samples."""
		for sample_uuid in self.samples_dict:
			checkbox_tag = f"sample_checkbox_{sample_uuid}"
			if dpg.does_item_exist(checkbox_tag):
				dpg.set_value(checkbox_tag, False)
				self.sample_checkbox_cb(checkbox_tag, False, sample_uuid)

	def clear_samples_cb(self, sender, app_data, user_data=None, *args):
		"""Clear all samples."""
		# Send unselect for all checked samples
		for sample_uuid in list(self.samples_dict.keys()):
			checkbox_tag = f"sample_checkbox_{sample_uuid}"

			if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
				self._send_sample(self.samples_dict[sample_uuid], action="unselect")
			
			dpg.delete_item(f"sample_group_{sample_uuid}")
			del self.samples_dict[sample_uuid]
		self.samples_order.clear()

		self._update_x_axis_combo()

EXPORTED_CLASS = Sample_container_win
EXPORTED_NAME = "Sample Container"


