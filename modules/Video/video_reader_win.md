# Video Reader Module (Motilitometer)

The **Motilitometer Video Reader** module provides hierarchical directory scanning, interactive video navigation with tree views, automated batch queue processing with visual color-coding, and frame-accurate video streaming for the Motilitometer computer vision analysis pipeline.

---

## Key Features

- **Hierarchical Directory Tree**: Scans root directories and nested subdirectories recursively, rendering an interactive tree view with DearPyGui tree nodes.
- **Batch Processing Queue (Process ALL)**:
  - Automates batch analysis by sequentially dispatching videos to downstream trackers
  - Automatically advances to the next video upon receiving completion signals (`IOTypes.TRIGGER`).
  - **Reprocess toggle**: Option to skip already analyzed files (tracked via `.processed` flag files) or force re-analysis.
  - **Subtree execution**: Right-click context menu on any folder to process only that specific directory subtree.
- **State Color-Coding**:
  - **Blue**: Currently active video in playback or analysis.
  - **Green**: Completed / analyzed video file.
- **Media Playback & Scrubber**:
  - Play , Pause, and Stop buttons with multi-threaded daemon playback.
  - Timeline seek bar / frame scrubber for random-access browsing with instant paused preview.
  - Adjustable frame rate (FPS).
- **Format Filtering**: Filter files by extension (`All`, `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.m4v`).
- **Context Menu Dispatch**: Right-click on any video to transmit its file path downstream via the `File Path` output.

---

## Technical Specifications

### Inputs

| Input Type | Description |
|---|---|
| `IOTypes.FOLDER_PATH` | Directory path string or tuple to set active working folder and generate the file tree. |
| `IOTypes.FILE_PATH` | Direct path to a video file. Loads its parent directory and selects the video. |
| `IOTypes.TRIGGER` | Downstream completion notification. When batch processing is active, automatically advances the queue to the next video. |
| `IOTypes.CMD_DICT` | Command dictionary supporting: `{"start": True}`, `{"stop": True}`, `{"pause": True}`, `{"browse": True}`, `{"fps": int}`, `{"folder": str}`. |

### Outputs

| Output Name | Type | Description |
|---|---|---|
| `Frame` | `IOTypes.FRAME` | Emits a tuple `(frame, video_name, frame_idx)` containing the decoded OpenCV image array, video basename, and 0-indexed frame number. |
| `Trigger` | `IOTypes.TRIGGER` | Emits lifecycle event strings (`"START"` when playback begins, `"STOP"` when finished or stopped). |
| `File Path` | `IOTypes.FILE_PATH` | Emits a tuple `(file_path, basename)` on queue advancement or manual context menu request. |

---

## UI Controls

- **Browse** (Button): Opens native OS directory picker to choose the working folder.
- **Format Filter** (Combo): Filters visible video formats (`All`, `.mp4`, `.mov`, etc.).
- **Recursive** (Checkbox): Toggles deep recursive scanning of nested folders.
- **Folder Label**: Displays current loaded path (truncated with ellipsis if exceeding 30 characters).
- **Process ALL** (Button): Starts automated sequential queue of all discovered videos.
- **Stop Process** (Button): Halts active batch execution.
- **Reprocess** (Checkbox): When unchecked, skips videos that have already been marked as processed.
- **Status Text**: Live status indicator (e.g. `Processing 3/20: test.mp4`).
- **Play / Pause / Stop** (Buttons): Transport controls for interactive video preview.
- **FPS** (Input Int): Target playback frame rate.
- **Timeline Slider**: Draggable seek bar displaying and adjusting current frame position.
- **Folder Tree**: Hierarchical folder view. Left-click loads a video; right-click opens the context menu (`Process this folder` or `Send path`).

---

## Lifecycle & Base Class Conformance

- **`is_ready()`**: Returns readiness state (`not self._is_closing`).
- **`update_permission()`**: Disables window close button (`no_close=True`) in user mode.
- **`on_close()`**: Halts queue processing, stops playback thread with timeout (`2.0s`), and releases OpenCV `VideoCapture` resources. Guarded against recursive closure.
- **`close()` / `destroy()`**: Unregisters window and cleans up tag tree.
