import json
import requests
import pandas as pd
import gspread
import yfinance as yf
from google.oauth2.service_account import Credentials


def get_companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f:
        data = json.load(f)
    
    for v in data.values():
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10)
    
    return requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers={'User-Agent': 'janslavik311@gmail.com'}).json()


def calculate_ttm_interest_expense(ticker):
    companyData = get_companyData(ticker)

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


def get_fcf(ticker):
    companyData = get_companyData(ticker)

    rows = []
    for entry in companyData['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD']:
        start, end = pd.to_datetime([entry['start'], entry['end']])
        duration = (end-start).days      
        row = {
            'start': entry['start'],
            'end': entry['end'],
            'fp': entry['fp'],
            'duration': duration,
            'val': entry['val'],
            'filed': entry['filed']
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))
    df = df.sort_values('filed', ascending=False)
    df = df.drop_duplicates(['start', 'end'], keep='first').sort_values('end', ascending=True)

    print(df.to_string(index=False))


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
                        "startIndex": 0,  # Column A
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

    get_fcf(ticker)


run()
