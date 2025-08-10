import json
import requests
import numpy as np
import pandas as pd
import gspread
import yfinance as yf
from google.oauth2.service_account import Credentials


def companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f:
        data = json.load(f)
    
    for v in data.values():
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10)
    
    return requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers={'User-Agent': 'janslavik311@gmail.com'}).json()



def find_available_metrics(companyFacts):
    us_gaap = companyFacts['us-gaap']
    
    metric_alternatives = {
        'GrossProfit': ['GrossProfit', 'GrossProfitLoss'],
        'CostOfRevenue': [
            'CostOfGoodsAndServicesSold',
            'CostOfRevenue',
            'CostOfGoodsSold',
            'CostOfSales',
            'CostOfSalesAndServices'
        ],
        'OperatingIncome': [
            'OperatingIncomeLoss',
            'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
            'OperatingRevenue'
        ],
        'NetIncome': [
            'NetIncomeLoss',
            'NetIncomeLossAvailableToCommonStockholdersBasic',
            'ProfitLoss',
            'IncomeLossFromContinuingOperations'
        ]
    }
    
    available_metrics = {}
    
    for metric_type, alternatives in metric_alternatives.items():
        for alt in alternatives:
            if alt in us_gaap and 'units' in us_gaap[alt] and 'USD' in us_gaap[alt]['units']:
                available_metrics[metric_type] = alt
                break
        
    return available_metrics



def collect_rows(companyFacts, available_metrics):
    q, y = [], []
    us_gaap = companyFacts.get('us-gaap', {})

    for metric_type, param in available_metrics.items():
        if param not in us_gaap:
            continue
            
        if 'units' not in us_gaap[param] or 'USD' not in us_gaap[param]['units']:
            continue
            
        for entry in us_gaap[param]['units']['USD']:

            if not all(k in entry for k in ['start', 'end', 'val', 'form']):
                continue
                
            start, end = pd.to_datetime([entry['start'], entry['end']])
            duration = (end-start).days
            
            row = {
                    'start_date': entry['start'],
                    'end_date': entry['end'],
                    'duration_days': duration,
                    'value': int(entry['val']/1e6),
                    'param': metric_type,
                    'fp': entry.get('fp', ''),
                    'form': entry['form'],
                    'filed': entry.get('filed', entry['end'])}

            if entry['form'] == '10-Q' and 85 <= duration <= 95:
                q.append(row)
            if entry['form'] in ('10-Q', '10-K') and 270 <= duration <= 370:
                y.append(row)

    return q, y



def tidy_dataframe(rows):        
    df = pd.DataFrame(rows).dropna(subset=['value'])

    df['filed'] = pd.to_datetime(df.get('filed', df.get('end_date')))
    df = df.sort_values('filed', ascending=False)
    df = df.drop_duplicates(['start_date', 'end_date', 'param'], keep='first')

    df = df.pivot(
        index=['start_date','end_date','duration_days','fp','form','filed'],
        columns='param',
        values='value'
    ).reset_index()

    df['filed'] = pd.to_datetime(df['filed'], errors='coerce')
    
    return df.sort_values('end_date')



def calculate_q4(df, available_metrics):        
    if df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    df[['end_date', 'filed']] = df[['end_date', 'filed']].apply(pd.to_datetime)

    q4 = []
    
    for metric_type in available_metrics.keys():
        if metric_type not in df.columns:
            continue
            
        fy = df[(df.fp == 'FY') & (df.form == '10-K')]
        q3 = df[(df.fp == 'Q3') & (df.form == '10-Q')]
        
        for index, row in fy.iterrows():
            prev = q3[q3['end_date'] < row['end_date']]
            if prev.empty:
                continue
            
            q3r = prev.sort_values('end_date').iloc[-1]
            
            if pd.isna(row[metric_type]) or pd.isna(q3r[metric_type]):
                continue
               
            q4.append({
                'start_date': q3r.end_date.strftime('%Y-%m-%d'),
                'end_date': row.end_date.strftime('%Y-%m-%d'),
                'duration_days': (row.end_date - q3r.end_date).days,
                'value': row[metric_type] - q3r[metric_type], 
                'param': metric_type,
                'form': 'CALC',
                'fp': 'Q4',
                'filed': (row.filed or row.end_date).strftime('%Y-%m-%d')
            })

    return tidy_dataframe(q4)



def assign_quarter(row):
    month = row['end_date'].month
    year = row['end_date'].year

    if month in [1, 2, 3]:
        quarter = 'Q1'
    elif month in [4, 5, 6]:
        quarter = 'Q2'
    elif month in [7, 8, 9]:
        quarter = 'Q3'
    else:
        quarter = 'Q4'

    return f"{quarter} {year}"


def create_csv(ticker):
    companyFacts = companyData(ticker)['facts']
    
    available_metrics = find_available_metrics(companyFacts)
    print("✅ Available metrics:", available_metrics)

    q_rows, y_rows = collect_rows(companyFacts, available_metrics)
    
    quarterly_df = tidy_dataframe(q_rows)
    yearly_df = tidy_dataframe(y_rows)
    q4_df = calculate_q4(yearly_df, available_metrics)

    df = pd.concat([quarterly_df, q4_df], ignore_index=True)

    df = df.sort_values('end_date').drop(columns=['start_date','duration_days','form','filed'])
    df['end_date'] = pd.to_datetime(df['end_date'])
    
    df['fp'] = df.apply(assign_quarter, axis=1)

    metric_cols = list(available_metrics.keys())
    metric_cols = [m for m in metric_cols if m in df.columns]
    other_cols = [c for c in df.columns if c not in metric_cols]
    df = df[other_cols + metric_cols]

    missing = [m for m in available_metrics if m not in df.columns]
    if missing:
        print(f"⚠️ Skipping missing metrics: {missing}")

    df = df.drop(columns=['end_date'])

    for metric in metric_cols:
        for pos in range(len(df)):
            if pd.isna(df.at[pos, metric]):
                df.at[pos, metric] = 0
    
        df[metric] = df[metric].astype(int)

    return df.to_csv('/home/mo-lester/Documents/6-7 Project/output.csv', index=False)



def spreadsheet():
    csv_file = '/home/mo-lester/Documents/6-7 Project/output.csv'
    sheet_name = 'Untitled spreadsheet'
    credentials_file = '/home/mo-lester/Documents/6-7 Project/service-account.json'
    
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    gc = gspread.authorize(credentials)

    spreadsheet = gc.open(sheet_name)
    worksheet = spreadsheet.sheet1

    df = pd.read_csv(csv_file)

    new_rows = []
    for i in range(len(df)):
        row = df.iloc[i]

        converted_row = [str(int(cell)) if isinstance(cell, (np.integer, int)) else str(cell) if not pd.isna(cell) else '' for cell in row]
        new_rows.append(converted_row)

        if str(row['fp']).startswith('Q4'):
            new_rows.append(['' for _ in df.columns])

    worksheet.clear()
    worksheet.update([df.columns.tolist()] + new_rows)

    print(f"✅ Spreadsheet '{sheet_name}' updated with Q4 blank rows.")



def run():
    ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()

    create_csv(ticker)
    spreadsheet()


run()
