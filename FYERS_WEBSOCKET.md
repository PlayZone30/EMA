***

# Fyers WebSocket API

## Introduction
The Fyers WebSocket provides a robust method for accessing real-time data or order updates seamlessly and with low latency. It enables developers to establish a persistent, bidirectional connection with the server, allowing them to receive continuous updates, such as symbol data, market depth, or order status.

To enhance your experience, here are some helpful tips and best practices:

*   **Subscription Limit**: You can subscribe to a maximum of 5000 data symbols simultaneously. Staying within this limit ensures smooth subscription management.
*   **Single Instance**: You can create only one WebSocket connection instance at a time to ensure stability and prevent issues from multiple concurrent connections.
*   **Efficient Thread Management**: The WebSocket operates on a dedicated thread, running independently of your main application thread to ensure your primary tasks continue without interruption.
*   **Customizable Callback Functions**: Tailor your application's behavior using callback functions to define specific actions for events like data updates or errors.
*   **Auto-Reconnect**: Enable automatic reconnection by setting the `reconnect` parameter to `true` during initialization. You can set the maximum reconnection count up to 50.
*   **Logging to File**: Set the `write_to_file` parameter to `true` to save received data to a log file for analysis or archival purposes. This function will only work without custom callback functions.
*   **Reconnect Retry**: To define a dynamic retry count (max 50), set the `reconnect_retry` parameter to an integer value. In Node.js, this is done via `fyersdata.autoreconnect(trycount)`.
*   **Disable Logging (Node.js)**: To disable logging, use the `disable_logging` flag. For example: `new FyersSocket("token", "logpath", true)`.

### Node.js Initialization Example
```javascript
const FyersSocket = require("fyers-api-v3").fyersDataSocket;

// The third parameter (true) is the flag to enable/disable logging
var fyersdata = new FyersSocket("xxxxx-1xx:ey....", "logpath", true);

// Set autoreconnect to try 6 times in case of disconnection
fyersdata.autoreconnect(6); 
fyersdata.connect();
```
---

## General Socket (Orders, Positions, Trades)
The General Socket API provides real-time updates for orders, positions, trades, price alerts, and EDIS status.

### Order Updates
Receive real-time updates on your orders.

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `id` | string | The unique order ID. |
| `exchOrdId` | string | The order ID provided by the exchange. |
| `symbol` | string | The symbol for which the order is placed. |
| `qty` | int | The original order quantity. |
| `remainingQuantity`| int | The remaining quantity. |
| `filledQty` | int | The filled quantity after partial trades. |
| `status` | int | `1`: Canceled, `2`: Traded/Filled, `5`: Rejected, `6`: Pending. |
| `message` | string | Error messages, if any. |
| `segment` | int | `10`: Equity, `11`: F&O, `12`: Currency, `20`: Commodity. |
| `limitPrice` | float | The limit price for the order. |
| `stopPrice` | float | The stop price for the order. |
| `productType` | string | The product type (e.g., `INTRADAY`, `CNC`). |
| `type` | int | `1`: Limit, `2`: Market, `3`: Stop (SL-M), `4`: Stoplimit (SL-L). |
| `side` | int | `1`: Buy, `-1`: Sell. |
| `orderValidity`| string | `DAY` or `IOC`. |
| `orderDateTime`| string | Order time in `DD-MMM-YYYY hh:mm:ss` format (IST). |
| `parentId` | string | The parent order ID (for BO/CO legs). |
| `tradedPrice` | float | The average traded price for the order. |
| `source` | string | The source from where the order was placed. |
| `fytoken` | string | A unique identifier for the symbol. |
| `offlineOrder`| boolean | `true` for After Market Orders (AMO). |
| `pan` | string | PAN of the client. |
| `clientId` | string | The client ID of the Fyers user. |
| `exchange` | int | The exchange where the order is placed. |
| `instrument` | int | Exchange instrument type. |

**Node.js Example**
```javascript
const FyersOrderSocket = require("fyers-api-v3").fyersOrderSocket;
var fyersOrderdata = new FyersOrderSocket("xxxx-1xx:eyjxxx");

fyersOrderdata.on("error", function (errmsg) {
    console.log(errmsg);
});

fyersOrderdata.on('connect', function () {
    fyersOrderdata.subscribe([fyersOrderdata.orderUpdates]);
});

fyersOrderdata.on('close', function () {
    console.log('closed');
});

// Listener for order update ticks
fyersOrderdata.on('orders', function (msg) {
    console.log("orders", msg);
});

fyersOrderdata.autoreconnect();
fyersOrderdata.connect();
```

