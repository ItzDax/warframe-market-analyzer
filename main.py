import requests
import json
import time
from tqdm import tqdm
import pandas as pd
import statistics

BASE_URL = "https://api.warframe.market/v2/"

def get_all_items_json(): #1 api call
    url = f"{BASE_URL}items"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        with open("data/data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print("✅ General API data saved to data.json")
        items = data["data"]
        clean_items = []
        for item in items:
            clean_item = {
                "id": item["id"],
                "slug": item["slug"],
                "name": item["i18n"]["en"]["name"],
                "tags": item.get("tags", [])
            }
            clean_items.append(clean_item)

        with open("data/clean_data.json", "w", encoding="utf-8") as f:
            json.dump(clean_items, f, indent=4, ensure_ascii=False)
        print("✅ Cleaned item data saved to clean_data.json")
        return clean_items
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching all data: {e}")
        return None

def get_orders(slug, retries = 2): #1 api call
    url = f"{BASE_URL}orders/item/{slug}"
    for attempt in range(retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            with open(f"data/orders.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return data
        except requests.exceptions.RequestException as e:
            print(f"Retry {attempt+1} for {slug}: {e}")
            time.sleep(2)
    return None
        
def analyze_orders(item, data):
    if not data or "data" not in data:
        return None
    is_arcane = "arcane_enhancement" in item.get("tags", [])
    orders = [
    o for o in data["data"]
    if o["user"]["status"] != "offline"
    ]
    sells = [o for o in orders if o["type"] == "sell"]
    buys = [o for o in orders if o["type"] == "buy"]

    ranks = [
    o.get("rank")
    for o in (sells + buys)
    if o.get("rank") is not None
    ]

    if not sells or not buys:
        return None

    def analyze_group(sells, buys):
        if not sells and not buys:
            return None

        sell_prices = sorted([
            o["platinum"]
            for o in sells
        ])

        buy_prices = sorted([
            o["platinum"]
            for o in buys
        ])


        def remove_outliers(data):
            if len(data) < 4:
                return data

            q1 = statistics.quantiles(data, n=4)[0]
            q3 = statistics.quantiles(data, n=4)[2]

            iqr = q3 - q1

            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr

            return [
                x for x in data
                if lower <= x <= upper
            ]


        clean_sells = remove_outliers(sell_prices)
        clean_buys = remove_outliers(buy_prices)

        sample_sells = clean_sells[:10] if len(clean_sells) > 10 else clean_sells
        sample_buys = clean_buys[-10:] if len(clean_buys) > 10 else clean_buys

        avg_sell = sum(sample_sells) / len(sample_sells) if sample_sells else None
        avg_buy = sum(sample_buys) / len(sample_buys) if sample_buys else None

        lowest_sell = min(o["platinum"] for o in sells) if sells else None
        highest_buy = max(o["platinum"] for o in buys) if buys else None

        if avg_sell is not None and avg_buy is not None:
            mid_point = (avg_sell + avg_buy) / 2
        else:
            mid_point = None

        if lowest_sell is not None and highest_buy is not None:
            compression = lowest_sell - highest_buy

            if compression > 0:
                status = "Buyers underpaying"
            elif compression < 0:
                status = "Arbitrage opportunity"
            else:
                status = "no gap"
        else:
            compression = None

            if sells and not buys:
                status = "No buyers"
            elif buys and not sells:
                status = "No sellers"
            else:
                status = "No market"

        sell_count = len(clean_sells)
        buy_count = len(clean_buys)
        spread = avg_sell - avg_buy if avg_sell is not None and avg_buy is not None else None
        spread_percentage = spread/mid_point if spread is not None and mid_point is not None else None
        volatility = statistics.stdev(clean_sells) if len(clean_sells) > 1 else None
        relative_volatility = volatility/mid_point if volatility is not None and mid_point is not None else None

        confidence = 100

        confidence += min(buy_count, 20) * 1.5
        confidence += min(sell_count, 20) * 1.0

        confidence -= spread_percentage * 100 if spread_percentage is not None else 0
        confidence -= relative_volatility * 50 if relative_volatility is not None else 0

        confidence = round(max(0, min(confidence, 100)))

        liquidity = (buy_count * sell_count) / (spread + 1) if spread is not None else 0

        volume = buy_count + sell_count

        stability = 1-volatility/mid_point if volatility is not None and mid_point is not None else None

        demand = buy_count / (sell_count + 1) if sell_count + 1 > 0 else None
        
        execution = liquidity * confidence if liquidity is not None and confidence is not None else None

        risk = (1 - stability) + (1 / (volume + 1)) if stability is not None and volume is not None else None

        return {
            "lowest_sell": lowest_sell,
            "highest_buy": highest_buy,
            "avg_sell": avg_sell,
            "avg_buy": avg_buy,
            "mid_point": mid_point,
            "confidence": confidence,
            "compression": compression,
            "status": status,
            "liquidity": liquidity,
            "spread": spread,
            "spread_percentage": spread_percentage,
            "volume": volume,
            "stability": stability,
            "demand": demand,
            "execution": execution,
            "risk": risk
        }

    result = {
        "base": analyze_group(sells, buys)
    }

    if ranks:
        min_rank = min(ranks)
        max_rank = max(ranks)

        base_sells = [o for o in sells if o.get("rank") == min_rank]
        base_buys = [o for o in buys if o.get("rank") == min_rank]

        max_sells = [o for o in sells if o.get("rank") == max_rank]
        max_buys = [o for o in buys if o.get("rank") == max_rank]

        result["base"] = analyze_group(base_sells, base_buys)
        result["max_rank"] = analyze_group(max_sells, max_buys)
        #if the item is an arcane check the rank compression
        if is_arcane:
            target_price = result["max_rank"]["avg_buy"] if result["max_rank"]["avg_buy"] is not None else None
            if target_price is None:
                return
            cheap_pool = [
                o for o in base_sells
                if o["platinum"] * 21 < target_price
            ]
            cheap_pool.sort(key=lambda o: o["platinum"])

            total_qty = 0
            total_cost = 0

            for o in cheap_pool:
                qty = o.get("quantity", 1)

                take = min(qty, 21 - total_qty)

                total_qty += take
                total_cost += take * o["platinum"]

                if total_qty >= 21:
                    break
            
            can_flip = total_qty >= 21

            profit = None
            if can_flip == True:
                profit = (target_price * 21) - total_cost
                result["flip_check"] = {
                "can_flip": can_flip,
                "units_found": total_qty,
                "cost": total_cost,
                "target_revenue": target_price * 21 if can_flip else None,
                "profit": profit
                }


    else:
        result = {
            "base": analyze_group(sells, buys)
        }

    return result

def process_item(item): #1 api call
    slug = item["slug"]

    data = get_orders(slug)
    if not data or "data" not in data:
        print(f"❌ No valid order data for {slug}")
        return
    
    metrics = analyze_orders(item, data)

    if not metrics:
        return
    
    avg_sell_base = metrics["base"]["avg_sell"]
    avg_sell_max = metrics.get("max_rank", {}).get("avg_sell")

    if not avg_sell_base:
        return
    
    if slug not in item_status:
        if avg_sell_base < 8:
            if avg_sell_max is None or avg_sell_max < 8:
                item_status[slug] = "cheap"
                print(f"🟢 {slug} marked as cheap (avg sell base: {avg_sell_base}, max: {avg_sell_max}) ")
        else:
            item_status[slug] = "expensive"
    
    item_metrics[slug] = {
        **metrics,
        "name": item["name"]
    }

if __name__ == "__main__":

    RATE_LIMIT = 3 #request per second
    DELAY = 1 / RATE_LIMIT #.3 second delay
    TESTING_COUNT = 10 #number of items to process in testing mode

    #load item status
    try:
        with open("data/item_status.json", "r") as f:
            item_status = json.load(f)
    except FileNotFoundError:
        item_status = {}
    
    #load item metrics
    try:
        with open("data/item_metrics.json", "r") as f:
            item_metrics = json.load(f)
    except FileNotFoundError:
        item_metrics = {}

    #load item list
    all_data = get_all_items_json()
    test_items = all_data[:TESTING_COUNT]

    #load list of arcanes
    arcane_items = [
    item for item in all_data
    if "arcane_enhancement" in item.get("tags", [])
    ]

    print("\n=== Warframe Market Analyzer ===")
    print("1. Initialize testing data")
    print("2. Initialize full market data")
    print("3. Process interesting market data")

    choice = input("\nSelect option (1-3): ").strip()

    if choice == "1":
        for item in tqdm(test_items, desc="Initializing Testing Market Data"):
            process_item(item)
            time.sleep(DELAY)

    elif choice == "2":
        for item in tqdm(all_data, desc="Initializing Market Data"):
            process_item(item)
            time.sleep(DELAY)

    elif choice == "3":
        for item in tqdm(all_data, desc="Processing Interesting Market Data"):
            slug=item["slug"]
    
            status = item_status.get(slug)

            if status is None:
                continue
            if status == "cheap":
                continue

            process_item(item)
            time.sleep(DELAY)

    else:
        print("Invalid selection.")
    

    #save new item statuses
    with open("data/item_status.json", "w") as f:
        json.dump(item_status, f, indent=4)

    #save new item metrics
    with open("data/item_metrics.json", "w") as f:
        json.dump(item_metrics, f, indent=4)

    rows = []

    for slug, item in item_metrics.items():
        rows.append({
            "slug": slug,
            "name": item.get("name"),

            "base_lowest_sell": item["base"].get("lowest_sell"),
            "base_highest_buy": item["base"].get("highest_buy"),
            "base_avg_sell": item["base"].get("avg_sell"),
            "base_avg_buy": item["base"].get("avg_buy"),
            "base_mid_point": item["base"].get("mid_point"),
            "base_confidence": item["base"].get("confidence"),
            "base_compression": item["base"].get("compression"),
            "base_liquidity": item["base"].get("liquidity"),
            "base_status": item["base"].get("status"),
            "base_spread": item["base"].get("spread"),
            "base_spread_percentage": item["base"].get("spread_percentage"),
            "base_volume": item["base"].get("volume"),
            "base_stability": item["base"].get("stability"),
            "base_demand": item["base"].get("demand"),
            "base_execution": item["base"].get("execution"),
            "base_risk": item["base"].get("risk"),

            "max_lowest_sell": item.get("max_rank", {}).get("lowest_sell"),
            "max_highest_buy": item.get("max_rank", {}).get("highest_buy"),
            "max_avg_sell": item.get("max_rank", {}).get("avg_sell"),
            "max_avg_buy": item.get("max_rank", {}).get("avg_buy"),
            "max_mid_point": item.get("max_rank", {}).get("mid_point"),
            "max_confidence": item.get("max_rank", {}).get("confidence"),
            "max_compression": item.get("max_rank", {}).get("compression"),
            "max_liquidity": item.get("max_rank", {}).get("liquidity"),
            "max_status": item.get("max_rank", {}).get("status"),
            "max_spread": item.get("max_rank", {}).get("spread"),
            "max_spread_percentage": item.get("max_rank", {}).get("spread_percentage"),
            "max_volume": item.get("max_rank", {}).get("volume"),
            "max_stability": item.get("max_rank", {}).get("stability"),
            "max_demand": item.get("max_rank", {}).get("demand"),
            "max_execution": item.get("max_rank", {}).get("execution"),
            "max_risk": item.get("max_rank", {}).get("risk")
        })
    df = pd.DataFrame(rows)
    df.to_csv("exports/item_metrics.csv", index=False)

    