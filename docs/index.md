# Pipeline Creator

**Pipeline Creator** is a modular, node-based graphical interface toolkit built on top of **DearPyGui (DPG)**. It provides a visual canvas where interconnected **nodes** are wired together to form automated data processing and machine control pipelines.

Pipeline Creator simplifies the development of complex graphical interfaces by transforming them into reusable, drag-and-drop node modules. Instead of writing boilerplate code for layouts, windows, and connections, you can visually construct your application on a dynamic canvas. Heavy computations run in background processes, keeping the user interface extremely responsive and stable at high frame rates.

---

## Key Capabilities

- 🧩 **Modular Canvas Interface**: Drag, drop, and arrange modules as floating windows or dock them together for a clean, custom workspace layout.
- 🔗 **Visual Node Connections**: Draw and wire inputs/outputs of different nodes to map out data flows and control sequences dynamically.
- 📡 **Link Bus (Zero-Wire Routing)**: Route data globally between modules without cluttered visual connection lines.
- ⚙️ **Process Isolation**: Run heavy computation nodes in background processes, ensuring the UI remains highly responsive.
- 🔒 **Role-Based Privilege Modes**: Toggle between `User`, `Advanced`, and `Dev` modes to adjust the UI complexity based on the operator's role.
- 💾 **Save & Restore Workspaces**: Save your entire workspace layout, configurations, and connections to portable JSON files for instant recovery.
- 🤖 **Startup Automation**: Execute startup scripts to auto-load layouts, log in, and trigger module actions automatically.
- 🏫 **Tutorial Manager**: Launch interactive, step-by-step tutorials.

---

## Navigation

| Section | Description |
|---|---|
| 🖥️ [Main Window](main_window.md) | Menu bar, themes, modes, keyboard shortcuts |
| 🕸️ [Node Editor](node_editor_guide.md) | Canvas, connections, colorization, flow import/export |
| 📁 [Fusion Manager](fusion_manager.md) | Window docking, tab layouts, workspace organization |
| 🏛️ [Architecture](architecture.md) | Boot sequence, scaling, Link Bus, App State |
| 🧩 [Core vs Dynamic Modules](core_vs_dynamic.md) | Static framework vs user-created extensions |
| ⚙️ [Configuration Reference](config_reference.md) | All `config.json` keys explained |
| 🤖 [Automation & Login](automation_login.md) | Startup scripts, login modes, CLI flags |
| 🛠️ [Developer Guide](developer_guide.md) | How to create UI nodes, processors, and themes |

---

## 📦 Modules Repositories

Explore official standalone module collections designed for plug-and-play use:

| Name | Repository | Description |
| :--- | :--- | :--- |
| **PC Camera Modules** | [n3odym3/PC_Camera_modules](https://github.com/n3odym3/PC_Camera_modules) | Camera acquisition and streaming (webcams, USB, IP cameras, industrial cameras, virtual cameras). |
| **PC Computer Vision Modules** | [n3odym3/PC_Computer_Vision_modules](https://github.com/n3odym3/PC_Computer_Vision_modules) | Computer vision, AI segmentation (SAM), barcode/QR decoding, and optical correction. |
| **PC Images and Video Tools** | [n3odym3/PC_Images_and_video_tools](https://github.com/n3odym3/PC_Images_and_video_tools) | Image and video processing, playback, sequence containers, and high-performance viewers. |
| **PC Plot Modules** | [n3odym3/PC_Plot_modules](https://github.com/n3odym3/PC_Plot_modules) | Real-time plotting, histograms, lineplots, boxplots, polar plots, and Matplotlib visualizations. |

