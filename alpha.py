import requests,json
import pandas as pd
import numpy as np

key = "SJI9YTPAA1KTKCDP"
ticker = "AAPL"
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

for i in range(0,len(annualReports)):

    tempAnnualReports = data["annualReports"]

    if annualReports[i]["fiscalDateEnding"] == tempAnnualReports[i]["fiscalDateEnding"]:
        annualReports[i] = {**annualReports[i],**tempAnnualReports[i]}


table = {}

for entry in annualReports:

    date = int(entry["fiscalDateEnding"].split("-")[0])
    newEntry = {"netIncome": int(entry["netIncome"]), 
    "capitalExpenditures": int(entry["capitalExpenditures"]), 
    "workingCapital": int(entry["totalCurrentAssets"]) - int(entry["totalCurrentLiabilities"]),
    "debt": int(entry["shortLongTermDebtTotal"])}
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

df["FCFE"] = df["netIncome"] - df["capitalExpenditures"] - df["deltaWorkingCapital"] + df["debtIssued"]

print(df)

# df["debtIssued"] = df[]

# print(df)


# with open("table.json","w") as file:
#     file.write(json.dumps(annualReports[0]))