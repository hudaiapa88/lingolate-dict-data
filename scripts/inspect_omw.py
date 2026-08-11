"""List all omw-data 1.4 languages + their licenses from README."""
import tarfile
from pathlib import Path

OMW_DIR = Path(r"E:\project\mobile-apps\lingolate-dict-data\sources_cache\omw")

# We only have en and ja downloaded. Let's fetch the full list from the index.toml
# But first, let's just check what we have
for lang in ["en", "ja"]:
    archive = OMW_DIR / f"omw-{lang}.tar.xz"
    if not archive.exists() or archive.stat().st_size < 100:
        continue
    print(f"\n=== {lang.upper()} ===")
    with tarfile.open(archive, "r:xz") as tar:
        for m in tar.getmembers():
            if "README" in m.name.upper() or "LICENSE" in m.name.upper():
                f = tar.extractfile(m)
                if f:
                    content = f.read().decode("utf-8", errors="replace")
                    print(f"\n--- {m.name} ---")
                    print(content[:2000])
