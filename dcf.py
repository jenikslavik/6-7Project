import json
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import yfinance as yf


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
        'market_cap': int(market_cap / 1_000_000),
        'long_term_debt': book_value_of_debt[0] / 1_000_000,
        'operating_lease_liabilities': book_value_of_debt[1] / 1_000_000,
        'book_value_of_debt': sum(book_value_of_debt) / 1_000_000,
        'risk_free_rate': risk_free_rate,
        'beta': beta,
        'cost_of_equity': cost_of_equity,
        'interest_expense': interest_expense / 1_000_000,
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
    month = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}[q]  # use end month of quarter
    return pd.Timestamp(year=int(year), month=month, day=1)


# --- Add/replace below ---

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

    # Build CFOA
    cfo_candidates = [c for c in CANDIDATES["cfoa"] if _concept_exists(companyData, c)]
    cfo_series = [_quarterize_series(companyData, c, "cfoa") for c in cfo_candidates]
    cfoa = _stitch_first_non_null([df for df in cfo_series if not df.empty], "cfoa")

    # Build CapEx
    capex_candidates = [c for c in CANDIDATES["capex"] if _concept_exists(companyData, c)]
    capex_series = [_quarterize_series(companyData, c, "capex") for c in capex_candidates]
    capex = _stitch_first_non_null([df for df in capex_series if not df.empty], "capex")

    # Make a full quarter calendar spanning both series
    if cfoa.empty and capex.empty:
        raise ValueError("No CFOA or CapEx data found for this filer.")

    frames = [d for d in [cfoa, capex] if not d.empty]
    allq = pd.concat(frames)["quarter"].unique().tolist()
    tmp = pd.DataFrame({"quarter": allq})
    tmp2 = tmp["quarter"].str.extract(r"Q(?P<q>\d)\s+(?P<y>\d{4})").astype(int)
    calendar = (tmp.assign(_y=tmp2["y"], _q=tmp2["q"])
                    .sort_values(["_y","_q"])
                    .drop(columns=["_y","_q"])
                    .reset_index(drop=True))

    # Merge & compute FCF
    combined = (calendar
                .merge(cfoa, on="quarter", how="left")
                .merge(capex, on="quarter", how="left"))

    # Normalize CapEx sign to positive spend
    combined["capex"] = _normalize_capex_sign(combined["capex"])

    combined["fcf"] = combined["cfoa"].fillna(0) - combined["capex"].fillna(0)

    # (Optional) plot, keeping your quarterly-label rule
    plot_df = combined[["quarter", "fcf"]].copy()
    ax = plot_df.plot(x="quarter", y="fcf", kind="bar")
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e9:.0f}B'))
    labels = [t.get_text() if "Q4" in t.get_text() else "" for t in ax.get_xticklabels()]
    ax.set_xticklabels(labels)
    plt.show()

    return combined




ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()
fcf(ticker)
