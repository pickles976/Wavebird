import requests, json

CIK_DICT = {}

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

    headers = {'User-Agent': "Two Llamas sebaslogo@gmail.com",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Language': 'en-US,en;q=0.8',
            'Connection': 'keep-alive'}

    data = requests.get(url,headers=headers)
    data = data.json()

    with open("data.json",'w') as file:
        file.write(json.dumps(data))

ticker = "SBUX"

load_ticker_dict()
get_cik_json(CIK_DICT[ticker])