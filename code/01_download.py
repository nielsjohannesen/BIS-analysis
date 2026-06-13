"""
Download BIS Locational Banking Statistics (WS_LBS_D_PUB) bulk CSV.

The BIS Data Portal publishes the full LBS dataset as a zipped CSV file.
This script downloads it into data/ and extracts it. Run once per vintage.

Output: data/WS_LBS_D_PUB_csv_col.csv
"""

import io
import zipfile
from pathlib import Path

import requests

# BIS Data Portal bulk-download URL (column-format CSV).
# If this 404s, visit https://data.bis.org/bulkdownload and copy the
# updated link for "Locational banking statistics".
BIS_ZIP_URL = "https://data.bis.org/static/bulk/WS_LBS_D_PUB_csv_col.zip"

DATA_DIR = Path(__file__).parent.parent / "data"
OUT_FILE = DATA_DIR / "WS_LBS_D_PUB_csv_col.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BIS-analysis research script)"
    )
}


def download_lbs(url: str = BIS_ZIP_URL, out_file: Path = OUT_FILE) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading LBS data from:\n  {url}")
    response = requests.get(url, headers=HEADERS, timeout=300)
    response.raise_for_status()

    print(f"  Downloaded {len(response.content) / 1e6:.1f} MB — extracting …")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV found inside zip. Contents: {zf.namelist()}")
        csv_name = max(csv_names, key=lambda n: zf.getinfo(n).file_size)
        print(f"  Extracting '{csv_name}' → {out_file}")
        with zf.open(csv_name) as src, open(out_file, "wb") as dst:
            dst.write(src.read())

    print(f"Done. Raw data saved to: {out_file}")


if __name__ == "__main__":
    download_lbs()
