import pandas as pd
import pandas_datareader as wb
import numpy as np
import time

# Functions used to get the discount rate for FCFE
# COE aka Cost of Equity

# get beta for a given stock vs a broader index (SPY/DJIA/NASDAQ)
# very slow since it is getting both historical stock prices for
# the ticker and the index
def getBeta(ticker,indexFund,start,end):

    t = time.time()

    # scrape time-series data from yahoo
    aapl = wb.DataReader(ticker, 'yahoo', start, end)
    spy = wb.DataReader(indexFund,"yahoo",start,end)

    # column names
    tickIdx = f"{ticker}%"
    indexIdx = f"{indexFund}%"

    # create and popualte dataframe
    both = pd.DataFrame()
    both[ticker] = aapl["Close"]
    both[tickIdx] = np.nan
    both[indexFund] = spy["Close"]
    both[indexIdx] = np.nan

    # make the index iterable
    both.index = [i for i in range(len(both))]

    # iterate to get deltas
    for idx,row in both.iterrows():

        if idx - 1 in both.index:
            # get price diffs
            both[tickIdx][idx] = (both[ticker][idx] - both[ticker][idx - 1]) / both[ticker][idx] * 100
            # get price diffs
            both[indexIdx][idx] = (both[indexFund][idx] - both[indexFund][idx - 1]) / both[indexFund][idx] * 100

    # making series from list a
    a = both[tickIdx]
    
    # making series from list b
    b = both[indexIdx]
        
    # covariance through pandas method
    covar = a.cov(b)
    var = b.var()
    beta = covar/var

    print(f"Retrieving beta took: {time.time() - t}s")

    return beta

# Get the most recent risk-free rate from 10-Year Bonds
def getRFR(start,end):

    tenyear = wb.DataReader("^TNX", 'yahoo', start, end)
    rfr = tenyear["Close"][-1]
    return rfr
    
# Get Cost of Equity for a stock against an index fund
# given a start date, end date, and ERP estimate
# ERP: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm
def getCOECurrent(ticker,indexFund,start,end,erp):

    beta = getBeta(ticker,indexFund,start,end)
    rfr = getRFR(start,end)
    return rfr + (beta * erp)

def getERP(year):

    df = pd.read_csv("HistoricalRates.csv")

    erp = df["Implied ERP (FCFE)"][df["Year"] == year].values[0]
    erp = float(erp.replace("%",""))
    return erp


# Get Cost of Equity for a stock given historical data
# of bond yields, erp, and index fund performance
def getCOEHistorical(ticker,indexFund,start,end):

    year = int(start.split("-")[0])

    erp = getERP(year)

    df = pd.read_csv("HistoricalRates.csv")

    # Get the RFR 10-Y from historical data
    rfr = df["T.Bond Rate"][df["Year"] == year].values[0]
    rfr = float(rfr.replace("%",""))

    # get beta from Yahoo
    beta = getBeta(ticker,indexFund,start,end)
    return rfr + (beta * erp)
