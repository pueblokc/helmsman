"""Generate helmsman.ico (multi-resolution) from assets/logo.png."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
src = Image.open(ROOT / "assets" / "logo.png").convert("RGBA")
sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
out = ROOT / "build" / "helmsman.ico"
src.save(out, format="ICO", sizes=sizes)
print(f"wrote {out} ({out.stat().st_size} bytes)")
