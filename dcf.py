import json
import requests
import pandas as pd
import gspread
import yfinance as yf
from google.oauth2.service_account import Credentials


def get_companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f: # get cik based on ticker from company_tickers.json
        data = json.load(f)
    
    for v in data.values():
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10) # format the ticker so that it has 10 digits (needed for the url)
    
    return requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers={'User-Agent': 'janslavik311@gmail.com'}).json() # get the json data file for the company (https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json try this link for google)


def calculate_ttm_interest_expense(ticker): # we need to calculate ttm interest expense
    companyData = get_companyData(ticker) # call the json data file

    '''this is an output for the loop for entry in companyData['facts']['us-gaap']['InterestExpenseNonoperating']['units']['USD'] (this is just shorter version, theres smthing around 100 of these blocks)
        we need to get the "val" figure, q1-q3 are being reported just fine, they report the val (value) for the 3 months but for q4, they only report the value for the whole year, no 3 month value, so we need to subtract the ytd q3 figure (9 months) from the q4 (12 months) figure
    {
        "start": "2022-01-01",
        "end": "2022-12-31",
        "val": 357000000,
        "accn": "0001652044-25-000014",
        "fy": 2024,
        "fp": "FY",
        "form": "10-K",
        "filed": "2025-02-05",
        "frame": "CY2022"
    },
    {
        "start": "2023-01-01",
        "end": "2023-06-30",
        "val": 123000000,
        "accn": "0001652044-24-000079",
        "fy": 2024,
        "fp": "Q2",
        "form": "10-Q",
        "filed": "2024-07-24"
    },
    '''

    rows = []
    q4_rows = []
    for entry in companyData['facts']['us-gaap']['InterestExpenseNonoperating']['units']['USD']:
        start, end = pd.to_datetime([entry['start'], entry['end']])
        duration = (end-start).days
        if 85 <= duration <= 95:
            row = {
                'start': entry['start'],
                'end': entry['end'],
                'fp': entry['fp'],
                'val': entry['val'],
                'filed': entry['filed']
            }
            rows.append(row)
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
        book_value_of_debt.append(companyData['facts']['us-gaap'][metric]['units']['USD'][-1]['val'])

    risk_free_rate = yf.Ticker('^TNX').history(period='1d')['Close'].iloc[-1] / 100
    beta = yf.Ticker(ticker).info.get('beta')
    cost_of_equity = risk_free_rate + beta * (.1 - risk_free_rate)

    interest_expense = calculate_ttm_interest_expense(ticker)
    cost_of_debt = interest_expense / sum(book_value_of_debt)
    corporate_tax_rate = .21

    return {
        'market_cap': int(market_cap / 1000000),
        'long_term_debt': book_value_of_debt[0] / 1000000,
        'operating_lease_liabilities': book_value_of_debt[1] / 1000000,
        'book_value_of_debt': sum(book_value_of_debt) / 1000000,
        'risk_free_rate': risk_free_rate,
        'beta': beta,
        'cost_of_equity': cost_of_equity,
        'interest_expense': interest_expense / 1000000,
        'cost_of_debt': cost_of_debt,
        'wacc': ((market_cap / (market_cap + sum(book_value_of_debt))) * cost_of_equity) + ((sum(book_value_of_debt) / (market_cap + sum(book_value_of_debt))) * cost_of_debt * (1-corporate_tax_rate))
    }


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
            'val': entry['val'] / 1000000,
            'filed': entry['filed']
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))
    df = df.sort_values('filed', ascending=False)
    df = df.drop_duplicates(['start', 'end'], keep='first').sort_values('end', ascending=True).drop(columns=['start', 'filed'])

    df = df.rename(columns={'val': metric})

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

def get_and_plot_fcf(ticker):
    metrics = [
        'NetCashProvidedByUsedInOperatingActivities',
        'PaymentsToAcquirePropertyPlantAndEquipment'
    ]

    df = pd.DataFrame()
    for metric in metrics:
        df = pd.merge(df, get_metrics_to_calculate_fcf(ticker, metric), on=['fp'], how='outer')

    print(df)


def fcf_forecast():
    growth_timespan = int(input('Enter the growth timespan (in years): '))


def spreadsheet(ticker):
    sheet_name = 'Untitled spreadsheet'
    credentials_file = '/home/mo-lester/Documents/6-7 Project/service-account.json'
    
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    gc = gspread.authorize(credentials)

    spreadsheet = gc.open(sheet_name)
    worksheet = spreadsheet.sheet1

    worksheet.clear()

    pos = 1
    for cell in wacc(ticker):
        worksheet.update(f'A{str(pos)}', [[cell]])
        worksheet.update(f'B{str(pos)}', [[wacc(ticker)[cell]]])
        
        pos += 1

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

    df = pd.DataFrame({
        'Year': [2024, 2025, 2026],
        'Revenue': [350018, 402521, 462899]
    })

    worksheet.update('C1', [df.columns.values.tolist()] + df.values.tolist())


def run():
    ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()

    get_and_plot_fcf(ticker)


run()
