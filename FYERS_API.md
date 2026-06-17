# Fyers API Documentation

## Introduction
Fyers API is a set of REST-like APIs that provide integration with our in-house trading platform with which you can build your own customized trading applications. You can place fresh single or multiple orders, modify and cancel existing orders in real-time. You can also get account-related information such as orderbook, tradebook, net positions, holdings, and funds.

We have ensured maximum security for our APIs which prevent unauthorised transactions. All API requests are received only over HTTPS protocol.

You can read more about when we introduced FYERS APIs [here](https://fyers.in/company-updates/introducing-fyers-api-for-trading/).

## Libraries and SDKs
To make it even easier for you to use the Fyers API in different programming languages, we have provided dedicated libraries/packages that handle the API calls for you. These libraries/packages abstract away the complexities of raw HTTP calls, allowing you to focus on integrating the API seamlessly into your applications.

*   **Fyers Python library** - Supports Python 3.8 to 3.12 version
*   **Fyers Node.js library** - Supports Node.js 12 to 21.6.2 version
*   **Fyers Web JS library** - Supports in Browser
*   **Fyers C# library** - Supports .NET 8.0.4
*   **Fyers Java library** - Supports Java 8

### CDN Link
| Versions | Links |
| :--- | :--- |
| 1.3.0 | `https://cdn.fyers.in/js/sdk/1.3.0/fyers-web-sdk-v3/index.min.js` |
| 1.2.1 | `https://cdn.fyers.in/js/sdk/1.2.1/fyers-web-sdk-v3/index.min.js` |
| 1.2.0 | `https://cdn.fyers.in/js/sdk/1.2.0/fyers-web-sdk-v3/index.min.js` |
| 1.1.0 | `https://cdn.fyers.in/js/sdk/1.1.0/fyers-web-sdk-v3/index.min.js` |
| 1.0.0 | `https://cdn.fyers.in/js/sdk/1.0.0/fyers-web-sdk-v3/index.min.js` |

---

## API Response Structure

### Success Response
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| s | string | “ok” |
| code | int | 200 |
| message | string | “” |
| *Additional key* | object / list / int / string | Each request will contain its own key based on the request |

### Failure Response
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| s | string | “error” |
| code | int | Negative integer to identify the specific error |
| message | string | Error message to identify error |
| HTTP Header | int | Refer to the error codes table |

### HTTP Status Codes
| Status Code | Meaning |
| :--- | :--- |
| 200 | Request was successful |
| 400 | Bad request. The request is invalid or certain other errors |
| 401 | Authorization error. User could not be authenticated |
| 403 | Permission error. User does not have the necessary permissions |
| 429 | Rate limit exceeded. Users have been blocked for exceeding the rate limit. |
| 500 | Internal server error. |

---

## Permission Templates
You can provide different app permissions for each application at the time of creation.

| Permission Template | Basic | Transactions Info | Order Placement | Market Data |
| :--- | :--- | :--- | :--- | :--- |
| **List of activities allowed** | Profile Details<br>Logout App<br>Logout | Basic Included<br>Orders<br>Positions<br>Trades<br>Holdings<br>Funds<br>Market Status | Transactions Info Included<br>Order Placement<br>Order Modification<br>Order Cancellation<br>Exit Positions<br>Convert Positions | Historical data<br>Market Depth<br>Quotes |

---

## Authentication Steps

### Step 1: Generate Auth Code
You need to navigate the user to the FYERS login URL with the correct GET parameters.

**Request Parameters**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `client_id` | string | This is the `app_id` which you have received after creating the app. E.g: “qwerty-100” |
| `redirect_uri`| string | This is where the user will be redirected after a successful login. This should be the same as what was provided at the time of app creation. E.g: `https://trade.fyers.in/api-login/redirect-uri/index.html` |
| `response_type`| string | This value must always be “code” |
| `state` | string | You send a random value. The same value will be returned after successful login to the redirect uri. E.g: “abcdefg” |

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| s | string | ok / error |
| code | int | This is the code to identify specific responses |
| message | string | This is the message to identify the specific error responses |
| `auth_code` | string | String value which will be used to generate the `access_token` |
| `state` | string | This value is returned as is from the first request |

**Node.js Example**
```javascript
// Import required modules
const FyersAPI = require("fyers-api-v3").fyersModel

// Create a new instance of FyersAPI
var fyers = new FyersAPI()

// Set your APPID obtained from Fyers (replace "xxx-1xx" with your actual APPID)
fyers.setAppId("xxx-1xx");

// Set the RedirectURL where the authorization code will be sent after the user grants access
// Make sure your redirectURL matches with your server URL and port
fyers.setRedirectUrl(`https://trade.fyers.in/api-login/redirect-uri/index.html`);

// Generate the URL to initiate the OAuth2 authentication process and get the authorization code
var generateAuthcodeURL = fyers.generateAuthCode();

console.log(generateAuthcodeURL)
```

**Sample Success Response URL**
```
https://api-t1.fyers.in/api/v3/generate-authcode?client_id=SPXXXXE7-100&redirect_uri=https%3A%2F%2Fdev.fyers.in%2Fredirection%2Findex.html&response_type=code&state=sample_state&nonce=sample_nonce
```

### Step 2: Generate Access Token

**Request Parameters**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `grant_type` | string | This value must always be “authorization\_code” |
| `appIdHash` | string | SHA-256 of `api_id` + `app_secret`. You can use an [online tool](https://emn178.github.io/online-tools/sha256.html) for reference. |
| `code` | string | This is the `auth_code` which is received from the first step |

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| s | string | ok / error |
| code | int | Code to identify specific responses |
| message | string | Message to identify specific error responses |
| `access_token` | string | This value will be used for all subsequent requests. |

**Node.js Example**
```javascript
const FyersAPI = require("fyers-api-v3").fyersModel

// Create a new instance of FyersAPI
var fyers = new FyersAPI()

// Set your APPID obtained from Fyers (replace "xxx-1xx" with your actual APPID)
fyers.setAppId("xxx-1xx");

// Set the RedirectURL where the authorization code will be sent after the user grants access
fyers.setRedirectUrl("https://trade.fyers.in/api-login/redirect-uri/index.html");

// Define the authorization code and secret key required for generating access token
const authcode = "eyJxxxx"; // Replace with the actual authorization code obtained from the user
const secretKey = "xxxxx"; // Replace with your secret key provided by Fyers
fyers.generate_access_token({ "secret_key": secretKey, "auth_code": auth_code }).then((response) => {
  console.log(response)
}).catch((error) => {
  console.log(error)
})
```

**Sample Success Response**
```json
{
  "s": "ok",
  "code": 200,
  "message": "",
  "access_token": "eyJ0eXAiOi***.eyJpc3MiOiJh***.HrSubihiFKXOpUOj_7***",
  "refresh_token": "eyJ0eXAiO***.eyJpc3MiOiJh***.67mXADDLrrleuEH_EE***"
}
```

### Refresh Token
When the `auth_code` is validated to generate the access token, a `refresh_token` is also sent in the response. This token has a validity of 15 days and can be used to generate a new access token.

**Request Body Parameters (POST)**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `grant_type` | string | Must be set to “refresh\_token”. |
| `appIdHash` | string | SHA-256 of `api_id` + `app_secret`. |
| `refresh_token`| string | The refresh token previously issued. |
| `pin` | string | The user's pin. |

---

## API Endpoints

### Get Profile
Fetches the user's profile details.
*Note: Ensure `fyers.setAccessToken` and `fyers.setAppId` are set before making any API calls.*

**Node.js Example**
```javascript
const FyersAPI = require("fyers-api-v3").fyersModel

var fyers = new FyersAPI()
fyers.setAppId("QCxxxx57-1xx")
fyers.setRedirectUrl("https://url.xyz")
fyers.setAccessToken("eyjb....")

fyers.get_profile().then((response)=>{
      console.log(response)
  }).catch((error)=>{
      console.log(error)
  })
```

**Sample Response**```json
 {
   "s": "ok",
   "code": 200,
   "message": "",
   "data": {
     "name": "XASHXX G H",
     "image": "https://fyers-user-details.s3.amazonaws.com/image/FK6107548224?X-Amz-Algorithm=AWS4-HMAC",
     "display_name":"Y2K",
     "email_id":"xashxx.ghang@gmail.com",
     "PAN": "EXXXXXXXXE",
     "fy_id": "FX0011",
     "pin_change_date": "19-08-2020 14:58:41",
     "mobile_number": "xxxxxxxxxx",
     "totp": true,
     "pwd_change_date": "19-08-2020 14:58:41",
     "pwd_to_expire": 42,
     "ddpi_enabled": false,
     "mtf_enabled": false
   }
 }
```

## Data API

### History
The historical API provides archived candle data for symbols across various exchanges.

**Handling Partial Candles**
To receive completed candle data, it is recommended to always use a "range_to" timestamp of the previous minute (or the previous resolution interval). Sending a timestamp for the current, incomplete interval will result in partial data.

**Limits**
*   Unlimited number of requests per day.
*   **Minute Resolutions (1 to 240 min):** Up to 100 days of data per request. Data is available from July 3, 2017.
*   **Daily Resolutions (1D):** Up to 366 days of data per request.
*   **Seconds Charts:** History is available for the last 30 trading days.

**Request Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol`* | string | E.g: `NSE:SBIN-EQ` |
| `resolution`* | string | Candle resolution. "D" or "1D" for day, "5S", "10S" etc. for seconds, "1", "2", "5" etc. for minutes. |
| `date_format`* | int | `0` for epoch value (e.g., 1690895316), `1` for "yyyy-mm-dd" format. |
| `range_from`* | string | Start date of records in the specified `date_format`. |
| `range_to`* | string | End date of records in the specified `date_format`. |
| `cont_flag`* | int | Set to `1` for continuous data for futures and options. |
| `oi_flag` | int | Set to `1` to include Open Interest (OI) as part of the candle data. |

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| s | string | "ok" or "error" |
| candles | array | Array of candles, where each candle is an array: `[epoch_time, open, high, low, close, volume]` |

**Node.js Example**
```javascript
const FyersAPI = require("fyers-api-v3").fyersModel

var fyers = new FyersAPI()
fyers.setAppId("QCxxxx57-1xx")
fyers.setRedirectUrl("https://url.xyz")
fyers.setAccessToken("eyjb....")

var inp={
    "symbol":"NSE:SBIN-EQ",
    "resolution":"D",
    "date_format":"0",
    "range_from":"1690895316",
    "range_to":"1691068173",
    "cont_flag":"1"
}
fyers.getHistory(inp).then((response)=>{
    console.log(response)
}).catch((err)=>{
    console.log(err)
})
```

**Sample Response**
```json
{
 "s": "ok",
 "candles": [
     [
         1622073600,
         413.7,
         429.1,
         412.0,
         425.2,
         73392997
     ]
 ]
}
```

### Quotes
Retrieves full market quotes for one or more symbols.

**Request Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbols`* | string | Comma-separated symbols. Maximum 50. E.g: `NSE:SBIN-EQ,NSE:TCS-EQ`. |

**Response Attributes**
*Each object in the `d` array contains the following keys:*
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| ch | float | Change value |
| chp | float | Percentage change |
| lp | float | Last traded price |
| spread | float | Difference between ask and bid |
| ask | float | Asking price |
| bid | float | Bidding price |
| open\_price | float | Opening price |
| high\_price | float | Highest price for the day |
| low\_price | float | Lowest price for the day |
| prev\_close\_price| float | Previous closing price |
| atp | float | Average traded price |
| volume | int | Volume traded |
| short\_name | string | Short name for the symbol |
| exchange | string | Name of the exchange |
| description | string | Description of the symbol |
| original\_name | string | Original name of the symbol |
| symbol | string | Symbol name |
| fyToken | string | Unique token for each symbol |
| tt | int | Today’s time |

**Node.js Example**
```javascript
const FyersAPI = require("fyers-api-v3").fyersModel

var fyers = new FyersAPI()
fyers.setAppId("QCxxxx57-1xx")
fyers.setRedirectUrl("https://url.xyz")
fyers.setAccessToken("eyjb....")

var inp=["NSE:SBIN-EQ","NSE:TCS-EQ"]

fyers.getQuotes(inp).then((response) => {
    console.log(response)
}).catch((error) => {
    console.log(error)
})
```

**Sample Response**
```json
{
"s": "ok",
"code": 200,
"d": [
    {
        "n": "NSE:ONGC-EQ",
        "s": "ok",
        "v": {
            "ch": -0.35,
            "chp": -0.28,
            "lp": 123.6,
            "spread": 0.05,
            "ask": 123.65,
            "bid": 123.6,
            "open_price": 123.95,
            "high_price": 126.6,
            "low_price": 122.5,
            "prev_close_price": 122.2,
            "atp": 120.6,
            "volume": 14942959,
            "short_name": "ONGC-EQ",
            "exchange": "NSE",
            "description": "NSE:ONGC-EQ",
            "original_name": "NSE:ONGC-EQ",
            "symbol": "NSE:ONGC-EQ",
            "fyToken": "10100000003045",
            "tt": "1623369600"
        }
    }
   ]
 }
```

### Market Depth
Returns complete market data including quantities, OHLC values, Open Interest, and bid/ask prices.

**Request Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol`* | string | E.g: `NSE:SBIN-EQ` |
| `ohlcv_flag`* | int | Set to `1` to get open, high, low, closing price and volume. |

**Response Attributes**
*The response object `d` contains a key for each symbol requested. Each symbol object contains:*
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| totalbuyqty | int | Total buying quantity |
| totalsellqty | int | Total selling quantity |
| bids | array | Array of bid objects (`{price, volume, ord}`) |
| ask | array | Array of ask objects (`{price, volume, ord}`) |
| o, h, l, c | float | Open, High, Low, Close prices |
| chp | float | Percentage change |
| tick\_Size | float | Minimum price multiplier |
| ch | float | Change value |
| ltq, ltt, ltp | int, int, float| Last traded quantity, time, and price |
| v | int | Volume traded |
| atp | float | Average traded price |
| lower\_ckt, upper\_ckt | float | Circuit limits |
| expiry | string | Expiry date |
| oi | float | Open interest |
| oiflag | bool | Boolean flag for OI data |
| pdoi | int | Previous day open interest |
| oipercent | float | Change in OI percentage |

**Node.js Example**
```javascript
const FyersAPI = require("fyers-api-v3").fyersModel

var fyers = new FyersAPI()
fyers.setAppId("QCxxxx57-1xx")
fyers.setRedirectUrl("https://url.xyz")
fyers.setAccessToken("eyjb....")

var inp={
  "symbol":["NSE:SBIN-EQ","NSE:TCS-EQ"],
  "ohlcv_flag":1
}

fyers.getMarketDepth(inp).then((response) => {
    console.log(response)
}).catch((error) => {
    console.log(error)
})
```

**Sample Response**
```json
{
"s": "ok",
"d": {
    "NSE:SBIN-EQ": {
        "totalbuyqty": 875917,
        "totalsellqty": 1972756,
        "bids": [
            {"price": 430.35, "volume": 33, "ord": 3}
        ],
        "ask": [
            {"price": 430.45, "volume": 3573, "ord": 11}
        ],
        "o": 432.0,
        "h": 432.5,
        "l": 427.45,
        "c": 431.7,
        "chp": -0.29,
        "tick_Size": 0.05,
        "ch": -1.25,
        "ltq": 1,
        "ltt": 1626429149,
        "ltp": 430.45,
        "v": 8393560,
        "atp": 429.63,
        "lower_ckt": 388.55,
        "upper_ckt": 474.85,
        "expiry": "",
        "oi": 0,
        "oiflag": false,
        "pdoi": 0,
        "oipercent": 0.0
    }
},
"message": ""
}
```

### Option Chain
Provides data for call and put options, focusing on strike prices, IndiaVIX, and expiry dates.

**Request Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol`* | string | E.g: `NSE:NIFTY50-INDEX` |
| `strikecount` | int | Number of ITM/OTM strikes to fetch (Max: 50) |
| `timestamp` | string | Option chain data at a specific timestamp |

**Response Attributes**
*The response is a complex object containing `callOi`, `putOi`, `expiryData`, `indiavixData`, and an array `optionsChain` with details for each option contract.*

const FyersAPI = require("fyers-api-v3").fyersModel
var fyers = new FyersAPI()
fyers.setAppId("QCxxxx57-1xx")
fyers.setRedirectUrl("https://url.xyz")
fyers.setAccessToken("eyjb....")
var inp={
  "symbol":["NSE:SBIN-EQ","NSE:TCS-EQ"],
  "ohlcv_flag":1
}
fyers.getOptionChain({"symbol":"NSE:SBIN-EQ","strikecount":1,"timestamp": ""}).then((response)=>{
    console.log(response.data)
}).catch((err)=>{
    console.log(err)
})
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
