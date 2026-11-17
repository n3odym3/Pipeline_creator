import subprocess
import os
import struct

svg_path = os.path.abspath("docs/media/functional_architecture.svg")
html_path = os.path.abspath("docs/media/temp_render.html")
png_path = os.path.abspath("docs/media/functional_architecture.png")

with open(svg_path, "r", encoding="utf-8") as f:
    svg_content = f.read()

html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0;
    padding: 0;
    background-color: #ffffff;
    width: 1460px;
    height: 1255px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }}
  svg {{
    width: 1440px;
    height: 1235px;
  }}
</style>
</head>
<body>
{svg_content}
</body>
</html>"""

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)

chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(chrome_path):
    chrome_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

file_url = "file:///" + html_path.replace(os.sep, "/")
cmd = [
    chrome_path,
    "--headless=new",
    f"--screenshot={png_path}",
    "--window-size=1460,1255",
    "--force-device-scale-factor=2",
    "--hide-scrollbars",
    file_url,
]

print("Running command:", " ".join(cmd))
res = subprocess.run(cmd, capture_output=True, text=True)
print("Return code:", res.returncode)

if os.path.exists(png_path):
    with open(png_path, "rb") as f:
        f.seek(16)
        w, h = struct.unpack(">II", f.read(8))
    print(f"Success! Output PNG: {w}x{h}, size: {os.path.getsize(png_path)} bytes")
else:
    print("PNG not created. Stderr:", res.stderr)

if os.path.exists(html_path):
    os.remove(html_path)
