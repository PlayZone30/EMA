Option Chain
The Optionchain API provides important information about options trading, specifically focusing on strike prices, which are predetermined prices at which an option contract can be exercised. This API offers data for both Call (CE) and Put (PE) options, along with values for index, IndiaVIX (Volatility Index) and available expiry dates. When using the API, you can specify the number of strike prices you're interested in. For example, if you request a strike count of 2, the API will return data for:

At-The-Money (ATM) strike: The closest strike price to the current market price.
Out-of-The-Money (OTM) strikes: Strike prices higher than the current market price for Calls (CE) and lower than the current market price for Puts (PE). The API will return data for 2 CE and 2 PE OTM strikes.
In-The-Money (ITM) strikes: Strike prices lower than the current market price for Calls (CE) and higher than the current market price for Puts (PE). The API will return data for 2 CE and 2 PE ITM strikes.
.
Request attributes
Attribute	Data Type	Description
symbol*	string	Mandatory.
Eg: NSE:SBIN-EQ
strikecount	int	Options strike count for symbol(MAX = 50)
timestamp	string	Options chain data at timestamp'
Response attributes
Attribute	Data Type	Description
s	string	ok / error
code	int	This is the code to identify specific responses
callOi	int	Total open interest for call options
putOi	int	Total open interest for put options
date	string	Expiry date in DD-MM-YYYY
expiry	string	Expiry timestamp
ask	float	Ask price
bid	float	Bid price
description	string	Description of the stock
ex_symbol	string	Exchange Symbol
exchange	int	The exchange in which order is placed.
View Details
fytoken	string	Fytoken is a unique identifier for every symbol.
View Details
ltp	float	LTP is the price from which the next sale of the stocks happens
ltpch	float	Change in last traded price
ltpchp	float	Percentage change in last traded price
option_type	string	Type of option (if applicable)
strike_price	int	Strike price (if applicable)
symbol	string	Symbol
fp	float	Future price
fpch	float	Change in future price
fpchp	float	Percentage change in future price
oi	int	Open interest
oich	int	Change in Open interest
oichp	float	Percentage change in Open interest
prev_oi	int	Previous Open interest
volume	int	Volume traded


response:
from fyers_apiv3 import fyersModel
client_id = "YYYYYYY-100"
access_token = "XXXXXXXXXXXXX"
# Initialize the FyersModel instance with your client_id, access_token, and enable async mode
fyers = fyersModel.FyersModel(client_id=client_id, token=access_token,is_async=False, log_path="")
data = {
    "symbol":"NSE:TCS-EQ",
    "strikecount":1,
    "timestamp": ""
}
response = fyers.optionchain(data=data);
print(response)
 ------------------------------------------------------------------------------------------------------------------------------------------
 Sample Success Response 
 ------------------------------------------------------------------------------------------------------------------------------------------
 {
  "code": 200,
  "data": {
    "callOi": 10038175,
    "expiryData": [
      {
        "date": "25-04-2024",
        "expiry": "1714039200"
      },
      {
        "date": "30-05-2024",
        "expiry": "1717063200"
      },
      {
        "date": "27-06-2024",
        "expiry": "1719482400"
      }
    ],
    "indiavixData": {
      "ask": 0,
      "bid": 0,
      "description": "INDIAVIX-INDEX",
      "ex_symbol": "INDIAVIX",
      "exchange": "NSE",
      "fyToken": "101000000026017",
      "ltp": 10.55,
      "ltpch": -2.15,
      "ltpchp": -16.93,
      "option_type": "",
      "strike_price": -1,
      "symbol": "NSE:INDIAVIX-INDEX"
    },
    "optionsChain": [
      {
        "ask": 3880.15,
        "bid": 3880.05,
        "description": "TATA CONSULTANCY SERV LT",
        "ex_symbol": "TCS",
        "exchange": "NSE",
        "fp": 3876.65,
        "fpch": 14.2,
        "fpchp": 0.37,
        "fyToken": "101000000011536",
        "ltp": 3880.15,
        "ltpch": 15.55,
        "ltpchp": 0.4,
        "option_type": "",
        "strike_price": -1,
        "symbol": "NSE:TCS-EQ"
      },
      {
        "ask": 34.9,
        "bid": 34.35,
        "fyToken": "1011240425139431",
        "ltp": 34.8,
        "ltpch": 2.7,
        "ltpchp": 8.41,
        "oi": 99575,
        "oich": -3325,
        "oichp": -3.23,
        "option_type": "CE",
        "prev_oi": 102900,
        "strike_price": 3860,
        "symbol": "NSE:TCS24APR3860CE",
        "volume": 202650
      },
      {
        "ask": 19.3,
        "bid": 19,
        "fyToken": "1011240425139432",
        "ltp": 19.05,
        "ltpch": -12.4,
        "ltpchp": -39.43,
        "oi": 159075,
        "oich": 28525,
        "oichp": 21.85,
        "option_type": "PE",
        "prev_oi": 130550,
        "strike_price": 3860,
        "symbol": "NSE:TCS24APR3860PE",
        "volume": 304150
      },
      {
        "ask": 24.85,
        "bid": 24.55,
        "fyToken": "1011240425133432",
        "ltp": 24.95,
        "ltpch": -0.55,
        "ltpchp": -2.16,
        "oi": 165900,
        "oich": 9975,
        "oichp": 6.4,
        "option_type": "CE",
        "prev_oi": 155925,
        "strike_price": 3880,
        "symbol": "NSE:TCS24APR3880CE",
        "volume": 543025
      },
      {
        "ask": 29.35,
        "bid": 28.8,
        "fyToken": "1011240425133433",
        "ltp": 29.2,
        "ltpch": -14.1,
        "ltpchp": -32.56,
        "oi": 98175,
        "oich": 28350,
        "oichp": 40.6,
        "option_type": "PE",
        "prev_oi": 69825,
        "strike_price": 3880,
        "symbol": "NSE:TCS24APR3880PE",
        "volume": 199500
      },
      {
        "ask": 17.8,
        "bid": 17.6,
        "fyToken": "1011240425139433",
        "ltp": 17.75,
        "ltpch": -1.4,
        "ltpchp": -7.31,
        "oi": 631050,
        "oich": 23275,
        "oichp": 3.83,
        "option_type": "CE",
        "prev_oi": 607775,
        "strike_price": 3900,
        "symbol": "NSE:TCS24APR3900CE",
        "volume": 981925
      },
      {
        "ask": 42.45,
        "bid": 41.85,
        "fyToken": "1011240425139434",
        "ltp": 41.75,
        "ltpch": -14.65,
        "ltpchp": -25.98,
        "oi": 338100,
        "oich": -9975,
        "oichp": -2.87,
        "option_type": "PE",
        "prev_oi": 348075,
        "strike_price": 3900,
        "symbol": "NSE:TCS24APR3900PE",
        "volume": 129325
      }
    ],
    "putOi": 3875200
  },
  "message": "",
  "s": "ok"
}