**Sample Response**
```json
{
  "s": "ok",
  "orders": {
    "clientId": "XV20986",
    "id": "23080400089344",
    "exchOrdId": "1100000009596016",
    "qty": 1,
    "filledQty": 1,
    "limitPrice": 7.95,
    "type": 2,
    "fyToken": "101000000014366",
    "exchange": 10,
    "segment": 10,
    "symbol": "NSE:IDEA-EQ",
    "instrument": 0,
    "offlineOrder": false,
    "orderDateTime": "04-Aug-2023 10:12:58",
    "orderValidity": "DAY",
    "productType": "INTRADAY",
    "side": -1,
    "status": 90,
    "source": "W",
    "ex_sym": "IDEA",
    "description": "VODAFONE IDEA LIMITED",
    "orderNumStatus": "23080400089344:2"
  }
}
```
### Position Updates
Receive real-time updates on your current positions.

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol` | string | E.g: `NSE:SBIN-EQ`. |
| `id` | string | The unique ID for each position. |
| `buyAvg` | float | Average buy price. |
| `buyQty` | int | Total buy quantity. |
| `sellAvg` | float | Average sell price. |
| `sellQty` | int | Total sell quantity. |
| `netAvg` | float | Net average price. |
| `netQty` | int | Net quantity. |
| `side` | int | Shows if the position is long or short. |
| `qty` | int | Absolute value of `netQty`. |
| `productType` | string | The product type of the position. |
| `realized_profit`| float | The realized P&L of the position. |
| `fytoken` | string | A unique identifier for the symbol. |
| `cfBuyQty` | int | Carry forward buy quantity. |
| `cfSellQty` | int | Carry forward sell quantity. |
| `dayBuyQty` | int | Intraday buy quantity. |
| `daySellQty` | int | Intraday sell quantity. |
| `buyVal` | float | Total buy value of the position. |
| `sellVal` | float | Total sell value of the position. |

**Node.js Example**
```javascript
const FyersOrderSocket = require("fyers-api-v3").fyersOrderSocket;
var fyersOrderdata = new FyersOrderSocket("xxxx-1xx:eyjxxx");

fyersOrderdata.on("error", function (errmsg) {
    console.log(errmsg);
});

fyersOrderdata.on('connect', function () {
    // Subscribe to multiple updates
    fyersOrderdata.subscribe([
        fyersOrderdata.orderUpdates,
        fyersOrderdata.tradeUpdates,
        fyersOrderdata.positionUpdates,
        fyersOrderdata.edis,
        fyersOrderdata.pricealerts
    ]);
});

fyersOrderdata.on('close', function () {
    console.log('closed');
});

// Listener for position update ticks
fyersOrderdata.on('positions', function (msg) {
    console.log('positions', msg);
});

// Other listeners for orders, trades, etc.
fyersOrderdata.on('orders', (msg) => console.log("orders", msg));
fyersOrderdata.on('trades', (msg) => console.log('trades', msg));
fyersOrderdata.on('general', (msg) => console.log('general', msg)); // For price-alerts, EDIS

fyersOrderdata.autoreconnect();
fyersOrderdata.connect();
```

**Sample Response**```json
{
  "s": "ok",
  "positions": {
    "symbol": "NSE:IDEA-EQ",
    "id": "NSE:IDEA-EQ-INTRADAY",
    "buyAvg": 8,
    "buyQty": 1,
    "buyVal": 8,
    "sellAvg": 7.95,
    "sellQty": 1,
    "sellVal": 7.95,
    "netAvg": 0,
    "netQty": 0,
    "side": 0,
    "qty": 0,
    "productType": "INTRADAY",
    "realized_profit": -0.04999999999999982,
    "rbiRefRate": 1,
    "fyToken": "101000000014366",
    "exchange": 10,
    "segment": 10,
    "dayBuyQty": 1,
    "daySellQty": 1,
    "cfBuyQty": 0,
    "cfSellQty": 0,
    "qtyMulti_com": 1
  }
}
```

---

## Market Data Socket
The Market Data socket provides real-time access to live stock market data, including prices, volumes, and market depth.

For sample scripts and code examples, visit our [GitHub repository](https://github.com/FyersDev/fyers-api-sample-code).

### Symbol Update (Full Mode)
Receive comprehensive real-time data for subscribed symbols.

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol` | string | Symbol in Fyers symbology format. |
| `ltp` | float | Last Traded Price. |
| `prev_close_price`| float | Previous day's closing price. |
| `high_price` | float | Current day's high. |
| `low_price` | float | Current day's low. |
| `open_price` | float | Current day's open. |
| `ch` | float | Change in price today. |
| `chp` | float | Change in price in percentage. |
| `vol_traded_today`| int | Volume traded for the day. |
| `last_traded_time`| int | Last traded time (epoch). |
| `bid_size`, `ask_size`| int | Quantity available at the best bid/ask price. |
| `bid_price`, `ask_price`| float | The best bid/ask price. |
| `last_traded_qty`| int | Last traded quantity. |
| `tot_buy_qty` | int | Total buy quantity. |
| `tot_sell_qty` | int | Total sell quantity. |
| `avg_trade_price` | float | Average trade price. |
| `type` | string | Message type (`sf` for symbol data). |

