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


def fcf(ticker):
    companyData = get_companyData(ticker)
    combined_df = None
    min_end = None
    max_end = None

    metrics = {
        'cfoa': ['NetCashProvidedByUsedInOperatingActivities'],
        'capex': ['PaymentsToAcquirePropertyPlantAndEquipment',
                  'PaymentsToAcquireProductiveAssets']
    }

    for key, value in metrics.items():
        metric_df = None

        for metric in value:
            rows = []
            for entry in companyData['facts']['us-gaap'][metric]['units']['USD']:
                start, end = pd.to_datetime([entry['start'], entry['end']])
                duration = (end - start).days
                rows.append({
                    'start': entry['start'],
                    'end': entry['end'],
                    'filed': entry['filed'],
                    'fp': entry['fp'],
                    'duration': duration,
                    key: entry['val']
                })

            df = pd.DataFrame(rows)
            df['filed'] = pd.to_datetime(df.get('filed', df.get('end')))
            df = df.sort_values('filed', ascending=False)
            df = df.drop_duplicates(['start', 'end'], keep='first').sort_values('end', ascending=True).drop(columns='filed')

            # Remove partial quarters except Q1
            for idx in sorted(df.index, reverse=True):
                if df.at[idx, 'duration'] < 95 and df.at[idx, 'fp'] != 'Q1':
                    df.drop(index=idx, inplace=True)

            # Convert YTD to quarterly
            adj_rows = []
            for pos in range(len(df)):
                duration = (pd.to_datetime(df['end'].iloc[pos]) - pd.to_datetime(df['end'].iloc[pos-1])).days
                if 85 < duration < 95:
                    if df['fp'].iloc[pos] == 'Q1':
                        adj_rows.append({'end': df['end'].iloc[pos], key: df[key].iloc[pos]})
                    else:
                        adj_rows.append({'end': df['end'].iloc[pos], key: df[key].iloc[pos] - df[key].iloc[pos-1]})

            df = pd.DataFrame(adj_rows)
            df['end'] = pd.to_datetime(df['end'])
            df['quarter'] = df.apply(assign_quarter, axis=1)

            # Track date range for calendar
            if min_end is None or df['end'].min() < min_end:
                min_end = df['end'].min()
            if max_end is None or df['end'].max() > max_end:
                max_end = df['end'].max()

            df = df.drop(columns='end')

            if metric_df is None:
                metric_df = df
            else:
                # Sum duplicate quarters for this key
                metric_df = pd.concat([metric_df, df]).groupby('quarter', as_index=False).sum()

        # Merge with main combined_df
        if combined_df is None:
            combined_df = metric_df
        else:
            combined_df = combined_df.merge(metric_df, on='quarter', how='outer')

    # Build full quarter calendar
    quarters = pd.date_range(start=min_end, end=max_end, freq='QE')
    quarter_year = [f'Q{d.quarter} {d.year}' for d in quarters]
    calendar_df = pd.DataFrame({'quarter': quarter_year})

    # Merge to ensure all quarters present
    combined_df = calendar_df.merge(combined_df, on='quarter', how='left')

    # Final chronological sort
    tmp = combined_df['quarter'].str.extract(r'Q(?P<q>\d)\s+(?P<year>\d{4})').astype(int)
    combined_df = (
        combined_df
        .assign(year=tmp['year'], qnum=tmp['q'])
        .sort_values(['year', 'qnum'])
        .drop(columns=['year', 'qnum'])
        .reset_index(drop=True)
    )

    # make sure the names exist and create FCF
    assert {'cfoa', 'capex'}.issubset(combined_df.columns), combined_df.columns
    combined_df['fcf'] = combined_df['cfoa'].fillna(0) - combined_df['capex'].fillna(0)

    # build the exact frame you’ll plot
    plot_df = combined_df[['quarter', 'fcf']].copy()

    ax = plot_df.plot(x='quarter', y='fcf', kind='bar')

    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x/1e6:.0f}M'))
    labels = [label.get_text() if "Q4" in label.get_text() else "" 
              for label in ax.get_xticklabels()]
    ax.set_xticklabels(labels)

    plt.show()




ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()
fcf(ticker)
