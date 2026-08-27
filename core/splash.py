from __future__ import annotations

import multiprocessing
import tkinter as tk
from pathlib import Path


def _run_splash(
    image_path: str,
    max_size: int,
    thickness: int,
    queue: multiprocessing.Queue | None = None,
) -> None:
    """
    Function executed in a separate process to show the splash screen.
    This keeps the UI responsive and isolated from the main process.
    """
    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        # Transparency setup (Windows only)
        transparent_color = "#000001"
        try:
            root.attributes("-transparentcolor", transparent_color)
        except Exception:
            pass

        root.configure(bg=transparent_color)

        # Image loading
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageTk

            pil_img = Image.open(image_path).convert("RGBA")
            pil_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            # Add White Contour to Logo
            if thickness > 0:
                # 1. Get the alpha channel and dilate/blur it to create a smooth contour shape
                alpha = pil_img.getchannel("A")
                # thickness maps to MaxFilter size: 1 -> 3, 2 -> 5, 3 -> 7
                filter_size = 2 * thickness + 1
                contour_mask = alpha.filter(ImageFilter.MaxFilter(filter_size))
                contour_mask = contour_mask.filter(ImageFilter.GaussianBlur(0.8))

                # 2. Create a solid white image with the dilated/blurred alpha
                contour = Image.new("RGBA", pil_img.size, (255, 255, 255, 255))
                contour.putalpha(contour_mask)

                # 3. Paste the original image on top of the white contour
                pil_img = Image.alpha_composite(contour, pil_img)

            # Binarize Alpha Channel with a narrow smooth transition
            # This fixes Tkinter transparency glitches while keeping the edges anti-aliased (smooth).
            # A ramp from 50 to 100 creates a tight 1-2 pixel transition, preventing a dark halo.
            alpha = pil_img.getchannel("A")
            smooth_alpha = alpha.point(
                lambda p: 0 if p < 50 else (255 if p > 100 else int((p - 50) * 255 / 50))
            )
            pil_img.putalpha(smooth_alpha)

            img = ImageTk.PhotoImage(pil_img)
        except ImportError:
            img = tk.PhotoImage(file=image_path)
            w = img.width()
            if w > max_size:
                factor = max(1, w // max_size)
                img = img.subsample(factor, factor)

        # Layout and centering
        w = max(img.width(), 400)  # Ensure enough width for text
        h = img.height()
        extra_h = 90  # More space for the outlined text and padding

        ws = root.winfo_screenwidth()
        hs = root.winfo_screenheight()
        x = (ws / 2) - (w / 2)
        y = (hs / 2) - ((h + extra_h) / 2)
        root.geometry(f"{w}x{h + extra_h}+{int(x)}+{int(y)}")

        # Display elements
        lbl_img = tk.Label(root, image=img, bg=transparent_color, borderwidth=0, highlightthickness=0)
        lbl_img.image = img
        lbl_img.pack()

        canvas_h = 75

        # Pre-load fonts for performance
        try:
            try:
                font_title = ImageFont.truetype("segoeuib.ttf", 20)
                font_detail = ImageFont.truetype("segoeuib.ttf", 20)
            except Exception:
                font_title = ImageFont.load_default()
                font_detail = ImageFont.load_default()
        except Exception:
            font_title = None
            font_detail = None

        def render_text_with_outline(text: str, font, fill_color, outline_color, thickness, y_pos) -> Image.Image:
            txt_img = Image.new("RGBA", (w, canvas_h), (0, 0, 0, 0))
            if not text:
                return txt_img

            mask = Image.new("L", (w, canvas_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.text((w / 2, y_pos), text, font=font, fill=255, anchor="mm")

            if thickness > 0:
                outline_mask = mask.filter(ImageFilter.MaxFilter(2 * thickness + 1))
                outline_mask = outline_mask.filter(ImageFilter.GaussianBlur(0.8))

                outline_layer = Image.new("RGBA", (w, canvas_h), outline_color)
                outline_layer.putalpha(outline_mask)

                text_layer = Image.new("RGBA", (w, canvas_h), fill_color)
                smooth_mask = mask.filter(ImageFilter.GaussianBlur(0.5))
                text_layer.putalpha(smooth_mask)

                return Image.alpha_composite(outline_layer, text_layer)
            else:
                text_layer = Image.new("RGBA", (w, canvas_h), fill_color)
                smooth_mask = mask.filter(ImageFilter.GaussianBlur(0.5))
                text_layer.putalpha(smooth_mask)
                return text_layer

        def update_text(title_text: str, detail_text: str) -> None:
            title_img = render_text_with_outline(
                title_text, font_title, (34, 34, 34, 255), (255, 255, 255, 255), thickness, 20
            )
            detail_img = render_text_with_outline(
                detail_text, font_detail, (85, 85, 85, 255), (255, 255, 255, 255), thickness, 50
            )

            composited = Image.alpha_composite(title_img, detail_img)

            alpha = composited.getchannel("A")
            smooth_alpha = alpha.point(
                lambda p: 0 if p < 50 else (255 if p > 100 else int((p - 50) * 255 / 50))
            )
            composited.putalpha(smooth_alpha)

            tk_txt_img = ImageTk.PhotoImage(composited)
            lbl_text_img.config(image=tk_txt_img)
            lbl_text_img.image = tk_txt_img

        lbl_text_img = tk.Label(root, bg=transparent_color, borderwidth=0, highlightthickness=0)
        lbl_text_img.pack(pady=5)

        current_title = "Initializing modules"
        current_detail = ""
        dots_count = 0

        def update_display() -> None:
            dots = "." * (dots_count % 4)
            update_text(f"{current_title}{dots}", current_detail)

        def check_queue() -> None:
            nonlocal current_title, current_detail
            updated = False
            if queue is not None:
                try:
                    while not queue.empty():
                        msg = queue.get_nowait()
                        if isinstance(msg, tuple) and len(msg) == 2:
                            det, main_act = msg
                            current_detail = det
                            if main_act is not None:
                                current_title = main_act
                            updated = True
                        elif isinstance(msg, str):
                            current_detail = msg
                            updated = True
                except Exception:
                    pass
            if updated:
                update_display()
            root.after(50, check_queue)

        def animate(count: int = 0) -> None:
            nonlocal dots_count
            dots_count = count
            update_display()
            root.after(400, animate, count + 1)

        check_queue()
        animate()
        root.mainloop()
    except Exception:
        pass


_splash_process: multiprocessing.Process | None = None
_splash_queue: multiprocessing.Queue | None = None


def show_splash(image_path: str, max_size: int = 400, thickness: int = 2) -> None:
    """
    Spawns a new process to display the splash screen.
    """
    if not Path(image_path).exists():
        return

    global _splash_process, _splash_queue
    _splash_queue = multiprocessing.Queue()

    _splash_process = multiprocessing.Process(
        target=_run_splash,
        args=(image_path, max_size, thickness, _splash_queue),
        daemon=True,
    )
    _splash_process.start()


def update_splash_status(status_text: str, main_action: str | None = None) -> None:
    """
    Sends a loading status update to the splash screen.
    If main_action is provided, it updates the main title text (which gets animating dots).
    """
    global _splash_queue
    if _splash_queue is not None:
        try:
            _splash_queue.put((status_text, main_action))
        except Exception:
            pass


def close_splash() -> None:
    """
    Terminates the splash screen process.
    """
    global _splash_process, _splash_queue
    if _splash_process and _splash_process.is_alive():
        try:
            _splash_process.terminate()
            _splash_process.join(timeout=0.2)
        except Exception:
            pass
        _splash_process = None
    _splash_queue = None

