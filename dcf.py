import json
import requests
import yfinance as yf


def get_companyData(ticker):
    with open('/home/mo-lester/Documents/6-7 Project/company_tickers.json', 'r') as f:
        data = json.load(f)
    
    for v in data.values():
        if v['ticker'] == ticker:
            cik = str(v['cik_str']).zfill(10)
    
    return requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json', headers={'User-Agent': 'janslavik311@gmail.com'}).json()


def wacc(ticker):
    companyData = get_companyData(ticker)
    ticker = yf.Ticker(ticker)

    market_cap = int(ticker.info.get('marketCap') / 1000000)

    needed_metrics = [
        'LongTermDebt',
        'OperatingLeaseLiabilityNoncurrent'
    ]
    
    my_dict = {}
    for metric in needed_metrics:
        new_row = {
            metric: int(companyData['facts']['us-gaap'][metric]['units']['USD'][-1]['val'] / 1000000)
        }
        my_dict.update(new_row)

    print(my_dict)




def run():
    ticker = input('Enter stock ticker symbol (e.g., AAPL, MSFT): ').upper()

    wacc(ticker)


run()
