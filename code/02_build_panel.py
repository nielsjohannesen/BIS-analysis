"""
Build a bilateral panel dataset from BIS Locational Banking Statistics.

Filters:
  - Position: liabilities (BALANCE_SHEET_POSITION = L)
  - Counterpart sector: non-bank (COUNTERPART_SECTOR = N)
  - Currency: all currencies, USD-converted (CURRENCY = TO1)
  - Instrument: all (TYPE_INSTRUMENT = A)
  - Remaining maturity: all (REM_MATURITY = A)
  - Measure: amounts outstanding / stocks (MEASURE = B)

Output columns:
  bank_country      | reporting bank country name
  bank_iso          | reporting country ISO 2-letter code
  counterpart_country | counterpart country name
  counterpart_iso   | counterpart country ISO 2-letter code
  quarter           | e.g. "2023-Q4"
  amount_usd        | USD millions (as reported by BIS)

Country filtering:
  Keep only true country observations (2-letter ISO-style codes).
  Drop regional aggregates (Africa, Offshore centres, etc.).
  Exception: keep BIS code "1W" (World) as counterpart_iso = "WLD".

Outputs:
  output/lbs_bilateral_panel.csv
  output/lbs_bilateral_panel.dta  (Stata 14 format)
"""

from pathlib import Path

import pandas as pd
import pycountry
import pyreadstat

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "LBS_D_PUB.csv"
OUT_DIR = ROOT / "output"
OUT_CSV = OUT_DIR / "lbs_bilateral_panel.csv"
OUT_DTA = OUT_DIR / "lbs_bilateral_panel.dta"

# ---------------------------------------------------------------------------
# BIS dimension column names (column-format CSV)
# These are the header names used in the BIS bulk CSV.
# Adjust if the file uses slightly different names.
# ---------------------------------------------------------------------------

DIM_FREQ = "FREQ"
DIM_MEASURE = "MEASURE"
DIM_POSITION = "BALANCE_SHEET_POSITION"
DIM_PARENT = "PARENT_CTY"
DIM_CURRENCY = "CURRENCY"
DIM_REP_CTY = "REP_CTY"          # reporting / bank country
DIM_SECTOR = "COUNTERPART_SECTOR"
DIM_CTR_AREA = "COUNTERPART_AREA"
DIM_INSTRUMENT = "TYPE_INSTRUMENT"
DIM_MATURITY = "REM_MATURITY"

# Target filter values
FILTERS = {
    DIM_FREQ: "Q",
    DIM_MEASURE: "B",
    DIM_POSITION: "L",
    DIM_CURRENCY: "TO1",
    DIM_INSTRUMENT: "A",
    DIM_MATURITY: "A",
    DIM_SECTOR: "N",
}

# BIS "World" aggregate code → we map it to iso "WLD"
BIS_WORLD_CODE = "1W"
WORLD_NAME = "World"

# ---------------------------------------------------------------------------
# Country helpers
# ---------------------------------------------------------------------------

def build_iso_lookup() -> dict[str, str]:
    """Return {iso2_code: country_name} for all ISO 3166-1 alpha-2 countries."""
    return {c.alpha_2: c.name for c in pycountry.countries}


def is_country_code(code: str, iso_lookup: dict) -> bool:
    """True if code is a 2-letter ISO country code or our World sentinel."""
    return code in iso_lookup or code == BIS_WORLD_CODE


def label_country(code: str, iso_lookup: dict) -> tuple[str, str]:
    """Return (name, iso) for a BIS country code."""
    if code == BIS_WORLD_CODE:
        return WORLD_NAME, "WLD"
    name = iso_lookup.get(code, code)   # fall back to code if unknown
    return name, code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_raw(path: Path) -> pd.DataFrame:
    print(f"Loading raw data from: {path}")
    # BIS CSVs often have a multi-line header; skip rows until we hit the
    # dimension columns. We detect the header by looking for the FREQ column.
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if DIM_FREQ in line:
                header_row = i
                break
        else:
            raise RuntimeError(
                f"Could not find header row containing '{DIM_FREQ}' in {path}.\n"
                "Check that the file is the BIS LBS column-format CSV."
            )

    df = pd.read_csv(path, skiprows=header_row, low_memory=False)
    print(f"  Raw shape: {df.shape}")
    return df


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    print("Applying dimension filters …")
    mask = pd.Series(True, index=df.index)
    for col, val in FILTERS.items():
        if col not in df.columns:
            raise KeyError(
                f"Expected dimension column '{col}' not found.\n"
                f"Available columns: {list(df.columns[:30])}"
            )
        mask &= df[col].astype(str).str.strip() == val
    filtered = df[mask].copy()
    print(f"  Rows after filtering: {len(filtered):,}")
    return filtered


