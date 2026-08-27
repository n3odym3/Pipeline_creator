import io
import sys
import numpy as np
from PIL import Image
import subprocess
from typing import Any, Union
from loguru import logger

# Conditional import for Windows
if sys.platform == 'win32':
    try:
        import win32clipboard
        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
        logger.warning("win32clipboard not found. Clipboard features may fail on Windows.")
else:
    HAS_WIN32 = False

class ClipboardInjector:
    def _to_pil_image(self, image: Any) -> Image.Image:
        """Convert input image (numpy array or PIL Image) to PIL Image (RGB)."""
        if isinstance(image, Image.Image):
            return image
        if isinstance(image, np.ndarray):
            if image.ndim == 3 and image.shape[2] == 3:
                # BGR to RGB conversion
                return Image.fromarray(image[:, :, ::-1])
            elif image.ndim == 3 and image.shape[2] == 4:
                # BGRA to RGBA conversion
                return Image.fromarray(image[:, :, [2, 1, 0, 3]])
            return Image.fromarray(image)
        raise TypeError(f"Unsupported image type for clipboard: {type(image)}")

    def send_image(self, image: Union[np.ndarray, Image.Image]) -> None:
        """
        Sends an image to the clipboard (cross-platform).
        - Windows: win32clipboard (CF_DIB)
        - Linux: xclip or wl-copy (image/png)
        - Mac: Not implemented (requires pyobjc or complex scripting)
        
        Parameters:
            image (numpy.ndarray or PIL.Image): The image to be sent (BGR or RGB format).
        """
        if sys.platform == 'win32' and HAS_WIN32:
            self._send_image_win32(image)
        elif sys.platform.startswith('linux'):
            self._send_image_linux(image)
        else:
            logger.warning(f"Clipboard image copy not supported on {sys.platform}")

    def _send_image_win32(self, image: Union[np.ndarray, Image.Image]) -> None:
        """Windows: Encode as BMP via PIL, strip 14-byte header to get DIB, send to clipboard."""
        try:
            pil_img = self._to_pil_image(image)
            output = io.BytesIO()
            pil_img.save(output, format="BMP")
            data = output.getvalue()[14:]  # Skip 14-byte BMP file header to get DIB

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        except Exception as e:
            logger.error(f"Windows clipboard error: {e}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _send_image_linux(self, image: Union[np.ndarray, Image.Image]) -> None:
        """Linux: Encode as PNG via PIL, pipe to xclip (X11) or wl-copy (Wayland)."""
        try:
            pil_img = self._to_pil_image(image)
            output = io.BytesIO()
            pil_img.save(output, format="PNG")
            data = output.getvalue()
        except Exception as e:
            logger.error(f"Failed to encode image with PIL: {e}")
            return

        # Try Wayland first
        try:
            subprocess.run(['wl-copy', '-t', 'image/png'], input=data, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        # Try X11 (xclip)
        try:
            subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'image/png', '-i'], input=data, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.error("Linux clipboard error: Install 'xclip' (X11) or 'wl-copy' (Wayland)")

    def send_text(self, data: str) -> None:
        """Sends text to clipboard."""
        if sys.platform == 'win32' and HAS_WIN32:
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(data)
            except Exception: pass
            finally:
                try: win32clipboard.CloseClipboard()
                except Exception: pass
        elif sys.platform == 'darwin':
            try:
                p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
                p.communicate(input=data.encode('utf-8'))
            except Exception: pass
        elif sys.platform.startswith('linux'):
             # Try wl-copy or xclip
            try:
                subprocess.run(['wl-copy'], input=data.encode('utf-8'), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                try:
                    subprocess.run(['xclip', '-selection', 'clipboard', '-i'], input=data.encode('utf-8'), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception: pass

clipboardinjector = ClipboardInjector()
