import datetime
import os
import pickle
import random
import time

import apprise
import json5
import requests
from apprise import AppriseAsset
from bs4 import BeautifulSoup

from utils.BuffApiCrypt import BuffApiCrypt
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import handle_caught_exception
from utils.static import (BUFF_COOKIES_FILE_PATH, SESSION_FOLDER,
                          SUPPORT_GAME_TYPES)
from utils.tools import get_encoding
from utils.multi_account_manager import (
    initialize_multi_account_manager,
    get_multi_account_manager,
)


def format_str(text: str, trade):
    for good in trade["goods_infos"]:
        good_item = trade["goods_infos"][good]
        created_at_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade["created_at"]))
        text = text.format(
            item_name=good_item["name"],
            steam_price=good_item["steam_price"],
            steam_price_cny=good_item["steam_price_cny"],
            buyer_name=trade["bot_name"],
            buyer_avatar=trade["bot_avatar"],
            order_time=created_at_time_str,
            game=good_item["game"],
            good_icon=good_item["original_icon_url"],
        )
    return text


def merge_buy_orders(response_data: dict):
    orders = response_data["items"]
    user_info = response_data["user_infos"]
    for order in orders:
        order["user"] = user_info[order["user_id"]]
        del order["user_id"]
        pay_method = order["pay_method"]
        if pay_method == 43:
            order["supported_pay_method"] = ["Alipay", "WeChat"]
        elif pay_method == 3:
            order["supported_pay_method"] = ["Alipay"]
        elif pay_method == 1:
            order["supported_pay_method"] = ["WeChat"]
        else:
            order["supported_pay_method"] = []
    return orders


