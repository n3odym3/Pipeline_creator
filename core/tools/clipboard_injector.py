from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Optional

import cv2
from loguru import logger
import numpy as np

# Conditional import for Windows
if sys.platform == "win32":
    try:
        import win32clipboard
        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
        logger.warning("win32clipboard not found. Clipboard features may fail on Windows.")
else:
    HAS_WIN32 = False


def _open_clipboard_win32(retries: int = 5, delay: float = 0.02) -> bool:
    """
    Attempt to open Windows clipboard with retry logic.

    Other processes (clipboard managers, office apps) may briefly hold the clipboard lock,
    causing transient access denied errors.
    """
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(delay)
    return False


class ClipboardInjector:
    """
    Cross-platform clipboard utility for images and text.

    Supports:
    - Windows: `win32clipboard` with dual-format injection (PNG for transparency + CF_DIB for legacy/Paint compatibility),
      and UTF-16 Unicode text.
    - Linux: Wayland (`wl-copy`) and X11 (`xclip`).
    - macOS: AppleScript (`osascript`) and `pbcopy`.
    """

    def send_image(self, image: np.ndarray) -> bool:
        """
        Send an image array to the system clipboard.

        Args:
            image: Image array in BGR or BGRA format.

        Returns:
            bool: True if image was successfully dispatched to clipboard, False otherwise.
        """
        if image is None or image.size == 0:
            logger.warning("Clipboard image copy aborted: empty image.")
            return False

        if sys.platform == "win32" and HAS_WIN32:
            return self._send_image_win32(image)
        elif sys.platform.startswith("linux"):
            return self._send_image_linux(image)
        elif sys.platform == "darwin":
            return self._send_image_darwin(image)
        else:
            logger.warning(f"Clipboard image copy not supported on {sys.platform}")
            return False

    def _send_image_win32(self, image: np.ndarray) -> bool:
        """
        Windows: Inject image into clipboard.
        - Provides 'PNG' format for applications supporting alpha channels (Photoshop, Word, browsers).
        - Provides 'CF_DIB' (24-bit BGR) for legacy tools (e.g. MS Paint).
        """
        # Ensure image is in 8-bit unsigned integer format
        if np.issubdtype(image.dtype, np.floating):
            if image.max() <= 1.0 and image.size > 0:
                image = (np.clip(image, 0.0, 1.0) * 255.0).astype(np.uint8)
            else:
                image = np.clip(image, 0.0, 255.0).astype(np.uint8)
        elif image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)

        # 1. Prepare 24-bit BGR image for CF_DIB compatibility
        if image.ndim == 3 and image.shape[2] == 4:
            # Alpha-blend over white background using vectorized NumPy broadcasting
            bgr = image[:, :, :3].astype(np.float32)
            alpha = image[:, :, 3:4].astype(np.float32) / 255.0
            dib_image = np.clip(bgr * alpha + 255.0 * (1.0 - alpha), 0, 255).astype(np.uint8)
        elif image.ndim == 2 or (image.ndim == 3 and image.shape[2] == 1):
            dib_image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            dib_image = image

        # Encode 24-bit BMP and strip the 14-byte BITMAPFILEHEADER to get raw CF_DIB bytes
        success_bmp, bmp_buf = cv2.imencode(".bmp", dib_image)
        dib_data = bmp_buf.tobytes()[14:] if success_bmp else None

        # Encode original image as PNG (preserves transparency)
        success_png, png_buf = cv2.imencode(".png", image)
        png_data = png_buf.tobytes() if success_png else None

        if not _open_clipboard_win32():
            logger.error("Windows clipboard error: could not open clipboard (resource locked).")
            return False

        try:
            win32clipboard.EmptyClipboard()

            # Set registered PNG format first (for apps supporting alpha transparency)
            if png_data is not None:
                try:
                    png_format = win32clipboard.RegisterClipboardFormat("PNG")
                    if png_format:
                        win32clipboard.SetClipboardData(png_format, png_data)
                except Exception as e:
                    logger.debug(f"Failed to register/set PNG clipboard format: {e}")

            # Set standard CF_DIB format (MS Paint and legacy app compatible)
            if dib_data is not None:
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)

            logger.success("Image successfully copied to Windows clipboard.")
            return True
        except Exception as e:
            logger.error(f"Windows clipboard error: {e}")
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _send_image_linux(self, image: np.ndarray) -> bool:
        """Linux: Encode as PNG, pipe to wl-copy (Wayland) or xclip (X11)."""
        success, buffer = cv2.imencode(".png", image)
        if not success:
            return False
        data = buffer.tobytes()

        # Try Wayland (wl-copy)
        try:
            subprocess.run(
                ["wl-copy", "-t", "image/png"],
                input=data,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.success("Image copied to Wayland clipboard.")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        # Try X11 (xclip)
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"],
                input=data,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.success("Image copied to X11 clipboard.")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.error("Linux clipboard error: please install 'wl-copy' (Wayland) or 'xclip' (X11).")
            return False

    def _send_image_darwin(self, image: np.ndarray) -> bool:
        """macOS: Encode image as PNG and pipe to clipboard via osascript."""
        success, buffer = cv2.imencode(".png", image)
        if not success:
            return False

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(buffer.tobytes())
                tmp_path = tmp.name

            script = f'set the clipboard to (read (POSIX file "{tmp_path}") as «class PNG »)'
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.success("Image copied to macOS clipboard.")
            return True
        except Exception as e:
            logger.error(f"macOS clipboard image copy error: {e}")
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def send_text(self, data: str) -> bool:
        """
        Send text string to the system clipboard.

        Args:
            data: Text string to copy (Unicode supported).

        Returns:
            bool: True if text was copied, False otherwise.
        """
        if not isinstance(data, str):
            data = str(data)

        if sys.platform == "win32" and HAS_WIN32:
            if not _open_clipboard_win32():
                logger.error("Windows clipboard error: could not open clipboard (resource locked).")
                return False
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(data, win32clipboard.CF_UNICODETEXT)
                return True
            except Exception as e:
                logger.error(f"Failed to copy text to Windows clipboard: {e}")
                return False
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

        elif sys.platform == "darwin":
            try:
                process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                process.communicate(input=data.encode("utf-8"))
                return True
            except Exception as e:
                logger.error(f"macOS clipboard text error: {e}")
                return False

        elif sys.platform.startswith("linux"):
            # Try Wayland (wl-copy)
            try:
                subprocess.run(
                    ["wl-copy"],
                    input=data.encode("utf-8"),
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

            # Try X11 (xclip)
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-i"],
                    input=data.encode("utf-8"),
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                logger.error("Linux clipboard error: please install 'wl-copy' or 'xclip'.")
                return False

        return False


clipboardinjector = ClipboardInjector()
