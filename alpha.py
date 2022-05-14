import requests,json,datetime
import pandas as pd
import numpy as np
from discount import getCOEHistorical, getERP, getCOECurrent

key = ""

with open("key.txt","r") as file:
    key = file.read()

# Return historical FCFEs for a ticker
# Free Cash Flow from Equity
def getFCFE(ticker):

    metric = "CASH_FLOW"

    # replace the "demo" apikey below with your own key from https://www.alphavantage.co/support/#api-key
    url = f'https://www.alphavantage.co/query?function={metric}&symbol={ticker}&apikey={key}'
    r = requests.get(url)
    data = r.json()

    annualReports = data["annualReports"]
    metric = "BALANCE_SHEET"

    url = f'https://www.alphavantage.co/query?function={metric}&symbol={ticker}&apikey={key}'
    r = requests.get(url)
    data = r.json()

    # combine CASH FLOW data with BALANCE SHEET data
    for i in range(0,len(annualReports)):

        tempAnnualReports = data["annualReports"]

        if annualReports[i]["fiscalDateEnding"] == tempAnnualReports[i]["fiscalDateEnding"]:
            annualReports[i] = {**annualReports[i],**tempAnnualReports[i]}

    table = {}

    # Extract data for every annual report
    for entry in annualReports:

        divPayout = 0
        if entry["dividendPayout"] != "None":
            divPayout = int(entry["dividendPayout"])

        debt = 0 
        if entry["shortLongTermDebtTotal"] != "None":
            debt = int(entry["shortLongTermDebtTotal"])

        date = int(entry["fiscalDateEnding"].split("-")[0])
        newEntry = {"netIncome": int(entry["netIncome"]), 
        "dividendPayout": divPayout,
        "capitalExpenditures": int(entry["capitalExpenditures"]), 
        "workingCapital": int(entry["totalCurrentAssets"]) - int(entry["totalCurrentLiabilities"]),
        "debt": debt,
        "shares": int(entry["commonStockSharesOutstanding"])}
        table[date] = newEntry

    df = pd.DataFrame.from_dict(table,orient="index")
    df["deltaWorkingCapital"] = np.nan
    df["debtIssued"] = np.nan
    df["FCFE"] = np.nan

    prev = ""

    # get new debt
    for idx,row in df.iterrows():

        if idx - 1 in df.index:
            # current debt - previous debt
            df["debtIssued"][idx] = df["debt"][idx] - df["debt"][idx - 1] 

            # current working capital - previous working capital
            df["deltaWorkingCapital"][idx] = df["workingCapital"][idx] - df["workingCapital"][idx - 1] 

    df["FCFE"] = df["netIncome"] + df["dividendPayout"] - df["capitalExpenditures"] - df["deltaWorkingCapital"] + df["debtIssued"]

    newdf = pd.DataFrame()
    newdf["FCFE"] = df["FCFE"]
    newdf["shares"] = df["shares"]
    return newdf

# formula for actually calculating a DCF estimate
def getDCF(fcfe,coe,erp,yearsGrowth):

    total = 0
    # print(fcfe,coe,erp)

    for i in range(yearsGrowth):
        discount = (1 + coe) ** i
        total += fcfe / discount

    terminal = fcfe / erp

    total += terminal

    return float(total)


# returns a trend of historical DCF's for a given stock
def getDCFArray(ticker,yearsGrowth):

    fcfeData = getFCFE(ticker)

    dcfData = pd.DataFrame() 
    dcfData.index = fcfeData.index
    dcfData["dcf"] = np.nan
    dcfData["FairValue"] = np.nan

    # get DCF for each year
    for (idx,value) in fcfeData.iterrows():
        start = f"{idx-1}-01-01"
        end = f"{idx}-12-31"
        fcfe = value["FCFE"]
        shares = value["shares"]

        try:

            coe = getCOEHistorical(ticker,"SPY",start,end)/100.0
            erp = getERP(idx)/100.0

            dcf = getDCF(fcfe,coe,erp,yearsGrowth)
            dcfData["dcf"][idx] = dcf
            dcfData["FairValue"][idx] = dcf/shares

        except:
            print(f"Failed for year {idx}, trying with fresh data")

            try:
                erp = 5.23
                coe = getCOECurrent(ticker,"SPY",start,end,erp)/100.0
                dcf = getDCF(fcfe,coe,erp,yearsGrowth)
                dcfData["dcf"][idx] = dcf
                dcfData["FairValue"][idx] = dcf/shares
            except:
                print("Failed with fresh data")

    dcfData.index = [f"{i}-04-01" for i in fcfeData.index]

    dates = []
    for (idx,value) in dcfData.iterrows():

        # format
        format = '%Y-%m-%d'
        # convert from string format to datetime format
        dates.append(datetime.datetime.strptime(idx, format))
    dcfData.index = dates

    return dcfData