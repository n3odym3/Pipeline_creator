# Compiling & Distributing the Application

Pipeline Creator provides a build script, `make_executable.py` that automates packaging the entire application into a standalone, portable folder. This compiled build contains the main executable (`Pipeline_Creator.exe`) along with the Python runtime and all required library dependencies.

---

## Why Compile to an Executable?

- **Zero-Dependency Deployment**: Users do not need to install Python, configure virtual environments, or run `pip install` commands. Everything runs out of the box.
- **Improved Performance & Protection**: Compiles code using **Nuitka**, which translates Python code into highly optimized C++ source code before compile time.
- **Easy Distribution**: The output `main.dist` directory can be easily compressed and shared.

---

## Compilation Settings (`make_executable.py`)

At the top of `make_executable.py` you can control the compilation behavior using the following variables:

```python
# Path to a specific layout JSON, or None to compile everything
MAIN_PIPELINE = "layouts/Webcam.json"  

# Output destination directory for Nuitka
OUTPUT_DIR = "C:/Users/demo/Desktop/Pipeline_creator" 
```

### 1. Full Build (Compile All Modules)
To compile all modules and their dependencies:

- Set `MAIN_PIPELINE = None`.
- The script scans the entire `modules/` directory, extracts all imported packages, and instructs Nuitka to include every single dependency in the bundle.
- **Best for**: Internal developer builds or general-purpose pipeline creation environments.

### 2. Targeted Build (Compile Specific Pipeline)
To compile a lightweight bundle for a specific, final application:

- Set `MAIN_PIPELINE = "layouts/YourLayout.json"`.
- The build script parses the JSON layout file to find *only* the modules referenced in that pipeline.
- It dynamically computes the imports and dependency libraries of *just those modules* and excludes unused packages.
- During the final stage, only the used module subfolders are copied to the `main.dist/modules/` directory.
- **Best for**: Shipping a locked, lightweight final product to end users.

---

## Running the Build

1. Ensure Nuitka and a compatible C compiler (like MSVC on Windows) are installed in your environment.
2. Run the script from the terminal:
   ```powershell
   python make_executable.py
   ```
3. Locate the output directory (specified by `OUTPUT_DIR`). The portable bundle is generated under `<OUTPUT_DIR>/main.dist/`.
