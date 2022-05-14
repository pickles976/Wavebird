from cgitb import lookup
import requests, json
from bs4 import BeautifulSoup
import pandas as pd

CIK_DICT = {}

HEADER = {'User-Agent': "Two Llamas sebaslogo@gmail.com",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Language': 'en-US,en;q=0.8',
            'Connection': 'keep-alive'}

# load the ticker to cik dictionary
def load_ticker_dict():

    with open("cik_ticker.csv","r") as file:

        i = 0
        for line in file:
            if i > 0:
                line = line.split("|")
                ticker = line[1]
                cik = line[0]
                CIK_DICT[ticker] = cik
            i += 1

# Return a JSON with filing data given a CIK
def get_cik_json(CIK):

    ZEROS = (10 - len(CIK)) * "0" 

    PADDED_CIK = ZEROS + CIK

    url = f"https://data.sec.gov/submissions/CIK{PADDED_CIK}.json"

    data = requests.get(url,headers=HEADER)
    data = data.json()
    return data

# get a dictionary that stores cik, form type, and filing date together
def get_lookup_table(cik):

    data = get_cik_json(cik)
    recent_filings = data["filings"]["recent"]
    accessions = recent_filings["accessionNumber"]

    filingDates = recent_filings["filingDate"]
    forms = recent_filings["form"]
    primaryDocs = recent_filings["primaryDocument"]
    docDescriptions = recent_filings["primaryDocDescription"]

    data_list = []

    i = 0
    for form in forms:
        data_list.append({"cik": cik, 
        "type": "10-K", 
        "date": filingDates[i].replace("-",""),
        "accessionNumber": accessions[i].replace("-",""),
        "primaryDocument": primaryDocs[i],
        "docDescription": docDescriptions[i]})
        i += 1

    return data_list

ticker = "sbux"
ticker = ticker.upper()

# load the ticker to CIK dictionary
load_ticker_dict()
cik = CIK_DICT[ticker]

# load all of the filing data into a table
lookup_table = {}
lookup_table[ticker] = get_lookup_table(cik)

report_url = ""

# Loop through all of the filing info and get the link to a 10-K
for data in lookup_table[ticker]:
    if data["type"] == data["docDescription"] and data["type"] == "10-K":
        
        accessionNumber = data["accessionNumber"]
        document = data["primaryDocument"]

        report_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accessionNumber}/{document}"
        print(report_url)
        break


# # # Obtain HTML for search page
edgar_resp = requests.get(report_url,headers=HEADER)
edgar_str = edgar_resp.text

# Find the document link
doc_link = ''
soup = BeautifulSoup(edgar_str, 'html.parser')
tables = soup.find_all('table')

tables_dict = {}

for table in tables:
    i = 0
    df = pd.read_html(str(table))[0]
    tables_dict[i] = df.to_dict(orient='records')
    i += 1

    assets = None
    liabilities = None

    if "Total Assets" in df.values or "TOTAL ASSETS" in df.values:
        assets = df.loc[df[0] == "TOTAL ASSETS"]

    if "Total liabilities" in df.values:
        liabilities = df.loc[df[0] == "Total liabilities"]

    if len(assets) == len(liabilities):
        total = assets - liabilities
        print(total)


