"""
Build a bilateral panel dataset from BIS Locational Banking Statistics.

Source file: data/WS_LBS_D_PUB_csv_col.csv

The BIS CSV has paired code+label columns for each dimension, followed by
one column per quarter (e.g. "1977-Q4", "1978-Q1", …).

Filters applied:
  L_MEASURE   = S   → Amounts outstanding / Stocks
  L_POSITION  = L   → Total liabilities
  L_INSTR     = A   → All instruments
  L_DENOM     = TO1 → All currencies, USD-converted
  L_CURR_TYPE = A   → All currencies
  L_PARENT_CTY= 5J  → All countries (parent)
  L_REP_BANK_TYPE = A → All reporting institutions
  L_CP_SECTOR = N   → Non-banks, total
  L_POS_TYPE  = N   → Cross-border positions

Country filtering:
  L_REP_CTY   : keep 2-letter ISO codes only (actual bank countries)
  L_CP_COUNTRY: keep 2-letter ISO codes + "5J" (All countries = World)

Output columns:
  bank_country        reporting bank country name
  bank_iso            reporting country ISO 2-letter code
  counterpart_country counterpart country name
  counterpart_iso     counterpart country ISO 2-letter code (or "WLD" for world)
  quarter             e.g. "2023-Q4"
  amount_usd          USD millions (stocks, as reported by BIS)

Outputs:
  output/lbs_bilateral_panel.csv
  output/lbs_bilateral_panel.dta  (Stata 14 format)
"""

import re
from pathlib import Path

import pandas as pd
import pyreadstat

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "WS_LBS_D_PUB_csv_col.csv"
OUT_DIR = ROOT / "output"
OUT_CSV = OUT_DIR / "lbs_bilateral_panel.csv"
OUT_DTA = OUT_DIR / "lbs_bilateral_panel.dta"

# ---------------------------------------------------------------------------
# Column names in the BIS CSV
# Each dimension has a code column and a label column side-by-side.
# ---------------------------------------------------------------------------

COL_MEASURE      = "L_MEASURE"
COL_POSITION     = "L_POSITION"
COL_INSTR        = "L_INSTR"
COL_DENOM        = "L_DENOM"
COL_CURR_TYPE    = "L_CURR_TYPE"
COL_PARENT_CTY   = "L_PARENT_CTY"
COL_REP_BANK_TYPE= "L_REP_BANK_TYPE"
COL_REP_CTY      = "L_REP_CTY"        # bank / reporting country (code)
COL_REP_CTY_LBL  = "Reporting country" # bank country name
COL_CP_SECTOR    = "L_CP_SECTOR"
COL_CP_COUNTRY   = "L_CP_COUNTRY"     # counterpart country (code)
COL_CP_CTY_LBL   = "Counterparty country"  # counterpart country name
COL_POS_TYPE     = "L_POS_TYPE"

# Dimension filter values
FILTERS = {
    COL_MEASURE:       "S",
    COL_POSITION:      "L",
    COL_INSTR:         "A",
    COL_DENOM:         "TO1",
    COL_CURR_TYPE:     "A",
    COL_PARENT_CTY:    "5J",
    COL_REP_BANK_TYPE: "A",
    COL_CP_SECTOR:     "N",
    COL_POS_TYPE:      "N",
}

# BIS aggregate code for "All countries" kept as World counterpart
WORLD_CODE = "5J"
WORLD_ISO  = "WLD"
WORLD_NAME = "World"

# Regex for valid quarter column names
QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_iso2(code: str) -> bool:
    """True for 2-letter uppercase codes (ISO 3166-1 alpha-2 style)."""
    return bool(re.match(r"^[A-Z]{2}$", code))