**Node.js Example**
```javascript
const FyersSocket = require("fyers-api-v3").fyersDataSocket;
var fyersdata = new FyersSocket("xxxxx-1xx:ey....");

fyersdata.on("message", (message) => console.log(message));
fyersdata.on("error", (err) => console.log(err));
fyersdata.on("close", () => console.log("socket closed"));

fyersdata.on("connect", function () {
    fyersdata.subscribe(['NSE:TCS-EQ']); // Subscribe to symbols
    fyersdata.autoreconnect(); // Enable auto-reconnection
});

fyersdata.connect();
```
**Sample Response**```json
{
  "symbol": "NSE:TCS-EQ",
  "ltp": 3452.05,
  "vol_traded_today": 1956167,
  "last_traded_time": 1690885691,
  "exch_feed_time": 1690885758,
  "bid_size": 0,
  "ask_size": 313,
  "bid_price": 0,
  "ask_price": 3452.05,
  "last_traded_qty": 3,
  "tot_buy_qty": 0,
  "tot_sell_qty": 313,
  "avg_trade_price": 3443.71,
  "low_price": 3415,
  "high_price": 3460,
  "open_price": 3415,
  "prev_close_price": 3355.4,
  "ch": 96.65,
  "chp": 2.88,
  "type": "sf"
}
```

### Indices Update
Receive real-time data for subscribed indices.

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol` | string | Index symbol (e.g., `NSE:NIFTY50-INDEX`). |
| `ltp` | float | Last Traded Price. |
| `prev_close_price`| float | Previous day's closing price. |
| `high_price` | float | Current day's high. |
| `low_price` | float | Current day's low. |
| `open_price` | float | Current day's open. |
| `ch` | float | Change in price today. |
| `chp` | float | Change in price in percentage. |
| `type` | string | Message type (`if` for index data). |

**Node.js Example**
```javascript
// ... (setup is the same as symbol update)
fyersdata.on("connect", function () {
    fyersdata.subscribe(['NSE:NIFTY50-INDEX']);
    fyersdata.autoreconnect();
});
fyersdata.connect();
```

**Sample Response**
```json
{
  "symbol": "NSE:NIFTY50-INDEX",
  "ltp": 19733.55,
  "prev_close_price": 19753.8,
  "high_price": 19795.6,
  "low_price": 19704.6,
  "open_price": 19784,
  "ch": -20.25,
  "chp": -0.1,
  "type": "if"
}
```

### Market Depth Update
Receive real-time market depth (5 bids/asks) for subscribed symbols.

**Response Attributes**
| Attribute | Data Type | Description |
| :--- | :--- | :--- |
| `symbol` | string | Symbol in Fyers symbology format. |
| `bid_price1`-`bid_price5` | float | The top 5 bid prices. |
| `ask_price1`-`ask_price5` | float | The top 5 ask prices. |
| `bid_size1`-`bid_size5` | integer | The quantity at each bid price level. |
| `ask_size1`-`ask_size5` | integer | The quantity at each ask price level. |
| `bid_order1`-`bid_order5` | integer | The number of orders at each bid price level. |
| `ask_order1`-`ask_order5` | integer | The number of orders at each ask price level. |
| `type` | string | Message type (`dp` for depth data). |

**Node.js Example**
```javascript
// ... (setup is the same as symbol update)
fyersdata.on("connect", function () {
    // The 'true' flag indicates subscription for market depth
    fyersdata.subscribe(['NSE:IDEA-EQ'], true); 
    fyersdata.autoreconnect();
});
fyersdata.connect();
```

**Sample Response**
```json
{
  "symbol": "NSE:IDEA-EQ",
  "bid_price1": 8.25,
  "ask_price1": 0,
  "bid_size1": 245373,
  "ask_size1": 0,
  "bid_order1": 34,
  "ask_order1": 0,
  // ... other depth levels
  "type": "dp"
}
```

### Lite Mode
A lightweight mode to receive only the Last Traded Price (LTP) for subscribed symbols.

**Node.js Example**
```javascript
// ... (setup is the same as symbol update)
fyersdata.on("connect", function () {
    fyersdata.subscribe(['NSE:NIFTY50-INDEX', 'NSE:TCS-EQ']);
    
    // Set the data mode to LiteMode
    fyersdata.mode(fyersdata.LiteMode); 
    
    fyersdata.autoreconnect();
});
fyersdata.connect();
```
**Sample Response**
```json
{
  "symbol": "NSE:IDEA-EQ",
  "ltp": 7.55,
  "type": "sf"
}
```

### Unsubscribe from Symbols
To stop receiving data updates for specific symbols.

**Node.js Example**
```javascript
// ... inside the on("message") handler
function onmsg(message) {
    console.log(message);
    // Example: if LTP for SBIN crosses 500, sell and unsubscribe
    if (message.ltp > 500 && message.symbol == "NSE:SBIN-EQ") {
        // ... (sell position logic) ...
        fyersdata.unsubscribe(["NSE:SBIN-EQ"]);
    }
}
// ... (rest of the setup)
```

**Sample Response**
```json
{ 
  "type": "unsub", 
  "s": "ok", 
  "message": "successful", 
  "code": 200 
}
```
### Advanced Configuration: Queue Processing Interval
Customize how frequently data in the subscription queue is handled. The interval can be set between 1ms and 2000ms.

**Node.js Example**
```javascript
const FyersSocket = require("fyers-api-v3").fyersDataSocket;