class BuffAutoOnSale:
    buff_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
    }

    def __init__(self, logger, steam_client, steam_client_mutex, config):
        self.logger = logger
        self.steam_client = steam_client
        self.config = config
        self.steam_client_mutex = steam_client_mutex
        self.asset = AppriseAsset()
        self.session = requests.session()
        self.lowest_price_cache = {}
        self.unfinish_supply_order_list = []  # Orders waiting for BUFF to create offers, then confirm; [{order_id, create_time}]
        self._current_steamid = "unknown"
        # Debug/dry-run mode: when enabled, we do NOT send any BUFF or Steam write requests.
        # Instead, we log exactly what would happen. Sleeps are also skipped or minimized.
        self.debug = False
        try:
            if "buff_auto_on_sale" in self.config and "debug" in self.config["buff_auto_on_sale"]:
                self.debug = bool(self.config["buff_auto_on_sale"]["debug"])
        except Exception:
            # Keep default
            pass

        # Simple pricing strategy: find the first significant price jump and target that tier
        pricing_cfg = self.config.get("buff_auto_on_sale", {}).get("pricing", {})
        self.pricing_jump_threshold = float(pricing_cfg.get("jump_threshold", 0.08))  # 8% jump to target higher tier
        self.pricing_undercut_amount = float(pricing_cfg.get("undercut_amount", 0.01))  # Always undercut by 0.01 RMB
        self.pricing_max_check = int(pricing_cfg.get("max_check", 8))  # Check up to 8th listing

        # Multi-account rotation support
        self.multi_account_rotation = False
        self.current_account_index = 0
        self.available_accounts = []
        
        try:
            # Auto-detect multi-account support from steam_account_info.json5
            if initialize_multi_account_manager(self.config):
                mam = get_multi_account_manager()
                if mam:
                    # Get all enabled accounts
                    all_accounts = mam.get_all_accounts()
                    self.available_accounts = [acc for acc in all_accounts if acc.get("enabled", True)]
                    
                    if len(self.available_accounts) >= 2:
                        self.multi_account_rotation = True
                        self.logger.info("[BuffAutoOnSale] Multi-account rotation auto-enabled with " + str(len(self.available_accounts)) + " accounts")
                    else:
                        self.logger.info("[BuffAutoOnSale] Only " + str(len(self.available_accounts)) + " account(s) available; using single account mode")
                else:
                    self.logger.error("[BuffAutoOnSale] Failed to get multi-account manager; using provided single account")
            else:
                self.logger.error("[BuffAutoOnSale] Failed to initialize multi-account manager; using provided single account")
        except Exception as e:
            handle_caught_exception(e, known=True)
            self.logger.error("[BuffAutoOnSale] Multi-account setup failed; using provided single account")

        # Populate current steamid if available
        try:
            self._current_steamid = str(self.steam_client.get_steam64id_from_cookies())
        except Exception:
            self._current_steamid = "unknown"

    def init(self) -> bool:
        # Return True to stop if BUFF session is invalid
        if get_valid_session_for_buff(self.steam_client, self.logger) == "":
            return True
        return False

    def check_buff_account_state(self):
        response_json = self.session.get("https://buff.163.com/account/api/user/info",
                                         headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if "data" in response_json:
                if "nickname" in response_json["data"]:
                    return response_json["data"]["nickname"]
        self.logger.error("[BuffAutoOnSale] BUFF login expired. Check buff_cookies.txt or try later!")
        # No exception raised in original; keep behavior

    def get_buff_inventory(self, page_num=1, page_size=500, sort_by="time.desc", state="all", force=0, force_wear=1,
                           game="csgo", app_id=730):
        url = "https://buff.163.com/api/market/steam_inventory"
        params = {
            "page_num": page_num,
            "page_size": page_size,
            "sort_by": sort_by,
            "state": state,
            "force": force,
            "force_wear": force_wear,
            "game": game,
            "appid": app_id,
            # attach current steamid for multi-account inventory fetching
            "steamid": str(self._current_steamid)
        }
        self.logger.info("[BuffAutoOnSale] Fetching inventory | game=" + str(game) + " appid=" + str(app_id) + " steamid=" + str(self._current_steamid))
        response_json = self.session.get(url, headers=self.buff_headers, params=params).json()
        if response_json["code"] == "OK":
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] Failed to fetch BUFF inventory. Check buff_cookies.txt or try later!")
            return {}

    def put_item_on_sale(self, items, price, description="", game="csgo", app_id=730, use_range_price=False):
        """
        Put the provided `items` on sale in BUFF at a calculated or explicit price.

        Debug behavior (self.debug=True):
        - No network calls that would change state (no create/preview/supply requests).
        - Sleeps are skipped.
        - Logs will describe intended actions, including computed prices and targets.

        Parameters:
        - items: list of Steam asset dicts expected by BUFF (assetid/classid/instanceid/etc.)
        - price: -1 to auto-compute via market lowest price; otherwise explicit float
        - description: text description attached to listing
        - game/app_id: BUFF game scope
        - use_range_price: if True for CS2 items with wear, compute via wear range
        """
        if game != "csgo" and use_range_price:
            self.logger.warning("[BuffAutoOnSale] Wear-range pricing supported for CSGO only. Auto-disabled.")
            use_range_price = False
        wear_ranges = [{'min': 0, 'max': 0.01},
                       {'min': 0.01, 'max': 0.02},
                       {'min': 0.02, 'max': 0.03},
                       {'min': 0.03, 'max': 0.04},
                       {'min': 0.04, 'max': 0.07},
                       {'min': 0.07, 'max': 0.08},
                       {'min': 0.08, 'max': 0.09},
                       {'min': 0.09, 'max': 0.10},
                       {'min': 0.10, 'max': 0.11},
                       {'min': 0.11, 'max': 0.15},
                       {'min': 0.15, 'max': 0.18},
                       {'min': 0.18, 'max': 0.21},
                       {'min': 0.21, 'max': 0.24},
                       {'min': 0.24, 'max': 0.27},
                       {'min': 0.27, 'max': 0.38},
                       {'min': 0.38, 'max': 0.39},
                       {'min': 0.39, 'max': 0.40},
                       {'min': 0.40, 'max': 0.41},
                       {'min': 0.41, 'max': 0.42},
                       {'min': 0.42, 'max': 0.45},
                       {'min': 0.45, 'max': 0.50},
                       {'min': 0.50, 'max': 0.63},
                       {'min': 0.63, 'max': 0.76},
                       {'min': 0.76, 'max': 0.9},
                       {'min': 0.9, 'max': 1}]
        sleep_seconds_to_prevent_buff_ban = 10
        if 'sleep_seconds_to_prevent_buff_ban' in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        supply_buy_orders = False
        only_auto_accept = True
        supported_payment_method = ["Alipay"]
        min_price = 0
        if 'buy_order' in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
            supply_buy_orders = True
            if 'only_auto_accept' in self.config["buff_auto_on_sale"]["buy_order"]:
                only_auto_accept = self.config["buff_auto_on_sale"]["buy_order"]["only_auto_accept"]
            if 'supported_payment_method' in self.config["buff_auto_on_sale"]["buy_order"]:
                supported_payment_method = self.config["buff_auto_on_sale"]["buy_order"]["supported_payment_method"]
            if 'min_price' in self.config["buff_auto_on_sale"]["buy_order"]:
                min_price = self.config["buff_auto_on_sale"]["buy_order"]["min_price"]
        url = "https://buff.163.com/api/market/sell_order/create/manual_plus"
        assets = []
        for item in items:
            has_requested_refresh = False
            refresh_count = 0
            self.logger.info("[BuffAutoOnSale] Parsing " + item["market_hash_name"])
            min_paint_wear = 0
            max_paint_wear = 1.0
            paint_wear = -1
            if use_range_price:
                done = False
                while not done:
                    has_wear = False
                    wear_keywords = ['(Factory New)', '(Minimal Wear)', '(Field-Tested)',
                                     '(Well-Worn)', '(Battle-Scarred)']
                    for wear_keyword in wear_keywords:
                        if wear_keyword in item["market_hash_name"]:
                            has_wear = True
                            break
                    if not has_wear:
                        self.logger.info("[BuffAutoOnSale] Item has no wear. Using type-level lowest price.")
                        done = True
                        break
                    self.logger.info("[BuffAutoOnSale] Fetching wear range...")
                    self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
                    time.sleep(sleep_seconds_to_prevent_buff_ban)
                    asset = {
                        "assetid": item["assetid"],
                        "classid": item["classid"],
                        "instanceid": item["instanceid"],
                        "contextid": item["contextid"],
                        "market_hash_name": item["market_hash_name"],
                        "price": "",
                        "income": "",
                        "has_market_min_price": False,
                        "game": game,
                        "goods_id": item["goods_id"]
                    }
                    data = {"game": game, "assets": [asset], "steamid": str(self._current_steamid)}
                    # Always fetch wear data - this is needed for price calculations, not a sale action
                    self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
                    csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
                    headers = {
                        "User-Agent": self.buff_headers["User-Agent"],
                        "X-CSRFToken": csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/json",
                        "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                    }
                    preview_url = "https://buff.163.com/market/sell_order/preview/manual_plus"
                    try:
                        resp = self.session.post(preview_url, json=data, headers=headers)
                        resp.raise_for_status()  # Raise an exception for bad status codes
                        response_json = resp.json()
                    except requests.exceptions.JSONDecodeError:
                        self.logger.error("[BuffAutoOnSale] Failed to get wear range. Using type-level lowest price.")
                        done = True
                        break
                    except requests.exceptions.RequestException:
                        self.logger.error("[BuffAutoOnSale] Failed to get wear range. Using type-level lowest price.")
                        done = True
                        break
                    except Exception:
                        self.logger.error("[BuffAutoOnSale] Failed to get wear range. Using type-level lowest price.")
                        done = True
                        break
                    
                    if 'data' not in response_json:
                        self.logger.error(response_json)
                        self.logger.error("[BuffAutoOnSale] Failed to get wear range. Using type-level lowest price.")
                        done = True
                        break
                    response_data = response_json["data"]
                    bs = BeautifulSoup(response_data, "html.parser")
                    paint_wear_p = bs.find("p", {"class": "paint-wear"})
                    try:
                        suggested_price = int(bs.find("span", {"class": "custom-currency"}).attrs.get("data-price"))
                    except Exception:
                        suggested_price = -1
                    if suggested_price != -1 and suggested_price < 10:
                        self.logger.info("[BuffAutoOnSale] Price below 10. Using type-level lowest price.")
                        done = True
                        break
                    if paint_wear_p is not None:
                        paint_wear = paint_wear_p.text.replace("磨损:", "").replace(" ", "").replace("\n", "")
                        # Remove "Float:" prefix if present
                        if paint_wear.startswith("Float:"):
                            paint_wear = paint_wear[6:]  # Remove "Float:" prefix
                        paint_wear = float(paint_wear)
                        for wear_range in wear_ranges:
                            if wear_range['min'] <= paint_wear < wear_range['max']:
                                min_paint_wear = wear_range['min']
                                max_paint_wear = wear_range['max']
                                done = True
                                break
                        if not done:
                            self.logger.error("[BuffAutoOnSale] Code error. Unable to parse wear: " + str(paint_wear))
                            self.logger.error("[BuffAutoOnSale] Using type-level lowest price.")
                            done = True
                            break
                    else:
                        if not has_requested_refresh:
                            has_requested_refresh = True
                            self.logger.info("[BuffAutoOnSale] Item not parsed yet. Requesting parse...")
                            self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
                            time.sleep(sleep_seconds_to_prevent_buff_ban)
                            # Always request asset parsing - this is needed for price calculations, not a sale action
                            post_url = "https://buff.163.com/api/market/csgo_asset/change_state_cs2"
                            data = {
                                "assetid": item["assetid"],
                                "contextid": item["contextid"]
                            }
                            self.session.get("https://buff.163.com/api/market/steam_trade",
                                             headers=self.buff_headers)
                            csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
                            headers = {
                                "User-Agent": self.buff_headers["User-Agent"],
                                "X-CSRFToken": csrf_token,
                                "X-Requested-With": "XMLHttpRequest",
                                "Content-Type": "application/json",
                                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                            }
                            response_json = self.session.post(post_url, json=data, headers=headers).json()
                            if response_json["code"] == "OK":
                                self.logger.info("[BuffAutoOnSale] Parse request succeeded")
                                continue
                            else:
                                self.logger.error(response_json)
                                self.logger.error("[BuffAutoOnSale] Parse request failed. Using type-level lowest price.")
                                done = True
                        else:
                            refresh_count += 1
                            if refresh_count >= 5:
                                self.logger.error("[BuffAutoOnSale] Parse failed. Using type-level lowest price.")
                                done = True
                                break
                            self.logger.error("[BuffAutoOnSale] Item not parsed yet...")
                            continue
                    self.logger.info(
                        "[BuffAutoOnSale] Using wear-range lowest price. Range: " + str(min_paint_wear) + " - " +
                        str(max_paint_wear))
            sell_price = price
            if sell_price == -1:
                # Use pricing strategy rather than raw lowest price
                sell_price = self.compute_listing_price(
                    goods_id=item["goods_id"],
                    game=game,
                    app_id=app_id,
                    min_paint_wear=min_paint_wear,
                    max_paint_wear=max_paint_wear,
                )
            if supply_buy_orders:
                highest_buy_order = self.get_highest_buy_order(item["goods_id"], game, app_id, paint_wear=paint_wear,
                                                               require_auto_accept=only_auto_accept,
                                                               supported_payment_methods=supported_payment_method)
                if sell_price <= min_price or sell_price <= float(highest_buy_order.get("price", -1)):
                    # Supply directly to the highest buy order
                    self.logger.info("[BuffAutoOnSale] Item " + item["market_hash_name"] +
                                     " will be supplied to the highest buy order " + str(highest_buy_order["price"]))
                    success = self.supply_item_to_buy_order(item, highest_buy_order, game, app_id)
                    if success:
                        if "on_sale_notification" in self.config["buff_auto_on_sale"]:
                            item_list = item["market_hash_name"] + " : " + highest_buy_order["price"] + "\n"
                            apprise_obj = apprise.Apprise(asset=self.asset)
                            for server in self.config["buff_auto_on_sale"]["servers"]:
                                apprise_obj.add(server)
                            apprise_obj.notify(
                                title=self.config["buff_auto_on_sale"]["on_sale_notification"]["title"].format(
                                    game=game, sold_count=len(assets)),
                                body=self.config["buff_auto_on_sale"]["on_sale_notification"]["body"].format(
                                    game=game, sold_count=len(assets), item_list=item_list)
                            )
                        continue
            if sell_price != -1:
                # Smart pricing already includes the undercut, so no additional -0.01 needed
                if sell_price < 0.02:
                    sell_price = 0.02
                self.logger.info("[BuffAutoOnSale] Item " + item["market_hash_name"] +
                                 " will be listed at " + str(sell_price))
                assets.append(
                    {
                        "appid": str(app_id),
                        "assetid": item["assetid"],
                        "classid": item["classid"],
                        "instanceid": item["instanceid"],
                        "contextid": item["contextid"],
                        "market_hash_name": item["market_hash_name"],
                        "price": sell_price,
                        "income": sell_price,
                        "desc": description,
                    }
                )
        if len(assets) == 0:
            return {}
        # Log batch size for visibility (e.g., 10 items per request)
        self.logger.info("[BuffAutoOnSale] Listing batch of " + str(len(assets)) + " item(s) | steamid=" + str(self._current_steamid))
        data = {"appid": str(app_id), "game": game, "assets": assets, "steamid": str(self._current_steamid)}
        if self.debug:
            # In debug mode, do not actually list items. Log intended payload and return a fake response shape.
            self.logger.info("[BuffAutoOnSale] [DEBUG] Would POST create listings: " + json5.dumps(data))
            response_json = {"code": "OK", "data": {"debug": True, "assets": assets}}
        else:
            self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
            csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
            headers = {
                "User-Agent": self.buff_headers["User-Agent"],
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
            }
        response_json = self.session.post(url, json=data, headers=headers).json()
        if response_json["code"] == "OK":
            if "on_sale_notification" in self.config["buff_auto_on_sale"]:
                item_list = ""
                for asset in assets:
                    item_list += asset["market_hash_name"] + " : " + str(asset["price"]) + "\n"
                apprise_obj = apprise.Apprise(asset=self.asset)
                for server in self.config["buff_auto_on_sale"]["servers"]:
                    apprise_obj.add(server)
                apprise_obj.notify(
                    title=self.config["buff_auto_on_sale"]["on_sale_notification"]["title"].format(
                        game=game, sold_count=len(assets)),
                    body=self.config["buff_auto_on_sale"]["on_sale_notification"]["body"].format(
                        game=game, sold_count=len(assets), item_list=item_list)
                )
            return response_json["data"]
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] Failed to list BUFF items. Check buff_cookies.txt or try later!")
            return {}

    def get_highest_buy_order(self, goods_id, game="csgo", app_id=730, paint_wear=-1, require_auto_accept=True,
                              supported_payment_methods=None):
        """
        Fetch the highest buy order for a goods_id from BUFF market.
        
        Debug behavior:
        - Still fetches real market data (this is needed for buy order decisions)
        - Only skips sleep delays in debug mode for faster testing
        """
        sleep_seconds_to_prevent_buff_ban = 10
        if 'sleep_seconds_to_prevent_buff_ban' in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        if supported_payment_methods is None:
            supported_payment_methods = ["Alipay", "WeChat"]
        # Translate payment method names if needed (original uses Chinese names)
        # We keep English names for consistency
        url = (
                "https://buff.163.com/api/market/goods/buy_order?goods_id="
                + str(goods_id)
                + "&page_num=1&page_size=20&same_goods=false&game="
                + game
                + "&appid="
                + str(app_id)
        )
        self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        self.logger.info("[BuffAutoOnSale] Fetching highest BUFF buy order")
        response = self.session.get(url, headers=self.buff_headers).json()
        if response["code"] != "OK":
            return {}
        buy_orders = merge_buy_orders(response["data"])
        if len(buy_orders) == 0:
            return {}
        for order in buy_orders:
            if require_auto_accept and not order["user"]["is_auto_accept"]:
                continue
            payment_method_supported = False
            for supported_payment_method in supported_payment_methods:
                if supported_payment_method in order["supported_pay_method"]:
                    payment_method_supported = True
                    break
            if not payment_method_supported:
                continue
            if len(order["specific"]) != 0:
                match_specific = True
                for specific in order["specific"]:
                    if specific["type"] == "paintwear":
                        if paint_wear == -1:
                            match_specific = False
                            break
                        min_paint_wear = specific["values"][0]
                        max_paint_wear = specific["values"][1]
                        if not (min_paint_wear <= paint_wear < max_paint_wear):
                            match_specific = False
                            break
                    if specific["type"] == "unlock_style":  # Template requirement; treat as not matching
                        match_specific = False
                        break
                if not match_specific:
                    continue
            return order
        return {}

    def get_lowest_sell_price(self, goods_id, game="csgo", app_id=730, min_paint_wear=0, max_paint_wear=1.0):
        """
        Fetch the lowest sell price for a goods_id from BUFF market.
        
        Debug behavior:
        - Still fetches real market data (this is needed for price calculations)
        - Only skips sleep delays in debug mode for faster testing
        """
        sleep_seconds_to_prevent_buff_ban = 10
        if 'sleep_seconds_to_prevent_buff_ban' in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        goods_key = str(goods_id) + ',' + str(min_paint_wear) + ',' + str(max_paint_wear)
        if goods_key in self.lowest_price_cache:
            if (self.lowest_price_cache[goods_key]["cache_time"] >= datetime.datetime.now() -
                    datetime.timedelta(hours=1)):
                lowest_price = self.lowest_price_cache[goods_key]["lowest_price"]
                return lowest_price
        self.logger.info("[BuffAutoOnSale] Fetching BUFF lowest sell price")
        self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        url = (
                "https://buff.163.com/api/market/goods/sell_order?goods_id="
                + str(goods_id)
                + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                + game
                + "&appid="
                + str(app_id)
                + "&min_paintwear="
                + str(min_paint_wear)
                + "&max_paintwear="
                + str(max_paint_wear)
        )
        if min_paint_wear == 0 and max_paint_wear == 1.0:
            url = (
                    "https://buff.163.com/api/market/goods/sell_order?goods_id="
                    + str(goods_id)
                    + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                    + game
                    + "&appid="
                    + str(app_id)
            )
        response_json = self.session.get(url, headers=self.buff_headers).json()
        if response_json["code"] == "OK":
            if len(response_json["data"]["items"]) == 0:  # No listings
                if min_paint_wear != 0 or max_paint_wear != 1.0:
                    self.logger.info("[BuffAutoOnSale] No items. Retry using type-level lowest price.")
                    return self.get_lowest_sell_price(goods_id, game, app_id, 0, 1.0)
                else:
                    self.logger.info("[BuffAutoOnSale] No items")
                    return -1
            
            # Debug: Log first 10 listings for pricing analysis
            if self.debug:
                self.logger.info("[BuffAutoOnSale] [DEBUG] Found " + str(len(response_json["data"]["items"])) + " listings. First 10:")
                for i, listing in enumerate(response_json["data"]["items"][:10]):
                    price = float(listing["price"])
                    self.logger.info("[BuffAutoOnSale] [DEBUG]   " + str(i+1) + ". Price: " + str(price) + " RMB")
            
            lowest_price = float(response_json["data"]["items"][0]["price"])
            self.lowest_price_cache[goods_key] = {"lowest_price": lowest_price, "cache_time": datetime.datetime.now()}
            return lowest_price
        else:
            if response_json["code"] == "Captcha Validate Required":
                captcha_url = response_json["confirm_entry"]["entry"]["url"]
                session = self.session.cookies.get("session", domain='buff.163.com')
                self.logger.error("[BuffAutoOnSale] CAPTCHA required. Use session " + session + " to open the link and complete verification")
                self.logger.error("[BuffAutoOnSale] " + captcha_url)
                if "captcha_notification" in self.config["buff_auto_on_sale"]:
                    apprise_obj = apprise.Apprise(asset=self.asset)
                    for server in self.config["buff_auto_on_sale"]["servers"]:
                        apprise_obj.add(server)
                    apprise_obj.notify(
                        title=self.config["buff_auto_on_sale"]["captcha_notification"]["title"],
                        body=self.config["buff_auto_on_sale"]["captcha_notification"]["body"].format(
                            captcha_url=captcha_url, session=session)
                    )
                return -1
            elif response_json["code"] == "System Error":
                # Too frequent access
                self.logger.error(response_json['error'])
                time.sleep(5)
                return -1
            else:
                self.logger.error(response_json)
                self.logger.error("[BuffAutoOnSale] Failed to get BUFF lowest price. Check buff_cookies.txt or try later!")
                return -1

    def compute_listing_price(self, goods_id, game="csgo", app_id=730, min_paint_wear=0, max_paint_wear=1.0):
        """
        Compute a listing price using smart tier selection:
        - Analyze all listings up to max_tier_check
        - Find the first tier with a significant gap (gap_threshold_pct) from the previous tier
        - Price 0.01 RMB below that tier
        - If no significant gaps found, price 0.01 RMB below the lowest
        """
        # Reuse sell-order endpoint to get current depth
        sleep_seconds_to_prevent_buff_ban = 10
        if 'sleep_seconds_to_prevent_buff_ban' in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        self.logger.info("[BuffAutoOnSale] Computing listing price using smart tier selection")
        self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
        time.sleep(sleep_seconds_to_prevent_buff_ban)
        url = (
                "https://buff.163.com/api/market/goods/sell_order?goods_id="
                + str(goods_id)
                + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                + game
                + "&appid="
                + str(app_id)
                + "&min_paintwear="
                + str(min_paint_wear)
                + "&max_paintwear="
                + str(max_paint_wear)
        )
        if min_paint_wear == 0 and max_paint_wear == 1.0:
            url = (
                    "https://buff.163.com/api/market/goods/sell_order?goods_id="
                    + str(goods_id)
                    + "&page_num=1&page_size=24&allow_tradable_cooldown=1&sort_by=default&game="
                    + game
                    + "&appid="
                    + str(app_id)
            )
        resp = self.session.get(url, headers=self.buff_headers).json()
        if resp.get("code") != "OK" or "data" not in resp or "items" not in resp["data"] or len(resp["data"]["items"]) == 0:
            self.logger.info("[BuffAutoOnSale] Depth fetch failed, fallback to lowest price")
            return self.get_lowest_sell_price(goods_id, game, app_id, min_paint_wear, max_paint_wear)
        
        items = resp["data"]["items"]
        prices = [float(x["price"]) for x in items]
        
        # Get current bot's Steam ID for self-detection
        current_steamid = self.steam_client.get_steam64id_from_cookies()
        
        # Check if any of the listings are our own
        my_listings = []
        for i, item in enumerate(items):
            if item.get("user_steamid") == current_steamid:
                my_listings.append({"index": i, "price": float(item["price"])})
        
        # If we have our own listings, match the lowest of our prices
        if my_listings:
            my_lowest_price = min([listing["price"] for listing in my_listings])
            suggested = my_lowest_price  # Match our own price exactly
            if self.debug:
                self.logger.info("[BuffAutoOnSale] [DEBUG] Found " + str(len(my_listings)) + " of our own listings, matching our lowest price: " + str(suggested))
            return round(suggested, 2)
        
        # Simple logic: find first significant price jump (5% from base)
        base_price = prices[0]
        target_price = prices[0]  # Default to lowest
        
        # Debug: log first few prices (always show for pricing analysis)
        self.logger.info("[BuffAutoOnSale] [DEBUG] First " + str(min(len(prices), 6)) + " prices: " + str([round(p, 2) for p in prices[:6]]))
        
        # Look for 5% jump from base price
        for i in range(1, min(len(prices), self.pricing_max_check)):
            jump_pct = (prices[i] - base_price) / base_price
            self.logger.info("[BuffAutoOnSale] [DEBUG] Tier " + str(i+1) + ": " + str(round(prices[i], 2)) + " (jump from base: " + str(round(jump_pct*100, 1)) + "%)")
            if jump_pct >= 0.01:  # 1% jump
                target_price = prices[i]
                self.logger.info("[BuffAutoOnSale] [DEBUG] Found " + str(round(jump_pct*100, 1)) + "% jump from base at tier " + str(i+1) + ", targeting: " + str(target_price))
                break
        
        # Always undercut by 0.01 RMB
        suggested = max(0.02, target_price - self.pricing_undercut_amount)
        suggested = round(suggested, 2)
        if self.debug:
            self.logger.info("[BuffAutoOnSale] [DEBUG] Final price: " + str(suggested))
        return suggested

    def get_next_account(self):
        """Get the next account in rotation for multi-account mode"""
        if not self.multi_account_rotation or len(self.available_accounts) == 0:
            return self.steam_client
        
        # Get next account in rotation
        account = self.available_accounts[self.current_account_index]
        self.current_account_index = (self.current_account_index + 1) % len(self.available_accounts)
        
        # Get the Steam client for this account
        mam = get_multi_account_manager()
        if mam:
            client = mam.get_client_for_steamid(account["steamid"])
            if client:
                self.logger.info("[BuffAutoOnSale] Rotating to account: " + account["name"] + " (SteamID: " + account["steamid"] + ")")
                # Update current steamid
                try:
                    self._current_steamid = str(client.get_steam64id_from_cookies())
                except Exception:
                    self._current_steamid = account.get("steamid", "unknown")
                return client
            else:
                self.logger.error("[BuffAutoOnSale] Failed to get client for account: " + account["name"])
        
        return self.steam_client

    def exec(self):
        self.logger.info("[BuffAutoOnSale] BUFF auto-listing plugin started. Sleeping 30s to stagger with auto-accept plugin")
        # time.sleep(30)
        try:
            self.logger.info("[BuffAutoOnSale] Preparing to log in to BUFF...")
            with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
                self.session.cookies["session"] = f.read().replace("session=", "").replace("\n", "").split(";")[0]
            self.logger.info("[BuffAutoOnSale] Cookies detected. Attempting login")
            self.logger.info("[BuffAutoOnSale] Logged in to BUFF. Username: " +
                             self.check_buff_account_state())
        except TypeError as e:
            handle_caught_exception(e, known=True)
            self.logger.error("[BuffAutoOnSale] BUFF login check failed. Check buff_cookies.txt or try later!")
            return
        sleep_interval = int(self.config["buff_auto_on_sale"]["interval"])
        black_list_time = []
        if 'blacklist_time' in self.config["buff_auto_on_sale"]:
            black_list_time = self.config["buff_auto_on_sale"]["blacklist_time"]
        white_list_time = []
        if 'whitelist_time' in self.config["buff_auto_on_sale"]:
            white_list_time = self.config["buff_auto_on_sale"]["whitelist_time"]
        random_chance = 100
        if 'random_chance' in self.config["buff_auto_on_sale"]:
            random_chance = self.config["buff_auto_on_sale"]["random_chance"] * 100
        force_refresh = 0
        if 'force_refresh' in self.config["buff_auto_on_sale"] and self.config["buff_auto_on_sale"]["force_refresh"]:
            force_refresh = 1
        description = ''
        if 'description' in self.config["buff_auto_on_sale"]:
            description = self.config["buff_auto_on_sale"]["description"]
        use_range_price = False
        if 'use_range_price' in self.config["buff_auto_on_sale"]:
            use_range_price = self.config["buff_auto_on_sale"]["use_range_price"]
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("[BuffAutoOnSale] Steam session expired. Re-logging in...")
                        self.steam_client._session.cookies.clear()
                        self.steam_client.login(
                            self.steam_client.username, self.steam_client._password, json5.dumps(self.steam_client.steam_guard)
                        )
                        self.logger.info("[BuffAutoOnSale] Steam session refreshed")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
                now = datetime.datetime.now()
                if now.hour in black_list_time:
                    self.logger.info("[BuffAutoOnSale] Current time is in blacklist hours. Sleeping " + str(sleep_interval) + "s")
                    time.sleep(sleep_interval)
                    continue
                if len(white_list_time) != 0 and now.hour not in white_list_time:
                    self.logger.info("[BuffAutoOnSale] Current time is outside whitelist hours. Sleeping " + str(sleep_interval) + "s")
                    time.sleep(sleep_interval)
                    continue
                if random.randint(1, 100) > random_chance:
                    self.logger.info("[BuffAutoOnSale] Random chance not hit. Sleeping " + str(sleep_interval) + "s")
                    time.sleep(sleep_interval)
                    continue
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoOnSale]", known=True)
                self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_interval) + "s")
                time.sleep(sleep_interval)
                continue
            try:
                # Process all accounts if multi-account rotation is enabled
                if self.multi_account_rotation:
                    self.logger.info("[BuffAutoOnSale] Processing all " + str(len(self.available_accounts)) + " accounts...")
                    for account_index in range(len(self.available_accounts)):
                        # Get next account in rotation
                        self.steam_client = self.get_next_account()
                        self.logger.info("[BuffAutoOnSale] Processing account " + str(account_index + 1) + "/" + str(len(self.available_accounts)))
                        
                        # Process this account's inventory
                        items_count_this_account = 0
                        for game in SUPPORT_GAME_TYPES:
                            self.logger.info("[BuffAutoOnSale] Checking " + game["game"] + " inventory for account " + str(self._current_steamid) + "...")
                            inventory_json = self.get_buff_inventory(
                                state="cansell", sort_by="price.desc", game=game["game"], app_id=game["app_id"],
                                force=force_refresh
                            )
                            items = inventory_json["items"]
                            items_count_this_account += len(items)
                            if len(items) != 0:
                                self.logger.info(
                                    "[BuffAutoOnSale] Found " + str(len(items)) + " sellable items in " + game["game"] +
                                    " inventory for account " + str(self._current_steamid) + ". Listing..."
                                )
                                # De-duplicate by assetid to avoid processing the same item multiple times
                                seen_assetids = set()
                                items_to_sell = []
                                for item in items:
                                    asset = item["asset_info"]
                                    asset_id = str(asset.get("assetid"))
                                    if asset_id in seen_assetids:
                                        continue
                                    seen_assetids.add(asset_id)
                                    asset["market_hash_name"] = item["market_hash_name"]
                                    items_to_sell.append(asset)
                                # List in groups of 10
                                items_to_sell_group = [items_to_sell[i:i + 10] for i in range(0, len(items_to_sell), 10)]
                                for items_to_sell in items_to_sell_group:
                                    self.put_item_on_sale(items=items_to_sell, price=-1, description=description,
                                                          game=game["game"], app_id=game["app_id"],
                                                          use_range_price=use_range_price)
                                    if 'buy_order' in self.config["buff_auto_on_sale"] and \
                                            self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
                                        self.confirm_supply_order()
                                self.logger.info("[BuffAutoOnSale] BUFF listing succeeded for account " + str(self._current_steamid) + "!")
                            else:
                                self.logger.info("[BuffAutoOnSale] " + game["game"] + " inventory empty for account " + str(self._current_steamid) + ". Skipping.")
                        
                        # Small delay between accounts to avoid rate limiting
                        if account_index < len(self.available_accounts) - 1:
                            self.logger.info("[BuffAutoOnSale] Sleeping 30s before next account...")
                            time.sleep(30)
                    
                    self.logger.info("[BuffAutoOnSale] All accounts processed. Sleeping " + str(sleep_interval) + "s before next cycle.")
                else:
                    # Single account mode (original logic)
                    while True:
                        items_count_this_loop = 0
                        for game in SUPPORT_GAME_TYPES:
                            self.logger.info("[BuffAutoOnSale] Checking " + game["game"] + " inventory...")
                            inventory_json = self.get_buff_inventory(
                                state="cansell", sort_by="price.desc", game=game["game"], app_id=game["app_id"],
                                force=force_refresh
                            )
                            items = inventory_json["items"]
                            items_count_this_loop += len(items)
                            if len(items) != 0:
                                self.logger.info(
                                    "[BuffAutoOnSale] Found " + str(len(items)) + " sellable items in " + game["game"] +
                                    " inventory. Listing..."
                                )
                                # De-duplicate by assetid to avoid processing the same item multiple times
                                seen_assetids = set()
                                items_to_sell = []
                                for item in items:
                                    asset = item["asset_info"]
                                    asset_id = str(asset.get("assetid"))
                                    if asset_id in seen_assetids:
                                        continue
                                    seen_assetids.add(asset_id)
                                    asset["market_hash_name"] = item["market_hash_name"]
                                    items_to_sell.append(asset)
                                # List in groups of 10
                                items_to_sell_group = [items_to_sell[i:i + 10] for i in range(0, len(items_to_sell), 10)]
                                for items_to_sell in items_to_sell_group:
                                    self.put_item_on_sale(items=items_to_sell, price=-1, description=description,
                                                          game=game["game"], app_id=game["app_id"],
                                                          use_range_price=use_range_price)
                                    if 'buy_order' in self.config["buff_auto_on_sale"] and \
                                            self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
                                        self.confirm_supply_order()
                                self.logger.info("[BuffAutoOnSale] BUFF listing succeeded!")
                            else:
                                self.logger.info("[BuffAutoOnSale] " + game["game"] + " inventory empty. Skipping.")
                        if items_count_this_loop == 0:
                            self.logger.info("[BuffAutoOnSale] Inventory empty. This batch finished.")
                            break
            except Exception as e:
                handle_caught_exception(e, "[BuffAutoOnSale]", known=True)
                self.logger.error("[BuffAutoOnSale] Listing failed. Error: " + str(e), exc_info=True)
            self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_interval) + "s")
            sleep_cnt = int(sleep_interval // 60)
            for _ in range(sleep_cnt):
                time.sleep(60)
                if 'buy_order' in self.config["buff_auto_on_sale"] and \
                        self.config["buff_auto_on_sale"]["buy_order"]["enable"]:
                    self.confirm_supply_order()

    def supply_item_to_buy_order(self, item, highest_buy_order, game, app_id):
        """
        Supply a specific `item` directly to a `highest_buy_order` on BUFF.

        Debug behavior:
        - No BUFF POST requests are made; logs describe the payload that would be sent.
        - No Steam trade offers are initiated; logs show intended offer flow.
        - Returns True to simulate success so caller flow continues predictably in debug.
        """
        sleep_seconds_to_prevent_buff_ban = 10
        if 'sleep_seconds_to_prevent_buff_ban' in self.config["buff_auto_on_sale"]:
            sleep_seconds_to_prevent_buff_ban = self.config["buff_auto_on_sale"]["sleep_seconds_to_prevent_buff_ban"]
        url = "https://buff.163.com/api/market/goods/supply/manual_plus"
        data = {
            "game": game,
            "buy_order_id": highest_buy_order["id"],
            "buyer_auto_accept": highest_buy_order["user"]["is_auto_accept"],
            "price": float(highest_buy_order["price"]),
            "assets": [
                item
            ],
            "steamid": str(self._current_steamid)
        }
        if not self.debug:
            self.logger.info("[BuffAutoOnSale] Sleeping " + str(sleep_seconds_to_prevent_buff_ban) + "s to avoid IP ban")
            time.sleep(sleep_seconds_to_prevent_buff_ban)
            self.logger.info("[BuffAutoOnSale] Supplying item to highest buy order...")
            self.session.get("https://buff.163.com/api/market/steam_trade", headers=self.buff_headers)
            csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
            headers = {
                "User-Agent": self.buff_headers["User-Agent"],
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
            }
            response_json = self.session.post(url, json=data, headers=headers).json()
        else:
            self.logger.info("[BuffAutoOnSale] [DEBUG] Would supply to buy order with payload: " + json5.dumps(data))
            response_json = {"code": "OK", "data": [{"id": "debug_order_id"}]}
        if response_json["code"] == "OK":
            self.logger.info("[BuffAutoOnSale] Supply succeeded!")
            self.logger.info("[BuffAutoOnSale] Initiating Steam trade offer...")
            order_id = response_json["data"][0]["id"]
            if self.debug:
                self.logger.info("[BuffAutoOnSale] [DEBUG] Would initiate Steam trade offer for BUFF bill order id=" + str(order_id))
                self.unfinish_supply_order_list.append({"order_id": order_id, "create_time": time.time()})
                self.logger.info("[BuffAutoOnSale] [DEBUG] Marked as pending for confirm_supply_order simulation")
            else:
                # Format: key=value; key=value
                steam_cookies_dict = self.steam_client._session.cookies.get_dict('steamcommunity.com')
                steam_cookies = ""
                for key in steam_cookies_dict:
                    steam_cookies += key + "=" + steam_cookies_dict[key] + "; "
                api_crypt = BuffApiCrypt()
                encrypted_steam_cookies = api_crypt.encrypt(steam_cookies)
                post_data = {
                    "seller_info": encrypted_steam_cookies,
                    "bill_orders": [
                        order_id
                    ],
                    "steamid": str(self._current_steamid)
                }
                csrf_token = self.session.cookies.get("csrf_token", domain='buff.163.com')
                headers = {
                    "User-Agent": self.buff_headers["User-Agent"],
                    "X-CSRFToken": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/json",
                    "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                }
                resp_json = self.session.post(
                    "https://buff.163.com/api/market/manual_plus/seller_send_offer",
                    json=post_data,
                    headers=headers,
                ).json()
                if resp_json["code"] == "OK":
                    self.unfinish_supply_order_list.append({"order_id": order_id, "create_time": time.time()})
                    self.logger.info("[BuffAutoOnSale] Steam trade offer initiated successfully!")
            return True
        else:
            self.logger.error(response_json)
            self.logger.error("[BuffAutoOnSale] Supply failed. Check buff_cookies.txt or try later!")
        return False

    def confirm_supply_order(self):
        """
        Process outstanding supply orders that have initiated Steam offers and are waiting for confirmation.

        Debug behavior:
        - Does not hit BUFF to poll bill info; instead, simulates that the offer is ready and logs what would happen.
        - No Steam confirmations are sent; logs the intended confirmation action.
        """
        unfinish_order_list = []
        error_num = 0  # timed-out orders
        unfinish_num = 0  # waiting for BUFF to create offer
        finish_num = 0  # completed

        self.logger.info("[BuffAutoOnSale] Processing pending offer-init orders. Count: {}".format(len(self.unfinish_supply_order_list)))
        for index, order in enumerate(self.unfinish_supply_order_list):
            order_id, create_time = order["order_id"], order["create_time"]
            if time.time() - create_time > 15 * 60:
                error_num += 1
                self.logger.error("[BuffAutoOnSale] BUFF failed to initiate Steam offer. Offer ID: {}".format(order_id))
                continue

            try:
                if self.debug:
                    steam_trade_offer_id = "debug_trade_offer_id"
                    self.logger.info("[BuffAutoOnSale] [DEBUG] Would poll BUFF bill order info for order_id=" + order_id)
                    self.logger.info("[BuffAutoOnSale] [DEBUG] BUFF indicates Steam offer is ready. Offer ID: " + steam_trade_offer_id)
                    self.logger.info("[BuffAutoOnSale] [DEBUG] Would confirm Steam offer via _confirm_transaction")
                    finish_num += 1
                else:
                    url = 'https://buff.163.com/api/market/bill_order/batch/info?bill_orders=' + order_id
                    csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
                    headers = {
                        "User-Agent": self.buff_headers["User-Agent"],
                        "X-CSRFToken": csrf_token,
                        "Referer": "https://buff.163.com/market/sell_order/create?game=csgo",
                    }
                    res_json = self.session.get(url, headers=headers).json()
                    if res_json["code"] == "OK" and len(res_json["data"]["items"]) > 0 and \
                            res_json["data"]["items"][0]["tradeofferid"] is not None:
                        steam_trade_offer_id = res_json["data"]["items"][0]["tradeofferid"]
                        self.logger.info("[BuffAutoOnSale] BUFF initiated Steam offer successfully. Offer ID: " + steam_trade_offer_id)
                        with self.steam_client_mutex:
                            self.steam_client._confirm_transaction(steam_trade_offer_id)
                        finish_num += 1
                        self.logger.info("[BuffAutoOnSale] Steam offer confirmed")
                    else:
                        unfinish_order_list.append(order)
                        unfinish_num += 1
                        self.logger.error("[BuffAutoOnSale] BUFF has not finished initiating the Steam offer. Waiting...")
            except Exception as e:
                unfinish_num += 1
                unfinish_order_list.append(order)
                self.logger.error("[BuffAutoOnSale] Failed to initiate Steam offer. Error: " + str(e), exc_info=True)
            if index != len(self.unfinish_supply_order_list) - 1:
                if not self.debug:
                    time.sleep(5)
        self.unfinish_supply_order_list = unfinish_order_list
        self.logger.info("[BuffAutoOnSale] Buy-order round complete. Confirmed: {}, Pending: {}, Failed: {}".format(
            finish_num, unfinish_num, error_num
        ))
        self.logger.info("[BuffAutoOnSale] Will recheck pending Steam offer confirmations after 5 items or 1 minute")
