import json # Handles reading/writing JSON files
import requests # Makes HTTP requests to fetch data from APIs
import pandas as pd # For working with tabular data
import gspread # Google Sheets API client
import yfinance as yf # Yahoo Finance API client
from google.oauth2.service_account import Credentials # Auth for Google APIs


# Fetch SEC company facts JSON based on ticker
def get_companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f: # Open and load the file that links tickers to SEC CIKs
        data = json.load(f)


    #    "4": {
    #        "cik_str": 1652044,
    #        "ticker": "GOOGL",
    #        "title": "Alphabet Inc."
    #    },


    for v in data.values(): # Loop through every block in the JSON file to find the matching ticker
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10) # Pad CIK to 10 digits for SEC URL format
    
    # Fetch the company's SEC "company facts" JSON file from URL
    return requests.get(
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
        headers={'User-Agent': 'janslavik311@gmail.com'}
    ).json()


# Calculate TTM interest expense (Needed for WACC calculation later)
def calculate_ttm_interest_expense(ticker):
    companyData = get_companyData(ticker) # Fetch company facts JSON

    # Quarterly (Q1–Q3) interest expense is reported directly for 3-month periods
    # Q4 is reported as the full-year (FY) value → so we must subtract Q3 YTD from FY to get Q4-only

    # I need to create lists first, so that I can later turn them into DataFrames
    rows = []
    q4_rows = []

    #    {
    #        "start": "2022-01-01",
    #        "end": "2022-12-31",
    #        "val": 357000000,
    #        "accn": "0001652044-25-000014",
    #        "fy": 2024,
    #        "fp": "FY",
    #        "form": "10-K",
    #        "filed": "2025-02-05",
    #        "frame": "CY2022"
    #    },

    # Loop through each block in the InterestExpenseNonoperating dictionary
    # The blocks are similar to the ones in company_tickers.json
    for entry in companyData['facts']['us-gaap']['InterestExpenseNonoperating']['units']['USD']:
        start, end = pd.to_datetime([entry['start'], entry['end']]) # Format dates so that I can make calculations with them
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

    # Create a DataFrame of yearly (FY) entries
    df = pd.DataFrame(q4_rows)

    # Add a calculated Q4-only row
    rows.append({
        'start': df['end'].iloc[-2],  # start = previous quarter's end
        'end': df['end'].iloc[-1],    # end = FY end date
        'fp': 'Q4',                   # label as Q4
        'val': df[df['fp'] == 'FY']['val'].iloc[-1] - df[df['fp'] == 'Q3']['val'].iloc[-1],     # calculation: FY value minus Q3 YTD value
        'filed': df['filed'].iloc[-1]
    })

    # Rebuild DataFrame with all quarterly entries (Q1–Q4)
    df = pd.DataFrame(rows)
    df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))  # parse dates for operating with them
    df = df.sort_values('filed', ascending=True)  # oldest to newest by filed date
    df = df.drop_duplicates(['start', 'end'], keep='first').sort_values('end', ascending=True)

    # Return sum of last 4 quarters (TTM)
    return df['val'].iloc[-4:].sum()


