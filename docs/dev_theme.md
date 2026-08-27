# Creating a Custom Theme

Pipeline Creator's visual appearance is controlled by **color palette dictionaries** defined in `config/theme_colors.py`. Adding a new theme requires only adding a new dictionary, no code changes needed.

---

## How Themes Work

The `ThemeManager` auto-discovers all `ALL_CAPS` dictionaries in `theme_colors.py` at startup using:

```python
for attr_name, palette in vars(theme_colors_module).items():
    if attr_name.isupper() and not attr_name.startswith("_"):
        if isinstance(palette, dict):
            PALETTES[attr_name] = palette
```

So a dictionary named `DARK_OCEAN` will automatically appear as the `"DARK_OCEAN"` theme in the **Tools → Theme** menu.

---

## Palette Dictionary Structure

A palette maps **semantic color keys** to RGBA tuples `(R, G, B, A)` where each channel is `0–255`:

```python
MY_THEME = {
    # Window chrome
    "window_bg":           (18, 18, 28, 255),
    "title_bar":           (30, 30, 50, 255),
    "title_bar_active":    (50, 50, 90, 255),
    "border":              (80, 80, 120, 200),

    # Buttons
    "button":              (60, 100, 180, 255),
    "button_hover":        (90, 130, 210, 255),
    "button_active":       (40,  80, 160, 255),

    # Text
    "text":                (220, 220, 230, 255),
    "text_disabled":       (120, 120, 140, 255),

    # Input fields
    "frame_bg":            (40,  40,  60, 255),
    "frame_bg_hover":      (55,  55,  80, 255),
    "frame_bg_active":     (70,  70, 100, 255),

    # Node editor
    "node_bg":             (30,  30,  50, 230),
    "node_border":         (80,  80, 130, 255),
    "node_title_bar":      (50,  50,  90, 255),
    "link_color":          (100, 200, 255, 255),

    # Highlights
    "highlight":           (255, 200,  50, 255),
    "error":               (220,  60,  60, 255),
    "success":             (60,  200, 100, 255),
}
```

---

## Step-by-Step

### 1. Open `config/theme_colors.py`

Add your new palette dictionary at the bottom of the file:

```python
# ── My Custom Dark Ocean Theme ─────────────────────────────────────────────
DARK_OCEAN = {
    "window_bg":        (10, 20, 40, 255),
    "title_bar":        (15, 35, 65, 255),
    "title_bar_active": (20, 50, 90, 255),
    "button":           (30, 100, 160, 255),
    "button_hover":     (50, 130, 190, 255),
    "text":             (200, 220, 240, 255),
    # ... add all required keys
}
```

### 2. Test It

Restart Pipeline Creator and navigate to **Tools → Theme → DARK_OCEAN**. The palette applies immediately.

### 3. Persist It

To make your theme the default, update `config.json`:

```json
"UI": {
    "theme_name": "DARK_OCEAN"
}
```

---

## Colorblind Variants

To provide a colorblind-safe version of your theme, suffix the palette name with the type:

```python
DARK_OCEAN_DEUTERANOPIA = {
    # Same structure, adjusted colors for green-blind users
    "success": (50, 150, 255, 255),  # Blue instead of green
    ...
}
```

Set `UI.colorblind_type` to `"deuteranopia"` in `config.json`. The theme manager will automatically append the suffix and try to load the variant.

---

## Tips for Good Color Palettes

- **Contrast ratio**: Ensure text colors have at least a 4.5:1 contrast ratio against their background for readability.
- **Node editor legibility**: Node titles should be clearly distinct from the canvas background.
- **Use HSL thinking**: Design your palette from a hue/saturation/lightness perspective, then convert to RGB. This makes it easy to create coherent light/dark variants.
- **Test with the log viewer open**: The log viewer uses heavily styled text, it's a good stress-test for your palette.