var fyersdata = new FyersSocket("xxxxx-1xx:ey....");
// Set a 200ms interval for queue processing
fyersdata.setQueueProcessInterval(200);  

fyersdata.connect();
```
---

## Tick-by-Tick (TBT) Websocket Usage Guide
Tick-by-tick (TBT) data is the most granular market data, recording every trade and order book update in real-time. It is crucial for analyzing market microstructure and developing high-frequency trading strategies.

### Key Points
*   **Availability**: TBT data is exclusively available for NFO (NSE Futures & Options) and NSE (Equity) instruments.
*   **Data Formats**: Requests are sent in JSON, and responses are received in a compact, efficient **protobuf** format.
*   **Incremental Updates**: The server sends only the changes (diffs) since the last data packet. The official Fyers SDKs handle the process of applying these changes automatically.
*   **Snapshot on Subscription**: The first packet received upon subscription is a full snapshot of the market data. Subsequent packets are incremental updates.

### TBT WebSocket Connection [50 Market Depth]
| Feature | Description | Status |
| :--- | :--- | :--- |
| **TBT 50 Market Depth** | Provides 50 levels of market depth. | **Available** |
| TBT Quotes | Quote data such as LTP, LTT, LTQ, OI, etc. | Coming soon |
| TBT DayQuote | Day quote data such as daily OHLCV, OI. | Coming soon |
| TBT OHLCV | OHLCV data on the smallest timeframe. | Coming soon |

**Connection Details**
*   **Endpoint**: `wss://rtsocket-api.fyers.in/versova`
*   **Header**:
    *   **Key**: `Authorization`
    *   **Format**: `<appId:accessToken>`
    *   **Sample**: `7ABXUX38S-100:eyJ0eXAi**********qiTnzd2lGwS17s`

### Request Message Types
| Type | Purpose | JSON String Format |
| :--- | :--- | :--- |
| **Ping** | Keep the connection alive. | `"ping"` |
| **Subscribe**| Subscribe to symbols. | `{"type":1,"data":{"subs":1,"symbols":[...],"mode":"depth","channel":"1"}}`|
| **Unsubscribe**| Unsubscribe from symbols. | `{"type":1,"data":{"subs":-1,"symbols":[...],"mode":"depth","channel":"1"}}`|
| **Switch Channel**| Switch between active and paused channels. |`{"type":2,"data":{"resumeChannels":["1"],"pauseChannels":[]}}`|

### Response Message Types (Protobuf)
Responses are formatted using Protocol Buffers (protobuf). The `.proto` file, which defines the data structure, is available at the link below. Compiled files for Python, Node.js, and Go are also provided.

| Proto Version | Proto URL | Compiled Files URL |
| :--- | :--- | :--- |
| 1.0.0 | [msg.proto](https://public.fyers.in/tbtproto/1.0.0/msg.proto) | [protogencode.zip](https://public.fyers.in/tbtproto/1.0.0/protogencode.zip) |

**Key Data Structures**
| Structure | Field Explanation |
| :--- | :--- |
| `SocketMessage` | **type**: `MessageType.depth`, **feeds**: map of symbol ticker to `MarketFeed`, **snapshot**: boolean. |
| `MarketFeed` | **depth**: `Depth` datastructure, **feed_time**: epoch time, **token**: fytoken, **ticker**: symbol ticker. |
| `Depth` | **tbq**: total bid qty, **tsq**: total sell qty, **asks/bids**: arrays of `MarketLevel`. |
| `MarketLevel` | **price**: price level, **qty**: quantity at price, **nord**: number of orders at price. |

### Ratelimits
| Description | Limit |
| :--- | :--- |
| Active Connections Per App Per User | 3 |
| Symbols per connection [Market Depth] | 5 |
| Channels per connection | 50 (1-50) |