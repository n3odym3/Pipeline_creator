import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("FontMerger")

try:
    from fontTools.ttLib import TTFont
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.transformPen import TransformPen
except ImportError:
    logger.error("The 'fonttools' library is required.")
    logger.info("Please install it using: pip install fonttools")
    sys.exit(1)

def merge_fonts(main_font_path, icon_font_path, output_path):
    """
    Physically rescales and redraws icons into the main font.
    This is the only way to ensure icons aren't 'tiny' when UPM differs.
    """
    logger.info(f"Loading main font: {main_font_path}")
    main_font = TTFont(main_font_path)
    
    logger.info(f"Loading icon font: {icon_font_path}")
    icon_font = TTFont(icon_font_path)

    # 1. Calculate the real scale factor
    main_upm = main_font['head'].unitsPerEm
    icon_upm = icon_font['head'].unitsPerEm
    scale_factor = main_upm / icon_upm
    logger.info(f"Applying scale factor of {scale_factor}x to icons (Syncing {icon_upm} -> {main_upm} UPM)")

    # 2. Ensure main font has 'glyf' table (required for TTF redrawing)
    if 'glyf' not in main_font:
        # If main is OTF, we convert it to a TTF structure for the output
        logger.info("Converting main font structure to TrueType...")
        from fontTools.ttLib.tables._g_l_y_f import table__g_l_y_f
        from fontTools.ttLib.tables._l_o_c_a import table__l_o_c_a
        main_font['glyf'] = table__g_l_y_f()
        main_font['glyf'].glyphs = {}
        main_font['loca'] = table__l_o_c_a()

    # 3. Tables to update
    main_hmtx = main_font['hmtx'].metrics
    icon_hmtx = icon_font['hmtx'].metrics
    main_cmap_obj = main_font['cmap']
    icon_cmap = icon_font['cmap'].getBestCmap()
    main_glyf = main_font['glyf']
    
    try:
        icon_glyph_set = icon_font.getGlyphSet()
    except Exception as e:
        logger.error(f"Could not get glyph set from icon font: {e}")
        logger.error("Note: Bitmap fonts like NotoColorEmoji are not supported by this redraw script.")
        return

    icons_added = 0

    for code, original_name in icon_cmap.items():
        # EXCLUSION RANGE: We completely ignore all standard text characters from the icon font!
        # code < 0x24F encompasses ASCII, basic Latin, and extended Latin characters (including /, %, etc.)
        if code < 0x002F:
            continue
            
        # Only merge characters that belong to the Private Use Area (PUA) 
        # Font Awesome icons are typically located between U+E000 and U+F8FF.
        if not (0xE000 <= code <= 0xF8FF): 
            continue
            
        if original_name not in icon_glyph_set:
            continue
            
        # Guarantee NO collision with standard glyphs
        target_name = f"FA_icon_{code:04X}_{original_name}"
            
        added_to_cmap = False
        for table in main_cmap_obj.tables:
            if table.isUnicode():
                if code not in table.cmap:
                    table.cmap[code] = target_name
                    added_to_cmap = True
        
        if added_to_cmap:
            # REDRAW with scaling
            pen = TTGlyphPen(None)
            tpen = TransformPen(pen, (scale_factor, 0, 0, scale_factor, 0, 0))
            
            try:
                icon_glyph_set[original_name].draw(tpen)
                main_glyf[target_name] = pen.glyph() # Use the pen to create a real TTF glyph
            except Exception as e:
                logger.warning(f"Failed to redraw glyph {original_name}: {e}")
                continue
            
            # Scale Horizontal Metrics
            if original_name in icon_hmtx:
                width, lsb = icon_hmtx[original_name]
                main_hmtx[target_name] = (int(width * scale_factor), int(lsb * scale_factor))
            
            # Update Glyph Order
            if target_name not in main_font.getGlyphOrder():
                main_font.setGlyphOrder(main_font.getGlyphOrder() + [target_name])
            icons_added += 1

    logger.info(f"Successfully rescued {icons_added} icons from the 'tiny' dimension.")
    
    # Save as .ttf (the most stable format for DPG)
    output_path = Path(output_path).with_suffix(".ttf")
    logger.info(f"Saving to: {output_path}")
    main_font.save(str(output_path))
    logger.info("Success: High-fidelity scaled font created!")

if __name__ == "__main__":
    root = Path(__file__).parent.parent

    # Use consola.ttf as base and font_awesome.otf for icons
    f1 = root / "ressources" / "consola.ttf"
    f2 = root / "ressources" / "font_awesome.otf"
    out = root / "ressources" / "consola_awesome.ttf"
    
    if f1.exists() and f2.exists():
        merge_fonts(f1, f2, out)
    else:
        if not f1.exists(): logger.error(f"Missing base font: {f1}")
        if not f2.exists(): logger.error(f"Missing icon font: {f2}")
