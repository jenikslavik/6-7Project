import json
import requests
import gspread
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from google.oauth2.service_account import Credentials


def get_companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f:
        data = json.load(f)

    for v in data.values():
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10)
    
    return requests.get(
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
        headers={'User-Agent': 'janslavik311@gmail.com'}
    ).json()


def ttm_interest_expense(ticker):
    companyData = get_companyData(ticker)

    rows = []
    q4_rows = []

    for entry in companyData['facts']['us-gaap']['InterestExpenseNonoperating']['units']['USD']:
        start, end = pd.to_datetime([entry['start'], entry['end']])
        duration = (end - start).days

        # Identify quarterly reports (approx. 3 months)
        if 85 <= duration <= 95:
            row = {
                'start': entry['start'],
                'end': entry['end'],
                'fp': entry['fp'],
                'val': entry['val'],
                'filed': entry['filed']
            }
            rows.append(row)

        # Identify yearly reports (approx. 12 months)
        if 270 <= duration <= 370:
            row = {
                'start': entry['start'],
                'end': entry['end'],
                'fp': entry['fp'],
                'val': entry['val'],
                'filed': entry['filed']
            }
            q4_rows.append(row)

    df = pd.DataFrame(q4_rows)


    rows.append({
        'start': df['end'].iloc[-2],
        'end': df['end'].iloc[-1],
        'fp': 'Q4',
        'val': df[df['fp'] == 'FY']['val'].iloc[-1] - df[df['fp'] == 'Q3']['val'].iloc[-1],
        'filed': df['filed'].iloc[-1]
    })

    # Rebuild DataFrame with all quarterly entries (Q1–Q4)
    df = pd.DataFrame(rows)
    df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))
    df = df.sort_values('filed', ascending=True)
    df = df.drop_duplicates(['start', 'end'], keep='first').sort_values('end', ascending=True)

    return df['val'].iloc[-4:].sum()


def wacc(ticker):
    companyData = get_companyData(ticker)

    market_cap = yf.Ticker(ticker).info.get('marketCap')

    needed_metrics = [
        'LongTermDebt',
        'OperatingLeaseLiabilityNoncurrent'
    ]
    book_value_of_debt = []
    for metric in needed_metrics:
        book_value_of_debt.append(
            companyData['facts']['us-gaap'][metric]['units']['USD'][-1]['val']
        )

    risk_free_rate = yf.Ticker('^TNX').history(period='1d')['Close'].iloc[-1] / 100
    beta = yf.Ticker(ticker).info.get('beta')
    cost_of_equity = risk_free_rate + beta * (.1 - risk_free_rate)

    interest_expense = ttm_interest_expense(ticker)
    cost_of_debt = interest_expense / sum(book_value_of_debt)
    corporate_tax_rate = .21

    return {
        'market_cap': market_cap / 1e6,
        'long_term_debt': book_value_of_debt[0] / 1e6,
        'operating_lease_liabilities': book_value_of_debt[1] / 1e6,
        'book_value_of_debt': sum(book_value_of_debt) / 1e6,
        'risk_free_rate': risk_free_rate,
        'beta': beta,
        'cost_of_equity': cost_of_equity,
        'interest_expense': interest_expense / 1e6,
        'cost_of_debt': cost_of_debt,
        'wacc': (
            (market_cap / (market_cap + sum(book_value_of_debt))) * cost_of_equity
            + (sum(book_value_of_debt) / (market_cap + sum(book_value_of_debt)))
              * cost_of_debt * (1 - corporate_tax_rate)
        )
    }




def assign_quarter(row):
    month = row['end'].month
    year = row['end'].year

    if month in [1, 2, 3]:
        quarter = 'Q1'
    elif month in [4, 5, 6]:
        quarter = 'Q2'
    elif month in [7, 8, 9]:
        quarter = 'Q3'
    else:
        quarter = 'Q4'

    return f"{quarter} {year}"


def quarter_to_date(quarter_str):
    q, year = quarter_str.split()
    month = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}[q]
    return pd.Timestamp(year=int(year), month=month, day=1) + pd.offsets.MonthEnd(0)