def melt_to_long(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot from wide (one column per quarter) to long format."""
    print("Melting to long format …")
    dim_cols = [
        DIM_FREQ, DIM_MEASURE, DIM_POSITION, DIM_PARENT,
        DIM_CURRENCY, DIM_REP_CTY, DIM_SECTOR, DIM_CTR_AREA,
        DIM_INSTRUMENT, DIM_MATURITY,
    ]
    # Time columns look like "2000-Q1", "2000-Q2", …
    time_cols = [c for c in df.columns if c not in dim_cols and "Q" in str(c)]
    if not time_cols:
        raise RuntimeError(
            "No quarter columns found (expected format: '2000-Q1').\n"
            f"Non-dimension columns: {[c for c in df.columns if c not in dim_cols][:20]}"
        )

    id_cols = [c for c in dim_cols if c in df.columns]
    long = df[id_cols + time_cols].melt(
        id_vars=id_cols,
        value_vars=time_cols,
        var_name="quarter",
        value_name="amount_usd",
    )
    # Drop missing observations
    long = long.dropna(subset=["amount_usd"])
    long = long[long["amount_usd"].astype(str).str.strip() != ""]
    long["amount_usd"] = pd.to_numeric(long["amount_usd"], errors="coerce")
    long = long.dropna(subset=["amount_usd"])
    print(f"  Observations after melting: {len(long):,}")
    return long


def filter_countries(df: pd.DataFrame, iso_lookup: dict) -> pd.DataFrame:
    """Drop regional aggregates; keep true countries + World."""
    print("Filtering country codes …")
    rep_ok = df[DIM_REP_CTY].apply(lambda c: is_country_code(str(c).strip(), iso_lookup))
    ctr_ok = df[DIM_CTR_AREA].apply(lambda c: is_country_code(str(c).strip(), iso_lookup))
    out = df[rep_ok & ctr_ok].copy()
    print(f"  Observations after country filtering: {len(out):,}")
    return out


def build_output(df: pd.DataFrame, iso_lookup: dict) -> pd.DataFrame:
    """Construct the final panel with clean column names."""
    df = df.copy()

    bank_labels = df[DIM_REP_CTY].apply(
        lambda c: pd.Series(label_country(str(c).strip(), iso_lookup),
                            index=["bank_country", "bank_iso"])
    )
    ctr_labels = df[DIM_CTR_AREA].apply(
        lambda c: pd.Series(label_country(str(c).strip(), iso_lookup),
                            index=["counterpart_country", "counterpart_iso"])
    )

    panel = pd.DataFrame({
        "bank_country": bank_labels["bank_country"].values,
        "bank_iso": bank_labels["bank_iso"].values,
        "counterpart_country": ctr_labels["counterpart_country"].values,
        "counterpart_iso": ctr_labels["counterpart_iso"].values,
        "quarter": df["quarter"].values,
        "amount_usd": df["amount_usd"].values,
    })

    panel = panel.sort_values(
        ["bank_iso", "counterpart_iso", "quarter"]
    ).reset_index(drop=True)

    return panel


def save_outputs(panel: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Saving CSV  → {OUT_CSV}")
    panel.to_csv(OUT_CSV, index=False)

    print(f"Saving Stata → {OUT_DTA}")
    pyreadstat.write_dta(panel, OUT_DTA, version=14)

    print(f"\nDone. Final panel: {len(panel):,} observations, "
          f"{panel['bank_iso'].nunique()} bank countries, "
          f"{panel['counterpart_iso'].nunique()} counterpart countries, "
          f"{panel['quarter'].nunique()} quarters.")


def main() -> None:
    iso_lookup = build_iso_lookup()

    raw = load_raw(DATA_FILE)
    filtered = apply_filters(raw)
    long = melt_to_long(filtered)
    countries = filter_countries(long, iso_lookup)
    panel = build_output(countries, iso_lookup)
    save_outputs(panel)


if __name__ == "__main__":
    main()
