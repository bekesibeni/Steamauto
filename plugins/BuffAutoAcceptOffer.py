import re
import threading
import time

import json5
import requests
from bs4 import BeautifulSoup
import utils.static as static
from BuffApi import BuffAccount
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer
from utils.tools import exit_code
from utils.multi_account_manager import get_multi_account_manager

logger = PluginLogger("BuffAutoAcceptOffer")


class BuffAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}]
        self.config = config
        self.order_info = {}
        
        self.master_panel_config = self.config.get("master_panel", {})
        self.api_url = self.master_panel_config.get("baseurl", "")
        self.api_key = self.master_panel_config.get("api_key", "")
        
        self.usd_to_cny_rate = 7.1098
        self.exchange_rate_lock = threading.Lock()
        self.exchange_rate_fetched = False
        
        if self.api_url and self.api_key:
            self.exchange_rate_thread = threading.Thread(target=self._exchange_rate_worker, daemon=True)
            self.exchange_rate_thread.start()

    def init(self) -> bool:
        return False

    def require_buyer_send_offer(self):
        try:
            logger.info('Enabling "buyer must initiate offer"...')
            result = self.buff_account.set_force_buyer_send_offer()
            if result:
                logger.info("Buyer-initiated trade offers enabled")
            else:
                logger.error("Failed to enable buyer-initiated trade offers")
        except Exception as e:
            logger.error(f"Failed to enable buyer-initiated trade offers: {str(e)}")

    def get_steam_info(self):
        steam_info = self.buff_account.get('https://buff.163.com/account/api/steam/info').json()['data']
        return steam_info

    def check_buff_account_state(self):
        try:
            username = self.buff_account.get_user_nickname()
            if username:
                trades = self.buff_account.get_steam_trade()
                if trades is None:
                    logger.error("BUFF login expired. Check buff_cookies.txt or try again later!")
                    return ""
                return username
        except Exception as e:
            logger.error(f"Failed to check BUFF account state: {str(e)}")

        logger.error("BUFF login expired. Check buff_cookies.txt or try again later!")
        return ""

    def format_item_info(self, trade):
        """Format item info for the Steam trade accept description"""
        result = "Fulfillment Platform: NetEase BUFF\n"

        for good_id, good_item in trade["goods_infos"].items():
            result += f"Item to deliver: {good_item['name']}"
            if len(trade.get('items_to_trade', [])) > 1:
                result += f" and {len(trade['items_to_trade'])} other item(s)"

            if trade["tradeofferid"] in self.order_info:
                price = float(self.order_info[trade["tradeofferid"]]["price"])
                result += f"\nOrder Price: {price} CNY"

            break

        return result
    
    def fetch_exchange_rate(self):
        """Fetch USD to CNY exchange rate from API"""
        try:
            response = requests.get("https://api.frankfurter.dev/v1/latest?base=USD", timeout=10)
            if response.status_code == 200:
                data = response.json()
                cny_rate = data.get("rates", {}).get("CNY")
                if cny_rate:
                    with self.exchange_rate_lock:
                        old_rate = self.usd_to_cny_rate
                        self.usd_to_cny_rate = float(cny_rate)
                        if old_rate != self.usd_to_cny_rate:
                            logger.info(f"Updated USD to CNY exchange rate: {self.usd_to_cny_rate}")
                    return True
        except Exception as e:
            logger.warning(f"Failed to fetch exchange rate: {str(e)}. Using cached rate: {self.usd_to_cny_rate}")
        return False
    
    def _exchange_rate_worker(self):
        """Background worker to fetch exchange rate every hour"""
        self.fetch_exchange_rate()
        self.exchange_rate_fetched = True
        
        while True:
            time.sleep(3600)
            self.fetch_exchange_rate()
    
    def round_price(self, price):
        """Round price to 2 decimals: round up if third decimal >= 0.005, down if < 0.005"""
        price_100 = price * 100
        integer_part = int(price_100)
        decimal_part = price_100 - integer_part
        
        if decimal_part >= 0.5:
            return round((integer_part + 1) / 100, 2)
        else:
            return round(integer_part / 100, 2)
    
    def calculate_prices(self, cny_price):
        """Calculate platformPrice and actualPrice from CNY price"""
        with self.exchange_rate_lock:
            usd_to_cny = self.usd_to_cny_rate
        
        usd_price = float(cny_price) / usd_to_cny
        platform_price = self.round_price(usd_price)
        
        actual_price = platform_price / 1.025
        actual_price = actual_price / 1.01
        actual_price = self.round_price(actual_price)
        
        return platform_price, actual_price
    
    def truncate_float(self, value, decimals=16):
        """Truncate float to specified decimal places (not rounded)"""
        multiplier = 10 ** decimals
        return int(float(value) * multiplier) / multiplier
    
    def post_to_master_panel(self, float_value, platform_price, actual_price, market_hash_name):
        """POST item data to master panel API"""
        if not self.api_url or not self.api_key:
            return False
        
        try:
            truncated_float = self.truncate_float(float_value, 16)
            # Ensure float is sent as a string by using format to prevent JSON auto-conversion
            float_str = f"{truncated_float:.16f}".rstrip('0').rstrip('.')
            if not float_str or float_str == '.':
                float_str = "0"
            
            item_data = {
                "float": float_str,
                "platformPrice": platform_price,
                "actualPrice": actual_price,
                "marketHashName": market_hash_name,
                "type": "sell"
            }
            
            response = requests.post(
                f"{self.api_url}/items",
                json=item_data,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key
                },
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                return True
            else:
                try:
                    response_text = response.text
                    logger.warning(f"Failed to post item to master panel. Status: {response.status_code}, Response: {response_text}")
                except:
                    logger.warning(f"Failed to post item to master panel. Status: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Error posting to master panel: {str(e)}", exc_info=True)
            return False

    def exec(self):
        logger.info("BUFF auto-accept offer plugin started. Please wait...")
        proxies = None
        if self.config["buff_auto_accept_offer"].get("use_proxies", False):
            proxies = self.config.get("proxies")
            logger.info("Detected Steam proxy settings, applying same proxy to BUFF...")

        session = get_valid_session_for_buff(self.steam_client, logger, proxies=proxies)
        self.buff_account = BuffAccount(session, proxies=proxies)

        try:
            user_info = self.buff_account.get_user_info()
            steamid_buff = user_info['steamid']
            logger.info('Sleeping 5s to avoid hitting APIs too frequently...')
            time.sleep(5)
            steam_info = self.get_steam_info()
        except Exception as e:
            logger.error("Failed to get BUFF user info!")
            handle_caught_exception(e, "BuffAutoAcceptOffer")
            exit_code.set(1)
            return 1

        multi_account_manager = get_multi_account_manager()
        if not multi_account_manager:
            logger.error("Multi-account manager not available")
            exit_code.set(1)
            return 1
            
        bound_accounts = []
        all_clients = multi_account_manager.get_all_clients()
        
        for steamid, client in all_clients.items():
            if steamid in [str(account['steamid']) for account in steam_info['items']]:
                bound_accounts.append(steamid)
                
        if not bound_accounts:
            logger.error("None of the configured Steam accounts are bound to this BUFF account. Auto-fulfillment unavailable!")
            exit_code.set(1)
            return 1
            
        logger.info(f"Found {len(bound_accounts)} bound Steam account(s): {bound_accounts}")

        logger.info(f"Logged into BUFF. Username: {user_info['nickname']}")
        if not user_info['force_buyer_send_offer']:
            logger.warning('Account has not enabled "buyer must initiate offer". Enabling automatically...')
            self.require_buyer_send_offer()
        else:
            logger.info('"Buyer must initiate offer" is already enabled')

        ignored_offer = {}
        REPROCESS_THRESHOLD = 10
        IGNORE_CLEAR_INTERVAL = 300
        last_ignore_clear = time.time()
        
        logger.info("Clearing any previously ignored offers to avoid 'already accepted' errors...")
        interval = self.config["buff_auto_accept_offer"]["interval"]
        dota2_support = self.config["buff_auto_accept_offer"].get("dota2_support", False)

        if 'sell_protection' in self.config['buff_auto_accept_offer']:
            logger.warning('You are using an old config. BUFF auto-fulfillment was rewritten and simplified. Delete config and regenerate!')

        if dota2_support:
            self.SUPPORT_GAME_TYPES.append({"game": "dota2", "app_id": 570})

        while True:
            try:
                # Clear ignore list periodically to handle offers that might become valid again
                current_time = time.time()
                if current_time - last_ignore_clear > IGNORE_CLEAR_INTERVAL:
                    if ignored_offer:
                        logger.info(f"Clearing {len(ignored_offer)} ignored offers to allow reprocessing...")
                        ignored_offer.clear()
                    last_ignore_clear = current_time
                
                logger.info("Checking BUFF items to deliver / to confirm...")
                username = self.check_buff_account_state()
                if username == "":
                    logger.info("BUFF login expired. Attempting re-login...")
                    proxies = None
                    if self.config["buff_auto_accept_offer"].get("use_proxies", False):
                        proxies = self.config.get("proxies")
                    session = get_valid_session_for_buff(self.steam_client, logger, proxies=proxies)
                    if session == "":
                        logger.error("BUFF login expired and auto re-login failed!")
                        return
                    self.buff_account = BuffAccount(session, proxies=proxies)

                notification = self.buff_account.get_notification()
                if 'error' in notification:
                    logger.error(f"Failed to fetch pending orders. Error: {notification['error']}")
                    logger.info(f"Retrying in {interval} seconds...")
                    time.sleep(interval)
                    continue

                if isinstance(notification, dict) and "to_deliver_order" in notification:
                    to_deliver_order = notification["to_deliver_order"]
                    try:
                        csgo_count = 0 if "csgo" not in to_deliver_order else int(to_deliver_order["csgo"])
                        dota2_count = 0 if (dota2_support or ("dota2" not in to_deliver_order)) else int(to_deliver_order["dota2"])
                        total_count = csgo_count + dota2_count

                        if csgo_count != 0 or dota2_count != 0:
                            logger.info(f"Detected {total_count} pending delivery request(s)")
                            logger.info(f"CSGO to deliver: {csgo_count}")
                            if dota2_support:
                                logger.info(f"DOTA2 to deliver: {dota2_count}")
                    except TypeError as e:
                        handle_caught_exception(e, "BuffAutoAcceptOffer", known=True)
                        logger.error("BUFF API returned invalid data. Check network or try later!")

                if any(list(notification["to_deliver_order"].values()) + list(notification["to_confirm_sell"].values())):
                    trades = self.buff_account.get_steam_trade()
                    logger.info("Sleeping 5s to avoid hitting APIs too frequently...")
                    time.sleep(5)

                    if trades is None:
                        logger.error("Failed to fetch Steam trades. Retrying...")
                        time.sleep(5)
                        continue

                    for index, game in enumerate(self.SUPPORT_GAME_TYPES):
                        response_data = self.buff_account.get_sell_order_to_deliver(game["game"], game["app_id"])
                        if response_data and "items" in response_data:
                            trade_supply = response_data["items"]
                            for trade_offer in trade_supply:
                                if trade_offer["tradeofferid"] is not None and trade_offer["tradeofferid"] != "":
                                    self.order_info[trade_offer["tradeofferid"]] = trade_offer
                                    if not any(trade_offer["tradeofferid"] == trade["tradeofferid"] for trade in trades):
                                        user_steamid = str(trade_offer.get('user_steamid', ''))
                                        
                                        if not user_steamid:
                                            logger.warning(f"No user_steamid found in offer {trade_offer['tradeofferid']}")
                                            continue
                                            
                                        target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                        if not target_client:
                                            logger.warning(f"No Steam client found for user_steamid: {user_steamid}")
                                            continue
                                            
                                        trade_offer['target_client'] = target_client
                                        trade_offer['user_steamid'] = user_steamid
                                        for goods_id, goods_info in response_data["goods_infos"].items():
                                            goods_id = str(goods_id)
                                            trade_offer["goods_id"] = str(trade_offer["goods_id"])
                                            if goods_id == trade_offer["goods_id"]:
                                                trade_offer["goods_infos"] = {}
                                                trade_offer["goods_infos"][goods_id] = goods_info
                                                break
                                        trades.append(trade_offer)

                        if index != len(self.SUPPORT_GAME_TYPES) - 1:
                            logger.info("Sleeping 5s to avoid hitting APIs too frequently...")
                            time.sleep(5)

                    seen_offers = set()
                    unique_trades = []
                    for trade in trades:
                        offer_id = trade.get("tradeofferid")
                        if offer_id and offer_id not in seen_offers:
                            seen_offers.add(offer_id)
                            unique_trades.append(trade)
                    
                    trades = unique_trades
                    unprocessed_count = len(trades)
                    logger.info(f"Found {unprocessed_count} unique BUFF offer(s) to process")
                    
                    float_map = {}
                    if len(trades) > 0:
                        try:
                            game_type = trades[0].get("game", "csgo")
                            
                            html_page = self.buff_account.get_sell_order_to_deliver_page(game_type)
                            if html_page:
                                order_ids_match = re.search(r'sellingToDeliver\(\[(.*?)\],\s*\d+\)', html_page, re.DOTALL)
                                if order_ids_match:
                                    order_ids_str = order_ids_match.group(1)
                                    order_ids = re.findall(r'"([^"]+)"', order_ids_str)
                                    
                                    if order_ids:
                                        batch_data = self.buff_account.get_sell_order_to_deliver_batch(game_type, order_ids)
                                        if batch_data.get("code") == "OK" and "data" in batch_data:
                                            html_content = batch_data["data"]
                                            soup = BeautifulSoup(html_content, "html.parser")
                                            order_rows = soup.find_all("tr", class_="deliver-order")
                                            
                                            for row in order_rows:
                                                item_div = row.find("div", class_="item-detail-img")
                                                if item_div:
                                                    assetid = item_div.get("data-assetid")
                                                    if assetid:
                                                        float_p = row.find("p", string=re.compile(r"Float:"))
                                                        float_value = None
                                                        if float_p:
                                                            float_text = float_p.get_text()
                                                            float_match = re.search(r"Float:\s*([\d.]+)", float_text)
                                                            if float_match:
                                                                float_value = float_match.group(1)
                                                        
                                                        price_span = row.find("span", class_="custom-currency")
                                                        cny_price = None
                                                        if price_span:
                                                            cny_price = price_span.get("data-price")
                                                        
                                                        if assetid and float_value and cny_price:
                                                            float_map[assetid] = {
                                                                "float": float_value,
                                                                "cny_price": cny_price
                                                            }
                        except Exception as e:
                            logger.error(f"[BuffAutoAcceptOffer] Failed to fetch float values: {str(e)}", exc_info=True)

                    try:
                        if len(trades) != 0:
                            filtered_trades = []
                            for trade in trades:
                                offer_id = trade["tradeofferid"]
                                if offer_id in ignored_offer:
                                    ignored_offer[offer_id] += 1
                                    if ignored_offer[offer_id] > REPROCESS_THRESHOLD:
                                        logger.warning(f"Offer {offer_id} ignored {ignored_offer[offer_id]-1} times. Above threshold {REPROCESS_THRESHOLD}. Reprocessing.")
                                        del ignored_offer[offer_id]
                                        filtered_trades.append(trade)
                                    else:
                                        logger.info(f"Offer {offer_id} already handled. Skipping.")
                                else:
                                    filtered_trades.append(trade)
                            
                            if not filtered_trades:
                                logger.info("All offers already processed. Skipping this cycle.")
                                continue
                                
                            for i, trade in enumerate(filtered_trades):
                                offer_id = trade["tradeofferid"]
                                logger.info(f"Processing offer {i+1} / {len(filtered_trades)}. Offer ID: {offer_id}")
                                
                                try:
                                    item_name = "Unknown"
                                    float_value = None
                                    cny_price = None
                                    market_hash_name = "Unknown"
                                    
                                    if "goods_infos" in trade and trade["goods_infos"]:
                                        for goods_id, goods_info in trade["goods_infos"].items():
                                            item_name = goods_info.get("name", "Unknown")
                                            market_hash_name = goods_info.get("market_hash_name", item_name)
                                            break
                                    
                                    for item in trade.get("items_to_trade", []):
                                        assetid = item.get("assetid")
                                        if assetid and assetid in float_map:
                                            item_data = float_map[assetid]
                                            if isinstance(item_data, dict):
                                                float_value = item_data.get("float")
                                                cny_price = item_data.get("cny_price")
                                            else:
                                                float_value = item_data
                                            break
                                    
                                    if float_value:
                                        logger.info(f"Confirming item {item_name} float: {float_value}")
                                    else:
                                        logger.info(f"Confirming item {item_name}")
                                    
                                    desc = self.format_item_info(trade)
                                    
                                    user_steamid = trade.get('user_steamid', '')
                                    if not user_steamid:
                                        logger.error(f"No user_steamid found for offer {offer_id}")
                                        continue
                                        
                                    target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                    if not target_client:
                                        logger.error(f"No Steam client found for user_steamid: {user_steamid}")
                                        continue
                                    
                                    if accept_trade_offer(target_client, self.steam_client_mutex, offer_id, desc=desc):
                                        ignored_offer[offer_id] = 1
                                        logger.info("Accepted. Offer added to ignore list.")
                                        
                                        # Try to get price from order_info if not in float_map
                                        if not cny_price and offer_id in self.order_info:
                                            try:
                                                cny_price = str(self.order_info[offer_id].get("price", ""))
                                                if cny_price:
                                                    logger.info(f"Using price from order_info for offer {offer_id}: {cny_price}")
                                            except Exception as e:
                                                logger.debug(f"Could not get price from order_info: {str(e)}")
                                        
                                        if float_value and cny_price and self.api_url and self.api_key:
                                            try:
                                                platform_price, actual_price = self.calculate_prices(cny_price)
                                                if self.post_to_master_panel(float_value, platform_price, actual_price, market_hash_name):
                                                    logger.info(f"Successfully sent item {item_name} to master panel")
                                            except Exception as e:
                                                logger.warning(f"Failed to process prices for master panel: {str(e)}")
                                        elif self.api_url and self.api_key:
                                            # Log why item wasn't reported
                                            missing = []
                                            if not float_value:
                                                missing.append("float_value")
                                            if not cny_price:
                                                missing.append("cny_price")
                                            logger.warning(f"Item {item_name} (offer {offer_id}) not reported to master panel: missing {', '.join(missing)}")
                                    else:
                                        ignored_offer[offer_id] = 1
                                        logger.info("Offer processing failed. Added to ignore list to prevent repeated attempts.")

                                    if i != len(filtered_trades) - 1:
                                        logger.info("Waiting 5s before next offer to reduce Steam API pressure...")
                                        time.sleep(5)
                                except Exception as e:
                                    ignored_offer[offer_id] = 1
                                    logger.error(f"Error while processing offer: {str(e)}", exc_info=True)
                                    logger.info("Error occurred. Offer added to ignore list to prevent repeated attempts.")

                    except Exception as e:
                        handle_caught_exception(e, "BuffAutoAcceptOffer")
                        logger.info("Error occurred. Will retry later.")
                else:
                    logger.info("No offers to process")
            except Exception as e:
                handle_caught_exception(e, "BuffAutoAcceptOffer")
                logger.info("Unknown error. Will retry later.")

            logger.info(f"Rechecking pending delivery orders in {interval} seconds...")
            time.sleep(interval)