CANDIDATES = {
    "cfoa": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "CashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesDomesticOperations",
    ],
    "capex": [
        # Common
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForPropertyPlantAndEquipment",
        # Variants seen in older/newer taxonomies
        "PaymentsToAcquireProductiveAssets",
        "CapitalExpenditures",
        "PurchaseOfPropertyAndEquipment",
        "AcquisitionOfPropertyPlantAndEquipment",
        "PaymentsToAcquireFixedAssets",
        "PaymentsForCapitalExpenditures",
        # Some companies bundle PP&E + intangibles; we still prefer coverage
        "CapitalExpendituresFixedAssetsIntangibleAssets",
    ],
}


def _concept_exists(companyData, concept):
    try:
        return "USD" in companyData["facts"]["us-gaap"][concept]["units"]
    except KeyError:
        return False


def _quarterize_series(companyData, concept, colname):
    """Return DataFrame: ['quarter', colname], true quarterly, deduped & sorted."""
    rows = []
    for entry in companyData["facts"]["us-gaap"][concept]["units"]["USD"]:
        start, end = pd.to_datetime([entry["start"], entry["end"]])
        duration = (end - start).days
        rows.append({
            "start": start, "end": end, "filed": pd.to_datetime(entry.get("filed", end)),
            "fp": entry.get("fp", ""), "duration": duration, colname: entry["val"]
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["quarter", colname])

    # Keep first file for a given period, sort chronologically
    df = (
        df.sort_values("filed")
          .drop_duplicates(["start", "end"], keep="first")
          .sort_values("end")
    )

    # Remove partial quarters (keep legit Q1 if filer uses odd YTD)
    # Your rule: drop <95d unless Q1 — keep it
    mask_keep = ~((df["duration"] < 95) & (df["fp"] != "Q1"))
    df = df[mask_keep].reset_index(drop=True)

    # Convert YTD to quarterly deltas
    out = []
    for i in range(len(df)):
        if i == 0 or (df.iloc[i]["end"] - df.iloc[i-1]["end"]).days < 85:
            continue  # skip anomalous first/close points
        if df.iloc[i]["fp"] == "Q1":
            q_val = df.iloc[i][colname]
        else:
            q_val = df.iloc[i][colname] - df.iloc[i-1][colname]
        out.append({"end": df.iloc[i]["end"], colname: q_val})

    dfq = pd.DataFrame(out)
    if dfq.empty:
        return pd.DataFrame(columns=["quarter", colname])

    dfq["quarter"] = dfq.apply(assign_quarter, axis=1)
    return dfq[["quarter", colname]]


def _stitch_first_non_null(dflist, colname):
    """Merge multiple quarterly series into one by taking first non-null by priority."""
    if not dflist:
        return pd.DataFrame(columns=["quarter", colname])

    # Order by coverage (more data first)
    dflist = sorted(dflist, key=lambda d: d[colname].notna().sum(), reverse=True)

    merged = dflist[0].copy()
    for nxt in dflist[1:]:
        merged = merged.merge(nxt, on="quarter", how="outer", suffixes=("", "_alt"))
        # choose first non-null among [colname, colname_alt]
        merged[colname] = merged[colname].combine_first(merged[f"{colname}_alt"])
        merged.drop(columns=[c for c in merged.columns if c.endswith("_alt")], inplace=True)

    # Keep unique, sorted by real time
    tmp = merged["quarter"].str.extract(r"Q(?P<q>\d)\s+(?P<y>\d{4})").astype(int)
    return (merged.assign(_y=tmp["y"], _q=tmp["q"])
                  .sort_values(["_y","_q"])
                  .drop(columns=["_y","_q"])
                  .reset_index(drop=True))


def _normalize_capex_sign(series):
    """Return CapEx as positive cash outflow."""
    if series.dropna().median() < 0:
        return -series  # make it positive spend
    return series


def fcf(ticker):
    companyData = get_companyData(ticker)

    # ----- Build CFOA -----
    cfo_candidates = [c for c in CANDIDATES["cfoa"] if _concept_exists(companyData, c)]
    cfo_series = [_quarterize_series(companyData, c, "cfoa") for c in cfo_candidates]
    cfoa = _stitch_first_non_null([df for df in cfo_series if not df.empty], "cfoa")

    # ----- Build CapEx -----
    capex_candidates = [c for c in CANDIDATES["capex"] if _concept_exists(companyData, c)]
    capex_series = [_quarterize_series(companyData, c, "capex") for c in capex_candidates]
    capex = _stitch_first_non_null([df for df in capex_series if not df.empty], "capex")

    if cfoa.empty and capex.empty:
        raise ValueError("No CFOA or CapEx data found for this filer.")

    # ----- Continuous quarter calendar -----
    quarter_labels = pd.Index([])
    for df in [cfoa, capex]:
        if not df.empty:
            quarter_labels = quarter_labels.union(df["quarter"])

    q_end_dates = [quarter_to_date(q) for q in quarter_labels]
    start, end = min(q_end_dates), max(q_end_dates)

    pr = pd.period_range(start=start, end=end, freq="Q-DEC")
    calendar = pd.DataFrame({"quarter": [f"Q{p.quarter} {p.year}" for p in pr]})

    # ----- Merge & compute FCF -----
    combined = (
        calendar
        .merge(cfoa, on="quarter", how="left")
        .merge(capex, on="quarter", how="left")
    )

    # Normalize CapEx sign only where present
    if combined["capex"].notna().any():
        combined.loc[combined["capex"].notna(), "capex"] = _normalize_capex_sign(combined["capex"])

    # FCF = blank if either CFOA or CapEx is blank
    combined["fcf"] = np.where(
        combined["cfoa"].isna() | combined["capex"].isna(),
        np.nan,
        combined["cfoa"] - combined["capex"]
    )

    # ----- Save CSV -----
    combined.to_csv('/home/mo-lester/Documents/6-7 Project/output.csv', index=False)

    # ----- Plot -----
    plot_df = combined[["quarter", "fcf"]].copy()
    ax = plot_df.plot(x="quarter", y="fcf", kind="bar")

    # Decide y-axis scale: billions if >5B, else millions
    y_max = plot_df["fcf"].abs().max()
    if y_max < 5e9:
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e6:.0f}M'))
    else:
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e9:.0f}B'))

    # Collect indices per year and Q4 positions
    year_to_idxs = {}
    q4_positions = []
    for i, q in enumerate(plot_df["quarter"]):
        qtr, yr = q.split()
        yr = int(yr)
        year_to_idxs.setdefault(yr, []).append(i)
        if qtr == "Q4":
            q4_positions.append(i)

    # Midpoint tick for each COMPLETE year (center between Q2 & Q3)
    mid_ticks, mid_labels = [], []
    for yr, idxs in sorted(year_to_idxs.items()):
        if len(idxs) == 4:  # only label full years
            left, right = min(idxs), max(idxs)
            mid = (left + right) / 2.0
            mid_ticks.append(mid)
            mid_labels.append(f"{yr % 100:02d}")  # two-digit year, no apostrophe

    # Replace default bar ticks with our centered year ticks; remove tick marks
    ax.set_xticks(mid_ticks)
    ax.set_xticklabels(mid_labels, rotation=0, ha='center')
    ax.tick_params(axis='x', which='both', length=0)  # hide short tick marks

    # Grid/lines behind bars
    ax.set_axisbelow(True)

    # Horizontal dashed gridlines (y-axis)
    ax.yaxis.grid(True, which='major', linestyle='--', linewidth=0.8, color='lightgray')

    # Vertical dashed guides after each Q4
    for pos in q4_positions:
        ax.axvline(pos + 0.5, color='lightgray', linestyle='--', linewidth=0.8, zorder=0)

    plt.title(f'Free cash flow ({ticker})')
    plt.tight_layout()
    plt.show()

    # return combined.to_csv('/home/mo-lester/Documents/6-7 Project/output.csv', index=False)




def spreadsheet():
    csv_file = '/home/mo-lester/Documents/6-7 Project/output.csv'
    sheet_name = 'Untitled spreadsheet'
    credentials_file = '/home/mo-lester/Documents/6-7 Project/service-account.json'

    # Auth
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    gc = gspread.authorize(credentials)

    # Open (or create) the spreadsheet
    try:
        ss = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        ss = gc.create(sheet_name)
        # If needed: share the sheet so you can see it in Drive
        # ss.share('your.email@example.com', perm_type='user', role='writer')

    ws = ss.sheet1

    # Read the CSV and convert NaN -> '' ONLY for upload (Sheets/JSON can't handle NaN)
    df = pd.read_csv(csv_file)
    upload_df = df.astype(object).where(pd.notnull(df), '')

    # Push header + data
    ws.clear()
    ws.update([upload_df.columns.tolist()] + upload_df.values.tolist())


ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()
fcf(ticker)
