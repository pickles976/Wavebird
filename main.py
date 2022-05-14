import time, json, requests
from alpha import getDCFArray
import pandas as pd
import matplotlib.pyplot as matplt
import datetime

ticker = "SBUX"

key = ""

# Fetch time-series stock data

with open("key.txt","r") as file:
    key = file.read()

metric = "TIME_SERIES_WEEKLY"

# replace the "demo" apikey below with your own key from https://www.alphavantage.co/support/#api-key
url = f'https://www.alphavantage.co/query?function={metric}&symbol={ticker}&apikey={key}'
r = requests.get(url)
data = r.json()

ts = data["Weekly Time Series"]
df = pd.DataFrame.from_dict(ts,orient="index")

newdf = pd.DataFrame()
newdf.index = df.index
newdf["price"] = df["4. close"].astype(float)
newdf = newdf[::-1]

dates = []
for (idx,value) in newdf.iterrows():

    # format
    format = '%Y-%m-%d'
    # convert from string format to datetime format
    dates.append(datetime.datetime.strptime(idx, format))
newdf.index = dates

# get dcf
t = time.time()
dcfs =  getDCFArray(ticker,5)
print(f"Elapsed time: {time.time() - t}s")
print(dcfs)


plot = newdf["price"].plot(style="-")
dcfs['FairValue'].plot(style=".",ax=plot)

matplt.style.use("seaborn")
matplt.show()

