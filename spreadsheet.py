# spreadsheet.py
import json
import requests
import gspread
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from google.oauth2.service_account import Credentials


# ---------- Config ----------
CSV_PATH = '/home/mo-lester/Documents/6-7 Project/income_output.csv'
SHEET_NAME = 'Untitled spreadsheet'   # change if you want
CREDENTIALS_FILE = '/home/mo-lester/Documents/6-7 Project/service-account.json'
COMPANY_TICKERS_JSON = '/home/mo-lester/Documents/6-7 Project/company_tickers.json'


# ---------- Data access ----------
def get_companyData(ticker: str) -> dict:
    with open(COMPANY_TICKERS_JSON, 'r') as f:
        data = json.load(f)
    cik = None
    for v in data.values():
        if v.get('ticker') == ticker:
            cik = str(v['cik_str']).zfill(10)
            break
    if not cik:
        raise ValueError(f"CIK not found for ticker {ticker}.")
    return requests.get(
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
        headers={'User-Agent': 'janslavik311@gmail.com'}
    ).json()


# ---------- Helpers ----------
def assign_quarter(row):
    m, y = row['end'].month, row['end'].year
    q = 'Q1' if m in (1,2,3) else 'Q2' if m in (4,5,6) else 'Q3' if m in (7,8,9) else 'Q4'
    return f"{q} {y}"

def quarter_to_date(quarter_str: str) -> pd.Timestamp:
    q, year = quarter_str.split()
    month = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}[q]
    return pd.Timestamp(year=int(year), month=month, day=1) + pd.offsets.MonthEnd(0)

def _concept_exists(companyData: dict, concept: str) -> bool:
    try:
        return "USD" in companyData["facts"]["us-gaap"][concept]["units"]
    except KeyError:
        return False

