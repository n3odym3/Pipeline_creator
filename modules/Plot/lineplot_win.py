import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
import numpy as np
from core.input_output_types import IOTypes
from loguru import logger
from .clipboard_injector import clipboardinjector

class Lineplot_win(WindowBase):
	def __init__(self,
				label="Lineplot",
				win_width=300,
				win_height=200,
				pos=(10, 10),
				uuid=None,
				outputs=None,
				visible=True,
                autoscale=True,
                smooth=False,
                smooth_window=5,
                log_x=False):

		if outputs is None:
			outputs = [] 

		super().__init__(label=label,pos=pos,win_width=win_width,win_height=win_height,uuid=uuid,outputs=outputs,visible=visible)

		self.plot_tag = "lineplot_plot" + self.UUID
		self.anot_check_tag = "lieplot_anot_check" + self.UUID
		self.autoscale_check_tag = "lineplot_autoscale_check" + self.UUID
		self.smooth_check_tag = "lineplot_smooth_check" + self.UUID
		self.smooth_win_tag = "lineplot_smooth_win" + self.UUID
		self.logX_check_tag = "lineplot_logx_check" + self.UUID
		
		self.xaxis_tag = "lineplot_x_axis" + self.UUID
		self.yaxis_tag = "lineplot_y_axis" + self.UUID
		self.closest_point_anot_tag = "closest_point_anot" + self.UUID
		self.handler_tag = "lineplot_handler" + self.UUID
		self.export_btn_tag = "lineplot_export_btn" + self.UUID
		self.drag_line_check_tag = "lineplot_vline_check" + self.UUID
		self.drag_line_tag = "lineplot_vline" + self.UUID

		self.autoscale = autoscale
		self.smooth = smooth
		self.smooth_window = smooth_window
		self.log_x = log_x
		self.selected_serie = None

		self._persistent_fields = ["label", "autoscale", "smooth", "smooth_window", "log_x"]
		self.accepted_input_types = [IOTypes.SAMPLE, IOTypes.ROI_SAMPLE, IOTypes.DATALIST, IOTypes.CMD_DICT]
		self.output_types = ["str"]

		self.outputs = {
			"Datalist" : IOTypes.DATALIST,
			"CMD" : IOTypes.CMD_DICT
		}

		self.descriptions = {
			"Datalist" : "Outputs the data as a list of points",
			"CMD" : "Outputs a command dictionary with the action to perform"
		}
		
		self.connections = {k: [] for k in self.outputs}

		with dpg.window(label=self.label,
						width=self.win_width,
						height=self.win_height,
						pos=self.pos,
						tag=self.winID,
						show=self.visible):
			
			with dpg.group(horizontal=True):
				dpg.add_checkbox(label="Anotation", tag=self.anot_check_tag, default_value=True)
				dpg.add_checkbox(label="Drag Line", tag=self.drag_line_check_tag, default_value=False, callback=self.toggle_drag_line)
				dpg.add_checkbox(label="Autoscale", tag=self.autoscale_check_tag, default_value=self.autoscale, callback=self.toggle_autoscale)
				dpg.add_checkbox(label="Smooth", tag=self.smooth_check_tag, default_value=self.smooth, callback=self.toggle_smooth)
				dpg.add_checkbox(label="Log X", tag=self.logX_check_tag, default_value=self.log_x, callback=self.toggle_log_x)
				dpg.add_drag_int(label="Win", tag=self.smooth_win_tag, default_value=self.smooth_window, min_value=1, max_value=50, width=100, callback=self.update_smooth_win)
				dpg.add_button(label="Export TSV", tag=self.export_btn_tag, callback=self.export_tsv_cb)
				dpg.add_button(label="Send Plot", callback=self.send_plot_cb)
				dpg.add_button(label="Clear Plot", callback=self.clear_plot)

			with dpg.plot(label="Line Serie", height=-1, width=-1, tag=self.plot_tag):
				dpg.add_plot_legend()

				dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag=self.xaxis_tag, log_scale=self.log_x)
				dpg.add_plot_axis(dpg.mvYAxis, label="Intensity", tag=self.yaxis_tag)

	
			with dpg.handler_registry(tag=self.handler_tag):
				dpg.add_mouse_move_handler(callback=self.plot_change_callback)
			
			dpg.configure_item(self.plot_tag, anti_aliased=True)
			dpg.configure_item(self.plot_tag, crosshairs=True)

		self.update_permission()

	def update_permission(self):
		"""
		Hide specific UI elements in 'user' mode.
		"""
		from core.app_state import app_state
		mode = app_state.mode
		
		# Elements to toggle visibility for
		elements = [
			self.anot_check_tag,
			self.drag_line_check_tag,
			self.smooth_check_tag,
			self.smooth_win_tag
		]
		
		show = (mode != "user")
		
		for tag in elements:
			if dpg.does_item_exist(tag):
				if show:
					dpg.show_item(tag)
				else:
					dpg.hide_item(tag)

	def toggle_autoscale(self, sender, app_data, user_data=None, *args):
		self.autoscale = app_data

	def toggle_smooth(self, sender, app_data, user_data=None, *args):
		self.smooth = app_data

	def update_smooth_win(self, sender, app_data, user_data=None, *args):
		self.smooth_window = app_data

	def toggle_log_x(self, sender, app_data, user_data=None, *args):
		self.log_x = app_data
		
		# Dear PyGui axes log_scale is immutable. Recreate the axes.
		series_data = []
		# Get all line series from the old y axis
		children = dpg.get_item_children(self.yaxis_tag, 1)
		if children:
			for child in children:
				if dpg.get_item_type(child) == "mvAppItemType::mvLineSeries":
					series_data.append({
						"x": dpg.get_value(child)[0],
						"y": dpg.get_value(child)[1],
						"label": dpg.get_item_label(child),
						"tag": child
					})
		
		dpg.delete_item(self.xaxis_tag)
		dpg.delete_item(self.yaxis_tag)
		
		dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag=self.xaxis_tag, log_scale=self.log_x, parent=self.plot_tag)
		dpg.add_plot_axis(dpg.mvYAxis, label="Intensity", tag=self.yaxis_tag, parent=self.plot_tag)
		
		for s in series_data:
			dpg.add_line_series(x=s["x"], y=s["y"], label=s["label"], parent=self.yaxis_tag, tag=s["tag"])
			dpg.bind_item_theme(s["tag"], "lineplot_blue_theme" + self.UUID)
		
		if self.autoscale:
			self.autofit_axis()

	def toggle_drag_line(self, sender, app_data, user_data=None, *args):
		if not app_data and dpg.does_item_exist(self.drag_line_tag):
			dpg.delete_item(self.drag_line_tag)
	
	def export_tsv_cb(self, sender, app_data, user_data=None, *args):
		"""Export all plot data to clipboard in TSV format (X, Y1, Y2...)."""
		try:
			# Get all line series from the plot
			children = dpg.get_item_children(self.yaxis_tag, 1)
			
			series_list = []
			
			for child in children:
				if dpg.get_item_type(child) == "mvAppItemType::mvLineSeries":
					data = dpg.get_value(child)
					label = dpg.get_item_label(child)
					
					series_list.append({
						"label": label,
						"x": list(data[0]) if data[0] is not None else [],
						"y": list(data[1]) if data[1] is not None else []
					})
			
			if not series_list:
				return
			
			# Find maximum length among all series
			max_len = max(len(s["x"]) for s in series_list)
			
			tsv_lines = []
			# Header: X	Series1	Series2...
			headers = ["X"] + [s["label"] for s in series_list]
			tsv_lines.append("\t".join(headers))
			
			# Data rows
			for i in range(max_len):
				# Get X coordinate from the first series that has data at this index
				x_val = ""
				for s in series_list:
					if i < len(s["x"]):
						x_val = str(s["x"][i])
						break
				
				row = [x_val]
				for s in series_list:
					if i < len(s["y"]):
						row.append(str(s["y"][i]))
					else:
						row.append("")
				tsv_lines.append("\t".join(row))
			
			# Join and copy
			tsv_content = "\n".join(tsv_lines)
			clipboardinjector.send_text(tsv_content)
			
			logger.info(f"Exported {len(series_list)} series to TSV")
			
		except Exception as e:
			logger.error(f"Error exporting TSV: {e}")

	def send_plot_cb(self, sender, app_data, user_data=None, *args):
		"""Extracts all series and emits them via the CMD output."""
		try:
			children = dpg.get_item_children(self.yaxis_tag, 1)
			all_series = []
			
			for child in children:
				if dpg.get_item_type(child) == "mvAppItemType::mvLineSeries":
					data = dpg.get_value(child)
					label = dpg.get_item_label(child)
					all_series.append({
						"label": label,
						"x": list(data[0]),
						"y": list(data[1])
					})
			
			if not all_series:
				logger.warning("No data to send")
				return

			payload = {
				"action": "plot_data",
				"series": all_series,
				"title": self.label,
				"xlabel": dpg.get_item_label(self.xaxis_tag),
				"ylabel": dpg.get_item_label(self.yaxis_tag),
				"x_lim": dpg.get_axis_limits(self.xaxis_tag),
				"y_lim": dpg.get_axis_limits(self.yaxis_tag),
				"log_x": dpg.get_value(self.logX_check_tag)
			}
			
			self.trigger_cb(out_key="CMD", data=payload)
			logger.info(f"Sent {len(all_series)} series to CMD output")
			
		except Exception as e:
			logger.error(f"Error sending plot: {e}")

	def rolling_average(self, data, window_size):
		if window_size < 2 or len(data) < window_size:
			return data
		
		kernel = np.ones(window_size) / window_size
		pad_len = window_size - 1
		pad_left = pad_len // 2
		pad_right = pad_len - pad_left
		
		padded_data = np.pad(data, (pad_left, pad_right), mode='edge')
		return np.convolve(padded_data, kernel, mode='valid')

	def find_closest_point(self, mouse_x, mouse_y, x_data, y_data):
		if len(x_data) == 0 or len(y_data) == 0:
			return None
		ptp_x = np.ptp(x_data)
		ptp_y = np.ptp(y_data)
		x_scale = 1 / ptp_x if ptp_x != 0 else 1.0
		y_scale = 1 / ptp_y if ptp_y != 0 else 1.0
		distances = np.hypot((x_data - mouse_x) * x_scale,(y_data - mouse_y) * y_scale)
		return np.argmin(distances)
	
	def select_serie(self, sender, app_data, user_data, *args):
		'''Select the serie that will be tracked/anotated'''
		self.selected_serie = user_data

	def plot_change_callback(self,sender, app_data, user_data=None, *args):
		if dpg.is_item_hovered(self.plot_tag) : #If the mouse is over the plot
			# Annotation update logic handles existence, no need to force delete here

			if dpg.get_value(self.anot_check_tag) : #If the anotation checkbox is checked
				axis = dpg.get_item_children(self.yaxis_tag, 1)
				
				if len(axis) > 0: #If there is a plot in the preview window
					if self.selected_serie is not None and dpg.does_item_exist(self.selected_serie) and dpg.get_item_type(self.selected_serie) == "mvAppItemType::mvLineSeries" :
						plot = dpg.get_value(self.selected_serie) #Get the selected serie
					else :
						plot = dpg.get_value(axis[0]) #Get the first by default

					xplot = plot[0] #get the X data
					yplot = plot[1] #get the Y data
					
					mouse_x, mouse_y = dpg.get_plot_mouse_pos() #get the mouse position
					point_index = self.find_closest_point(mouse_x, mouse_y, np.array(xplot), np.array(yplot)) #Find the closest point between the mouse and the plot

					if point_index is not None:
						annot = f"Index : {point_index}\nX : {xplot[point_index]}\nY : {yplot[point_index]}"
						
						# Update annotation instead of deleting/recreating (prevents color flickering)
						if dpg.does_item_exist(self.closest_point_anot_tag):
							dpg.configure_item(self.closest_point_anot_tag, label=annot, default_value=(xplot[point_index], yplot[point_index]))
						else:
							dpg.add_plot_annotation(tag=self.closest_point_anot_tag, label=annot, default_value=(xplot[point_index], yplot[point_index]), offset=(25, -25), color=[255, 255, 0, 255], parent=self.plot_tag)   
					else:
						if dpg.does_item_exist(self.closest_point_anot_tag):
							dpg.delete_item(self.closest_point_anot_tag)
			
			else:
				if dpg.does_item_exist(self.closest_point_anot_tag):
					dpg.delete_item(self.closest_point_anot_tag)

	def autofit_axis(self,x=True,y=True):
		if x :
			dpg.fit_axis_data(self.xaxis_tag)
		if y :
			dpg.fit_axis_data(self.yaxis_tag)

	def clear_plot(self, sender=None, app_data=None, user_data=None, *args, **kwargs):
		# Delete all line series
		children = dpg.get_item_children(self.yaxis_tag, 1)
		for child in children:
			if dpg.get_item_type(child) == "mvAppItemType::mvLineSeries":
				dpg.delete_item(child)
		
		# Clear annotation
		if dpg.does_item_exist(self.closest_point_anot_tag):
			dpg.delete_item(self.closest_point_anot_tag)
		
		# Reset selected serie
		self.selected_serie = None

	def plot_data(self, x =None, y =None, name="Serie", UUID=None):
		if x is None :
			x = list(range(len(y)))
		if y is None :
			return
		
		# Apply smoothing if enabled
		if self.smooth and len(y) > self.smooth_window:
			y = self.rolling_average(np.array(y), self.smooth_window).tolist()

		serie_id = f"prevplot_{UUID}"

		if dpg.does_item_exist(serie_id) :
			dpg.configure_item(serie_id, x=x, y=y)
			if name:
				dpg.set_item_label(serie_id, name)
			if self.autoscale:
				self.autofit_axis()
		else :
			dpg.add_line_series(x=x, y=y, label=name, parent=self.yaxis_tag, tag=serie_id)
			dpg.bind_item_theme(serie_id, "lineplot_blue_theme" + self.UUID)  # Force blue color

			dpg.configure_item(serie_id, x=x, y=y)
			if self.autoscale:
				self.autofit_axis()

		self.select_serie(None,None, serie_id)

	def input_cb(self, *args, **kwargs):
		# Handle SAMPLE format first (new standard)
		sample = kwargs.get("sample")
		if sample:
			action = sample.get("action", "select")
			uuid = sample.get("uuid")
			
			if action in ["unselect", "delete"]:
				# Remove serie
				if uuid and dpg.does_item_exist(f"prevplot_{uuid}"):
					dpg.delete_item(f"prevplot_{uuid}")
			elif action == "clear" or sample.get("clear"):
				# Fully clear plot
				self.clear_plot()
			elif action == "rename":
				# Just update the name
				new_name = sample.get("name", "Sample")
				if uuid and dpg.does_item_exist(f"prevplot_{uuid}"):
					dpg.set_item_label(f"prevplot_{uuid}", new_name)
			else:
				# select or any other action = plot data
				self.plot_data(
					x=sample.get("x"),
					y=sample.get("y"),
					name=sample.get("name", "Sample"),
					UUID=uuid
				)
			return
		
		# Handle CMD_DICT format
		if kwargs.get("data_type") == IOTypes.CMD_DICT:
			cmd = kwargs.get("cmd") if kwargs.get("cmd") is not None else kwargs.get("data")
			
			if isinstance(cmd, dict) and cmd.get("action") == "position":
				position = cmd.get("value")
				if dpg.does_item_exist(self.drag_line_check_tag) and not dpg.get_value(self.drag_line_check_tag):
					# Checkbox is disabled - ignore the reposition request
					return
				if position is not None:
					x_val = float(position)
					if dpg.does_item_exist(self.drag_line_tag):
						dpg.set_value(self.drag_line_tag, x_val)
					else:
						dpg.add_drag_line(default_value=x_val, tag=self.drag_line_tag, parent=self.plot_tag, color=[255, 0, 0, 255], thickness=2)
				else:
					if dpg.does_item_exist(self.drag_line_tag):
						dpg.delete_item(self.drag_line_tag)
				return

			if cmd and cmd.get("action") == "add serie":
				data = cmd.get("data", {})
				self.plot_data(x=data.get("x", None), y=data.get("y", None), name=data.get("name", None), UUID=data.get("uuid", None))

			if cmd and cmd.get("action") == "remove serie":
				data = cmd.get("data", {})
				UUID = data.get("uuid", None)
				if UUID is not None:
					dpg.delete_item(f"prevplot_{UUID}")
			
			if cmd and cmd.get("action") == "update serie name":
				data = cmd.get("data", {})
				UUID = data.get("uuid", None)
				new_name = data.get("name", None)
				if UUID is not None and new_name is not None:
					if dpg.does_item_exist(f"prevplot_{UUID}"):
						dpg.set_item_label(f"prevplot_{UUID}", new_name)
			if cmd and (cmd.get("action") == "clear" or cmd.get("clear")):
				self.clear_plot()
			return

		# Legacy DATALIST format
		y = kwargs.get("y") if kwargs.get("y") is not None else (args[0] if args and isinstance(args[0], list) else None)
		x = kwargs.get("x") if kwargs.get("x") is not None else (args[1] if len(args) > 1 and isinstance(args[1], list) else None)
		name = kwargs.get("name", "Serie")
		UUID = kwargs.get("uuid", None)
		self.plot_data(x=x, y=y, name=name, UUID=UUID)

	def trigger_cb(self, out_key=None, data=None):
		if out_key in self.connections:
			for module in self.connections[out_key]:
				try:
					module.input_cb(data=data, data_type=self.outputs[out_key])
				except Exception as e:
					logger.error(f"Error triggering output {out_key}: {e}")


EXPORTED_CLASS = Lineplot_win
EXPORTED_NAME = "Lineplot"