def normalise_counterpart(code: str, name: str) -> tuple[str, str]:
    if code == WORLD_CODE:
        return WORLD_NAME, WORLD_ISO
    return name, code


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_raw(path: Path) -> pd.DataFrame:
    print(f"Loading: {path}")
    df = pd.read_csv(path, low_memory=False)
    print(f"  Shape: {df.shape}")
    return df


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    print("Applying filters …")
    mask = pd.Series(True, index=df.index)
    for col, val in FILTERS.items():
        if col not in df.columns:
            raise KeyError(
                f"Column '{col}' not found. "
                f"Available: {[c for c in df.columns if not c.startswith('19') and not c.startswith('20')]}"
            )
        mask &= df[col].astype(str).str.strip() == val
    out = df[mask].copy()
    print(f"  Rows after filtering: {len(out):,}")
    return out


def filter_countries(df: pd.DataFrame) -> pd.DataFrame:
    print("Filtering country codes …")
    # Bank country: ISO 2-letter only
    rep_ok = df[COL_REP_CTY].astype(str).str.strip().apply(is_iso2)
    # Counterpart: ISO 2-letter OR the World aggregate (5J)
    ctr_ok = df[COL_CP_COUNTRY].astype(str).str.strip().apply(
        lambda c: is_iso2(c) or c == WORLD_CODE
    )
    out = df[rep_ok & ctr_ok].copy()
    print(f"  Rows after country filtering: {len(out):,}")
    return out


def melt_to_long(df: pd.DataFrame) -> pd.DataFrame:
    print("Melting to long format …")
    time_cols = [c for c in df.columns if QUARTER_RE.match(str(c))]
    if not time_cols:
        raise RuntimeError("No quarter columns found (expected format: '2000-Q1').")

    id_cols = [COL_REP_CTY, COL_REP_CTY_LBL, COL_CP_COUNTRY, COL_CP_CTY_LBL]
    long = (
        df[id_cols + time_cols]
        .melt(id_vars=id_cols, value_vars=time_cols,
              var_name="quarter", value_name="amount_usd")
    )
    long["amount_usd"] = pd.to_numeric(long["amount_usd"], errors="coerce")
    long = long.dropna(subset=["amount_usd"]).reset_index(drop=True)
    print(f"  Observations after melting: {len(long):,}")
    return long


def build_output(df: pd.DataFrame) -> pd.DataFrame:
    rep_codes  = df[COL_REP_CTY].astype(str).str.strip()
    rep_names  = df[COL_REP_CTY_LBL].astype(str).str.strip()
    ctr_codes  = df[COL_CP_COUNTRY].astype(str).str.strip()
    ctr_names  = df[COL_CP_CTY_LBL].astype(str).str.strip()

    ctr_mapped = [normalise_counterpart(c, n) for c, n in zip(ctr_codes, ctr_names)]
    ctr_name_out, ctr_iso_out = zip(*ctr_mapped)

    panel = pd.DataFrame({
        "bank_country":        rep_names.values,
        "bank_iso":            rep_codes.values,
        "counterpart_country": list(ctr_name_out),
        "counterpart_iso":     list(ctr_iso_out),
        "quarter":             df["quarter"].values,
        "amount_usd":          df["amount_usd"].values,
    })

    panel = panel.sort_values(
        ["bank_iso", "counterpart_iso", "quarter"]
    ).reset_index(drop=True)

    return panel


def save_outputs(panel: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Saving CSV   → {OUT_CSV}")
    panel.to_csv(OUT_CSV, index=False)

    print(f"Saving Stata → {OUT_DTA}")
    pyreadstat.write_dta(panel, OUT_DTA, version=14)

    print(
        f"\nDone. "
        f"{len(panel):,} observations | "
        f"{panel['bank_iso'].nunique()} bank countries | "
        f"{panel['counterpart_iso'].nunique()} counterpart countries | "
        f"{panel['quarter'].nunique()} quarters"
    )


def main() -> None:
    raw      = load_raw(DATA_FILE)
    filtered = apply_filters(raw)
    countries= filter_countries(filtered)
    long     = melt_to_long(countries)
    panel    = build_output(long)
    save_outputs(panel)


if __name__ == "__main__":
    main()