# Calculate WACC
def wacc(ticker):
    companyData = get_companyData(ticker)

    market_cap = yf.Ticker(ticker).info.get('marketCap')  # Yahoo Finance market cap

    # Debt metrics for calculating Book value of debt
    needed_metrics = [
        'LongTermDebt',
        'OperatingLeaseLiabilityNoncurrent'
    ]
    book_value_of_debt = []
    for metric in needed_metrics:
        book_value_of_debt.append(
            companyData['facts']['us-gaap'][metric]['units']['USD'][-1]['val']
        )

    # Risk-free rate from 10-year Treasury yield
    risk_free_rate = yf.Ticker('^TNX').history(period='1d')['Close'].iloc[-1] / 100
    beta = yf.Ticker(ticker).info.get('beta')  # Yahoo Finance beta
    cost_of_equity = risk_free_rate + beta * (.1 - risk_free_rate)  # CAPM formula

    interest_expense = calculate_ttm_interest_expense(ticker)  # TTM interest expense
    cost_of_debt = interest_expense / sum(book_value_of_debt)  # pre-tax cost of debt
    corporate_tax_rate = .21

    # Return a dictionary of all metrics
    return {
        'market_cap': int(market_cap / 1_000_000),  # millions
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


# -------- Build a DataFrame for each FCF metric --------
def get_metrics_to_calculate_fcf(ticker, metric):
    companyData = get_companyData(ticker)

    rows = []
    for entry in companyData['facts']['us-gaap'][metric]['units']['USD']:
        start, end = pd.to_datetime([entry['start'], entry['end']])
        duration = (end-start).days      
        row = {
            'start': entry['start'],
            'end': entry['end'],
            'fp': entry['fp'],
            'duration': int(duration),
            'val': entry['val'] / 1_000_000,  # convert to millions
            'filed': entry['filed']
        }
        rows.append(row)

    # Build DF, sort by filing date, drop duplicates, then sort by end date
    df = pd.DataFrame(rows)
    df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))
    df = df.sort_values('filed', ascending=False)
    df = df.drop_duplicates(['start', 'end'], keep='first') \
           .sort_values('end', ascending=True) \
           .drop(columns=['start', 'filed'])

    # Rename 'val' to metric name for clarity
    df = df.rename(columns={'val': metric})

    # Adjust values: for Q1 keep as-is, for Q2–Q4 subtract previous quarter
    rows = []
    for pos in range(len(df)):
        if df['fp'].iloc[pos] == 'Q1':
            row = {
                'fp': df['fp'].iloc[pos],
                'end': df['end'].iloc[pos],
                metric: df[metric].iloc[pos]
            }
        else:
            val = df[metric].iloc[pos] - df[metric].iloc[pos-1]
            row = {
                'fp': 'Q4' if df['fp'].iloc[pos] == 'FY' else df['fp'].iloc[pos],
                'end': df['end'].iloc[pos],
                metric: val
            }
        rows.append(row)

    return pd.DataFrame(rows)


# -------- Combine FCF metrics into one DF --------
def get_and_plot_fcf(ticker):
    metrics = [
        'NetCashProvidedByUsedInOperatingActivities',
        'PaymentsToAcquirePropertyPlantAndEquipment'
    ]

    df = pd.DataFrame()
    for metric in metrics:
        df = pd.merge(df, get_metrics_to_calculate_fcf(ticker, metric), on=['fp'], how='outer')

    print(df)


# -------- Placeholder for FCF forecast --------
def fcf_forecast():
    growth_timespan = int(input('Enter the growth timespan (in years): '))


# -------- Write WACC + forecast to Google Sheet --------
def spreadsheet(ticker):
    sheet_name = 'Untitled spreadsheet'
    credentials_file = '/home/mo-lester/Documents/6-7 Project/service-account.json'
    
    # Auth scopes for Sheets + Drive
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    gc = gspread.authorize(credentials)

    spreadsheet = gc.open(sheet_name)     # open spreadsheet by name
    worksheet = spreadsheet.sheet1        # first sheet

    worksheet.clear()                     # wipe existing data

    # Write WACC dictionary to columns A and B
    pos = 1
    for cell in wacc(ticker):
        worksheet.update(f'A{pos}', [[cell]])
        worksheet.update(f'B{pos}', [[wacc(ticker)[cell]]])
        pos += 1

    # Auto-resize column A
    worksheet.spreadsheet.batch_update({
        "requests": [
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": worksheet.id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 1
                    }
                }
            }
        ]
    })

    # Example: small DataFrame of revenue forecast
    df = pd.DataFrame({
        'Year': [2024, 2025, 2026],
        'Revenue': [350018, 402521, 462899]
    })

    # Write forecast starting at cell C1
    worksheet.update('C1', [df.columns.values.tolist()] + df.values.tolist())


# -------- Main entry point --------
def run():
    ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()
    get_and_plot_fcf(ticker)


run()
