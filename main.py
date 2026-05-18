import requests
import json
import time
from tqdm import tqdm
import pandas as pd
import statistics
import math

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

        sample_buys = clean_buys[-10:] if len(clean_buys) > 10 else clean_buys


        #avg_sell
        if clean_sells:
            sample_size = min(10, len(clean_sells))

            avg_sell = (
                sum(clean_sells[:sample_size]) / sample_size
            )
        else:
            avg_sell = None

        #fair_price
        if len(clean_sells) >= 3:
            fair_price = statistics.median(clean_sells)
        else:
            fair_price = avg_sell


        #avg_buy
        if clean_buys:
            sample_size = min(10, len(clean_buys))

            avg_buy = (
                sum(clean_buys[-sample_size:]) / sample_size
            )
        else:
            avg_buy = None

        spread = avg_sell - avg_buy if avg_sell is not None and avg_buy is not None else None

        #confidence
        if fair_price and len(clean_sells) >= 5:

            threshold = fair_price * 0.10

            close_count = sum(
                1
                for price in clean_sells
                if abs(price - fair_price) <= threshold
            )

            confidence = round(
                (close_count / len(clean_sells)) * 100
            )

        else:
            confidence = 0

        volume = len(clean_sells) + len(clean_buys)

        #score
        if (
            fair_price is not None
            and confidence is not None
        ):

            score = (
                fair_price
                * math.log(volume + 1)
                * (confidence / 100)
            )

        else:
            score = 0


        if score >= 80:
            action = "TRADE"

        elif score >= 40:
            action = "WATCH"

        else:
            action = "IGNORE"

        if volume < 5:
            action = "IGNORE"

        return {
            "avg_sell": avg_sell,
            "fair_price": fair_price,
            "volume": volume,
            "confidence": confidence,
            "score": score,
            "action": action,
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
        #if the item is an arcane check the rank flippable
        if is_arcane:
            target_price = result["max_rank"]["fair_price"] if result["max_rank"]["fair_price"] is not None else None
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
        item_lookup = {item["slug"]: item for item in all_data}
        for slug, status in tqdm(item_status.items(), desc="Processing Interesting Market Data"):

            if status == "cheap":
                continue

            item = item_lookup.get(slug)

            if not item:
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

            "base_avg_sell": item["base"].get("avg_sell"),
            "base_fair_price": item["base"].get("fair_price"),
            "base_volume": item["base"].get("volume"),
            "base_confidence": item["base"].get("confidence"),
            "base_score": item["base"].get("score"),
            "base_action": item["base"].get("action"),
            

            "max_avg_sell": item.get("max_rank", {}).get("avg_sell"),
            "max_fair_price": item.get("max_rank", {}).get("fair_price"),
            "max_volume": item.get("max_rank", {}).get("volume"),
            "max_confidence": item.get("max_rank", {}).get("confidence"),
            "max_score": item.get("max_rank", {}).get("score"),
            "max_action": item.get("max_rank", {}).get("action"),
            
        })
    df = pd.DataFrame(rows)
    df.to_csv("exports/item_metrics.csv", index=False)

    