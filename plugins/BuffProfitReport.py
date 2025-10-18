import os
import pickle
import time

import apprise
import json5
import requests
from _decimal import Decimal
from apprise import AppriseAsset, AppriseAttachment

from utils.buff_helper import get_valid_session_for_buff
from utils.logger import handle_caught_exception
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER,
                          SUPPORT_GAME_TYPES)
from utils.tools import get_encoding


class BuffProfitReport:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.session = requests.session()
        self.asset = AppriseAsset()

    def init(self) -> bool:
        # Return True to stop if BUFF session is invalid
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            return True
        return False

    def get_all_buff_inventory(self, game="csgo"):
        self.logger.info("[BuffProfitReport] Fetching " + game + " BUFF inventory...")
        page_num = 1
        page_size = 300
        sort_by = "time.desc"
        state = "all"
        force = 0
        force_wear = 0
        url = "https://buff.163.com/api/market/steam_inventory"
        total_items = []
        while True:
            params = {
                "page_num": page_num,
                "page_size": page_size,
                "sort_by": sort_by,
                "state": state,
                "force": force,
                "force_wear": force_wear,
                "game": game
            }
            self.logger.info("[BuffProfitReport] Sleeping 15s to avoid ban")
            time.sleep(15)
            response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
            if response_json["code"] == "OK":
                items = response_json["data"]["items"]
                total_items.extend(items)
                if len(items) < page_size:
                    break
                page_num += 1
            else:
                self.logger.error(response_json)
                break
        return total_items

    def get_sell_history(self, game: str) -> dict:
        page_size = 100
        page_num = 1
        result = {}
        local_sell_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, "sell_history_" + game + "_full.json")
        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_sell_history = json5.load(f)
        except Exception as e:
            self.logger.error("[BuffProfitReport] Failed to read local sell history: " + str(e), exc_info=True)
        while True:
            should_break = False
            self.logger.info("[BuffProfitReport] Sleeping 15s to avoid ban")
            time.sleep(15)
            url = ('https://buff.163.com/api/market/sell_order/history?page_num=' + str(page_num) +
                   '&page_size=' + str(page_size) + '&game=' + game)
            response_json = self.session.get(url, headers=self.buff_headers).json()
            if response_json["code"] != "OK":
                self.logger.error("[BuffProfitReport] Failed to get historical sell orders")
                break
            items = response_json["data"]["items"]
            for item in items:
                if item["state"] != "SUCCESS":
                    continue
                item_copy = item.copy()
                trade_id = item_copy["id"]
                item_copy["item_details"] = response_json["data"]["goods_infos"][str(item_copy["goods_id"])]
                result[trade_id] = item_copy
                if not should_break and trade_id in local_sell_history:
                    self.logger.info("[BuffProfitReport] No newer orders. Stop pagination.")
                    should_break = True
            if should_break or len(items) < page_size:
                break
            page_num += 1
        if local_sell_history:
            for key in local_sell_history:
                if key not in result:
                    result[key] = local_sell_history[key]
        if result:
            with open(history_file_path, "w", encoding="utf-8") as f:
                json5.dump(result, f, indent=4)
        return result

    def get_buy_history(self, game: str) -> dict:
        local_history = {}
        history_file_path = os.path.join(SESSION_FOLDER, "buy_history_" + game + "_full.json")
        try:
            if os.path.exists(history_file_path):
                with open(history_file_path, "r", encoding=get_encoding(history_file_path)) as f:
                    local_history = json5.load(f)
        except Exception as e:
            self.logger.error("[BuffProfitReport] Failed to read local purchase history: " + str(e), exc_info=True)
        page_num = 1
        result = {}
        while True:
            self.logger.debug("[BuffProfitReport] Fetching " + game + " purchase history, page: " + str(page_num))
            url = ("https://buff.163.com/api/market/buy_order/history?page_num=" + str(page_num) +
                   "&page_size=300&game=" + game)
            response_json = self.session.get(url, headers=self.buff_headers).json()
            if response_json["code"] != "OK":
                self.logger.error("[BuffProfitReport] Failed to get historical orders")
                break
            items = response_json["data"]["items"]
            should_break = False
            for item in items:
                item_copy = item.copy()
                trade_id = item_copy["id"]
                item_copy["item_details"] = response_json["data"]["goods_infos"][str(item_copy["goods_id"])]
                if not should_break and trade_id in local_history:
                    self.logger.info("[BuffProfitReport] No newer orders. Stop pagination.")
                    should_break = True
                transact_time = 0
                if "transact_time" in item_copy and item_copy["transact_time"]:
                    transact_time = item_copy["transact_time"]
                # Only read the last 1.5 years
                if transact_time != 0 and time.time() - transact_time > int(365 * 1.5 * 24 * 60 * 60):
                    should_break = True
                    break
                if item_copy["state"] == "SUCCESS":
                    result[trade_id] = item_copy
            if len(items) < 300 or should_break:
                break
            page_num += 1
            self.logger.info("[BuffProfitReport] Sleeping 15s to avoid ban")
            time.sleep(15)
        if local_history:
            for key in local_history:
                if key not in result:
                    result[key] = local_history[key]
        if result:
            with open(history_file_path, "w", encoding="utf-8") as f:
                json5.dump(result, f, indent=4)
        return result

    def get_lowest_price(self, goods_id, game="csgo"):
        sleep_seconds_to_prevent_buff_ban = 30
        self.logger.info("[BuffProfitReport] Fetching BUFF lowest sell price")
        self.logger.info("[BuffProfitReport] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid ban")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        url = (
                "https://buff.163.com/api/market/goods/sell_order?goods_id="
                + str(goods_id)
                + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                + game)
        response_json = self.session.get(url, headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if len(response_json["data"]["items"]) == 0:
                self.logger.info("[BuffProfitReport] No listings")
                return Decimal("-1")
            lowest_price = Decimal(response_json["data"]["items"][0]["price"])
            return lowest_price
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffProfitReport] Failed to get BUFF lowest price. Check buff_cookies.txt or try later!")
            return Decimal("-1")

    def check_buff_account_state(self):
        response_json = self.session.get("https://buff.163.com/account/api/user/info", headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if "data" in response_json and "nickname" in response_json["data"]:
                return response_json["data"]["nickname"]
        self.logger.error("[BuffProfitReport] BUFF login expired. Check buff_cookies.txt or try later!")
        raise TypeError

    def exec(self):
        sleep_interval = 20
        self.logger.info("[BuffProfitReport] Profit report plugin started. Sleeping 90s to stagger with others")
        time.sleep(90)
        send_report_time = "20:30"
        servers = []
        if "buff_profit_report" in self.config:
            if "send_report_time" in self.config["buff_profit_report"]:
                send_report_time = self.config["buff_profit_report"]["send_report_time"]
            if "servers" in self.config["buff_profit_report"]:
                servers = self.config["buff_profit_report"]["servers"]
        if not servers:
            self.logger.error("[BuffProfitReport] No servers configured. Cannot send report")
            return
        try:
            self.logger.info("[BuffProfitReport] Preparing to log in to BUFF...")
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("[BuffProfitReport] Cookies detected. Attempting login")
            self.logger.info("[BuffProfitReport] Logged in to BUFF. Username: " + self.check_buff_account_state())
        except TypeError as e:
            handle_caught_exception(e, "[BuffProfitReport]", known=True)
            self.logger.error("[BuffProfitReport] BUFF login check failed. Check buff_cookies.txt or try later!")
            return
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("[BuffProfitReport] Steam session expired. Re-logging in...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(
                            self.steam_client.username, self.steam_client._password,
                            json5.dumps(self.steam_client.steam_guard)
                        )
                        self.logger.info("[BuffProfitReport] Steam session refreshed")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
            except Exception as e:
                self.logger.error("[BuffProfitReport] Error: " + str(e), exc_info=True)
                self.logger.info("[BuffProfitReport] Sleeping " + str(sleep_interval) + "s")
                time.sleep(sleep_interval)
                continue
            if time.strftime("%H:%M", time.localtime()) != send_report_time:
                time.sleep(sleep_interval)
                continue

            profit_in_inventory = {}
            total_profit_in_inventory = Decimal('0.00')
            total_profit_after_fee_in_inventory = Decimal('0.00')
            inventory_profit_result = []

            profit_in_sold = {}
            total_profit_in_sold = Decimal('0.00')
            total_profit_after_fee_in_sold = Decimal('0.00')
            sold_profit_result = []

            total_profit_in_missing = Decimal('0.00')
            total_profit_after_fee_in_missing = Decimal('0.00')
            missing_profit_result = []

            try:
                for game in SUPPORT_GAME_TYPES:
                    transaction_fee = Decimal('0.975')
                    if game['game'] == "dota2":
                        transaction_fee = Decimal('0.982')
                    self.logger.info("[BuffProfitReport] Fetching " + game["game"] + " purchase history...")
                    buy_history = self.get_buy_history(game["game"])
                    if not buy_history:
                        self.logger.error("[BuffProfitReport] " + game["game"] + " has no purchase history")
                        continue
                    self.logger.info("[BuffProfitReport] Sleeping 20s to avoid ban")
                    time.sleep(20)
                    self.logger.info("[BuffProfitReport] Fetching " + game["game"] + " BUFF inventory...")
                    game_inventory = self.get_all_buff_inventory(game=game["game"])
                    if not game_inventory:
                        self.logger.error("[BuffProfitReport] " + game["game"] + " has no inventory")
                        continue
                    self.logger.info("[BuffProfitReport] Sleeping 20s to avoid ban")
                    time.sleep(20)
                    self.logger.info("[BuffProfitReport] Fetching " + game["game"] + " sell history...")
                    sell_history = self.get_sell_history(game["game"])

                    inventory_items_to_pop = []
                    self.logger.info("[BuffProfitReport] Matching inventory items with purchase records...")
                    for item in game_inventory:
                        asset_id = item["asset_info"]["assetid"]
                        class_id = item["asset_info"]["classid"]
                        context_id = item["asset_info"]["contextid"]
                        trade_id_to_pop = ''
                        for trade_id in buy_history:
                            if buy_history[trade_id]["asset_info"]["assetid"] == asset_id and \
                                    buy_history[trade_id]["asset_info"]["classid"] == class_id and \
                                    buy_history[trade_id]["asset_info"]["contextid"] == context_id:
                                profit_in_inventory[trade_id] = {"buy": buy_history[trade_id], "sell": item}
                                trade_id_to_pop = trade_id
                                inventory_items_to_pop.append(item)
                                break
                        if trade_id_to_pop != '':
                            buy_history.pop(trade_id_to_pop)
                    for item in inventory_items_to_pop:
                        game_inventory.remove(item)

                    for trade_id in profit_in_inventory:
                        profit = (Decimal(profit_in_inventory[trade_id]['sell']['sell_min_price']) -
                                  Decimal(profit_in_inventory[trade_id]['buy']['price']))
                        real_price = Decimal(profit_in_inventory[trade_id]['sell']['sell_min_price']) * transaction_fee
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        real_price = Decimal(real_price) * Decimal('0.99')  # withdrawal fee
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        profit_after_fee = Decimal(real_price) - Decimal(profit_in_inventory[trade_id]['buy']['price'])
                        item_name = profit_in_inventory[trade_id]["buy"]["item_details"]["name"]
                        inventory_profit_result.append(
                            {
                                "item_name": item_name,
                                "profit": str(profit),
                                "profit_after_fee": str(profit_after_fee)
                            }
                        )
                        total_profit_in_inventory += profit
                        total_profit_after_fee_in_inventory += profit_after_fee

                    self.logger.info("[BuffProfitReport] Handling inventory items with remark-only purchase price...")
                    for item in game_inventory:
                        buy_price = Decimal("0")
                        if "asset_extra" in item and "remark" in item["asset_extra"]:
                            current_comment = item["asset_extra"]["remark"]
                            try:
                                buy_price = Decimal(current_comment.split(" ")[0])
                            except Exception:
                                buy_price = Decimal("0")
                        if buy_price != Decimal("0"):
                            profit = (Decimal(item['sell_min_price']) - buy_price)
                            real_price = Decimal(item['sell_min_price']) * transaction_fee
                            real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                            real_price = Decimal(real_price) * Decimal('0.99')
                            real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                            profit_after_fee = Decimal(real_price) - buy_price
                            item_name = item["name"]
                            inventory_profit_result.append(
                                {
                                    "item_name": item_name,
                                    "profit": str(profit),
                                    "profit_after_fee": str(profit_after_fee),
                                    "purchase_price": str(buy_price),
                                    "sold_price": item['sell_min_price']
                                }
                            )
                            total_profit_in_inventory += profit
                            total_profit_after_fee_in_inventory += profit_after_fee

                    self.logger.info("[BuffProfitReport] Matching sold records with purchases...")
                    for trade_id in sell_history:
                        asset_id = sell_history[trade_id]["asset_info"]["assetid"]
                        class_id = sell_history[trade_id]["asset_info"]["classid"]
                        context_id = sell_history[trade_id]["asset_info"]["contextid"]
                        trade_id_to_pop = ''
                        for trade_id2 in buy_history:
                            if buy_history[trade_id2]["asset_info"]["assetid"] == asset_id and \
                                    buy_history[trade_id2]["asset_info"]["classid"] == class_id and \
                                    buy_history[trade_id2]["asset_info"]["contextid"] == context_id:
                                profit_in_sold[trade_id] = {"buy": buy_history[trade_id2],
                                                            "sell": sell_history[trade_id]}
                                trade_id_to_pop = trade_id2
                                break
                        if trade_id_to_pop != '':
                            buy_history.pop(trade_id_to_pop)

                    for trade_id in profit_in_sold:
                        profit = (Decimal(profit_in_sold[trade_id]['sell']['price']) -
                                  Decimal(profit_in_sold[trade_id]['buy']['price']))
                        real_price = Decimal(profit_in_sold[trade_id]['sell']['price']) * transaction_fee
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        real_price = Decimal(real_price) * Decimal('0.99')
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        profit_after_fee = Decimal(real_price) - Decimal(profit_in_sold[trade_id]['buy']['price'])
                        item_name = profit_in_sold[trade_id]["buy"]["item_details"]["name"]
                        sold_profit_result.append(
                            {
                                "item_name": item_name,
                                "profit": str(profit),
                                "profit_after_fee": str(profit_after_fee),
                                "purchase_price": profit_in_sold[trade_id]['buy']['price'],
                                "sold_price": profit_in_sold[trade_id]['sell']['sell_min_price']
                            }
                        )
                        total_profit_in_sold += profit
                        total_profit_after_fee_in_sold += profit_after_fee

                    self.logger.info("[BuffProfitReport] Handling purchases without sell records...")
                    purchased_items = {}
                    for trade_id in buy_history:
                        goods_id = buy_history[trade_id]["goods_id"]
                        purchase_price = buy_history[trade_id]["price"]
                        if goods_id not in purchased_items:
                            purchased_items[goods_id] = []
                        purchased_items[goods_id].append({"purchase_price": purchase_price,
                                                          "name": buy_history[trade_id]["item_details"]["name"]})
                    purchased_items_stats = {}
                    for goods_id in purchased_items:
                        total_amount = len(purchased_items[goods_id])
                        total_price = Decimal("0")
                        for item in purchased_items[goods_id]:
                            total_price += Decimal(item["purchase_price"])
                        average_price = total_price / Decimal(total_amount)
                        purchased_items_stats[goods_id] = {
                            "total_amount": total_amount,
                            "total_price": total_price,
                            "average_price": average_price,
                            "name": purchased_items[goods_id][0]["name"]
                        }
                    for goods_id in purchased_items_stats:
                        lowest_price = self.get_lowest_price(goods_id, game=game["game"])
                        if lowest_price == Decimal("-1"):
                            continue
                        for key in ["total_price", "average_price"]:
                            purchased_items_stats[goods_id][key] = Decimal(
                                purchased_items_stats[goods_id][key]).quantize(Decimal('0.00'),
                                                                               rounding="ROUND_DOWN")
                        profit = (lowest_price - purchased_items_stats[goods_id]["average_price"])
                        real_price = lowest_price * transaction_fee
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        real_price = Decimal(real_price) * Decimal('0.99')
                        real_price = Decimal(real_price).quantize(Decimal('0.00'), rounding="ROUND_DOWN")
                        profit_after_fee = Decimal(real_price) - purchased_items_stats[goods_id]["average_price"]
                        item_name = purchased_items_stats[goods_id]["name"]
                        missing_profit_result.append(
                            {
                                "item_name": item_name,
                                "profit_per_item": str(profit),
                                "profit_per_item_after_fee": str(profit_after_fee),
                                "total_amount": purchased_items_stats[goods_id]["total_amount"],
                                "total_price": purchased_items_stats[goods_id]["total_price"],
                                "average_price": purchased_items_stats[goods_id]["average_price"],
                                "sold_price": lowest_price
                            }
                        )
                        total_profit_in_missing += (Decimal(profit) *
                                                    Decimal(purchased_items_stats[goods_id]["total_amount"]))
                        total_profit_after_fee_in_missing += (Decimal(profit_after_fee) *
                                                              Decimal(purchased_items_stats[goods_id]["total_amount"]))
                message = ""
                message += "Total inventory profit: " + str(total_profit_in_inventory) + " RMB\n"
                message += "Total inventory profit (after fees): " + str(total_profit_after_fee_in_inventory) + " RMB\n"
                for item in inventory_profit_result:
                    message += "Item: " + item["item_name"] + "\n"
                    message += "Profit: " + item["profit"] + " RMB\n"
                    message += "Profit (after fees): " + item["profit_after_fee"] + " RMB\n\n"
                message += "----------------------------------\n\n\n"
                message += "Total sold profit: " + str(total_profit_in_sold) + " RMB\n"
                message += "Total sold profit (after fees): " + str(total_profit_after_fee_in_sold) + " RMB\n"
                for item in sold_profit_result:
                    message += "Item: " + item["item_name"] + "\n"
                    message += "Profit: " + item["profit"] + " RMB\n"
                    message += "Profit (after fees): " + item["profit_after_fee"] + " RMB\n"
                    message += "Purchase price: " + item["purchase_price"] + " RMB\n"
                    message += "Sell price: " + item["sold_price"] + " RMB\n\n"
                message += "----------------------------------\n\n\n"
                message += "Total profit for inventory without sell records: " + str(total_profit_in_missing) + " RMB\n"
                message += "Total profit for inventory without sell records (after fees): " + str(total_profit_after_fee_in_missing) + " RMB\n"
                for item in missing_profit_result:
                    message += "Item: " + item["item_name"] + "\n"
                    message += "Profit: " + item["profit_per_item"] + " RMB\n"
                    message += "Profit (after fees): " + item["profit_per_item_after_fee"] + " RMB\n"
                    message += "Total profit: " + str(Decimal(item["profit_per_item"]) * Decimal(item["total_amount"])) + " RMB\n"
                    message += "Total profit (after fees): " + str(Decimal(item["profit_per_item_after_fee"]) * Decimal(item["total_amount"])) + " RMB\n"
                    message += "Average purchase price: " + str(item["average_price"]) + " RMB\n"
                    message += "Sell price: " + str(item["sold_price"]) + " RMB\n"
                    message += "Total amount: " + str(item["total_amount"]) + "\n"
                    message += "Total value: " + str(item["total_price"]) + " RMB\n\n"
                message += "----------------------------------\n\n\n"
                message += "Grand total profit: " + str(total_profit_in_inventory + total_profit_in_sold + total_profit_in_missing) + " RMB\n"

                report_file_path = os.path.join(SESSION_FOLDER, "report.txt")
                with open(report_file_path, 'w', encoding="utf-8") as f:
                    f.write(message)

                apprise_obj = apprise.Apprise(asset=self.asset)
                for server in servers:
                    apprise_obj.add(server)
                apprise_obj.notify(
                    title='BUFF Daily Profit Report',
                    body='BUFF Daily Profit Report',
                    attach=AppriseAttachment(report_file_path)
                )
            except Exception as e:
                handle_caught_exception(e, "[BuffProfitReport]", known=True)
                self.logger.error("[BuffProfitReport] Failed to generate BUFF profit report. Error: " + str(e), exc_info=True)
            time.sleep(sleep_interval)