def _quarterize_series(companyData: dict, concept: str, colname: str) -> pd.DataFrame:
    """Return DataFrame ['quarter', colname] as TRUE quarterly values."""
    rows = []
    for entry in companyData["facts"]["us-gaap"][concept]["units"]["USD"]:
        start, end = pd.to_datetime([entry["start"], entry["end"]])
        duration = (end - start).days
        rows.append({
            "start": start, "end": end,
            "filed": pd.to_datetime(entry.get("filed", end)),
            "fp": entry.get("fp", ""), "duration": duration,
            colname: entry["val"]
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["quarter", colname])

    df = (df.sort_values("filed")
            .drop_duplicates(["start","end"], keep="first")
            .sort_values("end")
            .reset_index(drop=True))

    # drop partial quarters (<95d) except legit Q1
    df = df[~((df["duration"] < 95) & (df["fp"] != "Q1"))].reset_index(drop=True)

    # YTD → quarterly delta
    out = []
    for i in range(len(df)):
        if i == 0 or (df.iloc[i]["end"] - df.iloc[i-1]["end"]).days < 85:
            continue
        qval = df.iloc[i][colname] if df.iloc[i]["fp"] == "Q1" else df.iloc[i][colname] - df.iloc[i-1][colname]
        out.append({"end": df.iloc[i]["end"], colname: qval})

    dfq = pd.DataFrame(out)
    if dfq.empty:
        return pd.DataFrame(columns=["quarter", colname])

    dfq["quarter"] = dfq.apply(assign_quarter, axis=1)
    return dfq[["quarter", colname]]

def _stitch_first_non_null(dflist: list[pd.DataFrame], colname: str) -> pd.DataFrame:
    if not dflist:
        return pd.DataFrame(columns=["quarter", colname])
    dflist = sorted(dflist, key=lambda d: d[colname].notna().sum(), reverse=True)
    merged = dflist[0].copy()
    for nxt in dflist[1:]:
        merged = merged.merge(nxt, on="quarter", how="outer", suffixes=("", "_alt"))
        merged[colname] = merged[colname].combine_first(merged[f"{colname}_alt"])
        merged.drop(columns=[c for c in merged.columns if c.endswith("_alt")], inplace=True)
    tmp = merged["quarter"].str.extract(r"Q(?P<q>\d)\s+(?P<y>\d{4})").astype(int)
    return (merged.assign(_y=tmp["y"], _q=tmp["q"])
                  .sort_values(["_y","_q"])
                  .drop(columns=["_y","_q"])
                  .reset_index(drop=True))


# ---------- Metric builders ----------
INCOME_CANDIDATES = {
    "gross_profit": [
        "GrossProfit", "GrossProfitLoss"
    ],
    "operating_income": [
        "OperatingIncomeLoss", "OperatingIncome"
    ],
    "net_income": [
        "NetIncomeLoss", "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic"
    ],
}

def build_quarterly_income(ticker: str) -> pd.DataFrame:
    companyData = get_companyData(ticker)

    cols = []
    for colname, concepts in INCOME_CANDIDATES.items():
        candidates = [c for c in concepts if _concept_exists(companyData, c)]
        series = [_quarterize_series(companyData, c, colname) for c in candidates]
        stitched = _stitch_first_non_null([d for d in series if not d.empty], colname)
        cols.append(stitched)

    # calendar across all three
    qlabels = pd.Index([])
    for df in cols:
        if not df.empty: qlabels = qlabels.union(df["quarter"])
    if qlabels.empty:
        raise ValueError("No quarterly income data found.")

    q_end = [quarter_to_date(q) for q in qlabels]
    pr = pd.period_range(start=min(q_end), end=max(q_end), freq="Q-DEC")
    calendar = pd.DataFrame({"quarter": [f"Q{p.quarter} {p.year}" for p in pr]})

    combined = calendar.copy()
    for df in cols:
        combined = combined.merge(df, on="quarter", how="left")

    # Save CSV for your Sheets flow
    combined.to_csv(CSV_PATH, index=False)
    return combined


# ---------- Plotting (same style as dcf.py, but KEEP blank quarters) ----------
def plot_metric(df: pd.DataFrame, ticker: str, metric: str):
    if metric not in ("gross_profit", "operating_income", "net_income"):
        raise ValueError("metric must be one of: gross_profit, operating_income, net_income")

    # KEEP NaNs so blank quarters show up as empty slots (like in dcf.py)
    plot_df = df[["quarter", metric]].copy()

    ax = plot_df.plot(x="quarter", y=metric, kind="bar")

    # Decide y-axis scale: millions if under ~2B, else billions (robust to NaNs)
    values = plot_df[metric].to_numpy(dtype=float)
    y_max = np.nanmax(np.abs(values)) if values.size else 0.0
    if y_max < 2e9:
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e6:.0f}M'))
    else:
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e9:.0f}B'))

    # Build centered year ticks for COMPLETE years and vertical guides after each Q4
    year_to_idxs, q4_positions = {}, []
    for i, q in enumerate(plot_df["quarter"]):
        qtr, yr = q.split(); yr = int(yr)
        year_to_idxs.setdefault(yr, []).append(i)
        if qtr == "Q4": q4_positions.append(i)

    mid_ticks, mid_labels = [], []
    for yr, idxs in sorted(year_to_idxs.items()):
        if len(idxs) == 4:  # label only full years
            left, right = min(idxs), max(idxs)
            mid = (left + right) / 2.0
            mid_ticks.append(mid)
            mid_labels.append(f"{yr % 100:02d}")  # two-digit year, no apostrophe

    ax.set_xticks(mid_ticks)
    ax.set_xticklabels(mid_labels, rotation=0, ha='center')
    ax.tick_params(axis='x', which='both', length=0)

    ax.set_axisbelow(True)
    ax.yaxis.grid(True, which='major', linestyle='--', linewidth=0.8, color='lightgray')
    for pos in q4_positions:
        ax.axvline(pos + 0.5, color='lightgray', linestyle='--', linewidth=0.8, zorder=0)

    pretty = {
        "gross_profit": "Gross Profit",
        "operating_income": "Operating Income",
        "net_income": "Net Income"
    }[metric]
    plt.title(f'{pretty} ({ticker})')
    plt.tight_layout()
    plt.show()


# ---------- Google Sheets upload ----------
def upload_to_sheets(csv_path: str, sheet_name: str, tab_title: str | None = None):
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(credentials)

    # Open/create spreadsheet
    try:
        ss = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        ss = gc.create(sheet_name)

    ws = ss.sheet1 if not tab_title else (
        ss.worksheet(tab_title) if tab_title in [w.title for w in ss.worksheets()]
        else ss.add_worksheet(title=tab_title, rows=1, cols=1)
    )

    df = pd.read_csv(csv_path)
    upload_df = df.astype(object).where(pd.notnull(df), '')
    ws.clear()
    ws.update([upload_df.columns.tolist()] + upload_df.values.tolist())


# ---------- CLI ----------
if __name__ == "__main__":
    ticker = input("Enter stock ticker symbol (e.g., AAPL, MSFT): ").upper()
    metric = input("Choose metric [gross_profit | operating_income | net_income]: ").strip().lower()

    data = build_quarterly_income(ticker)

    # plot the chosen metric
    plot_metric(data, ticker, metric)

    # optional upload
    do_upload = input("Upload to Google Sheets? [y/N]: ").strip().lower() == 'y'
    if do_upload:
        upload_to_sheets(CSV_PATH, SHEET_NAME, tab_title=f"Income - {ticker}")
        print(f"Uploaded to '{SHEET_NAME}' → tab 'Income - {ticker}'.")
    print(f"CSV saved to: {CSV_PATH}")
