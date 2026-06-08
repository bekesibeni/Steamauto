import re
import threading
import time

import requests
from bs4 import BeautifulSoup
from BuffApi import BuffAccount
from utils.BuffApiCrypt import BuffApiCrypt
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
        # bill_ids already reported to the master panel after a seller-send delivery,
        # so retries of the confirm step don't double-report.
        self.seller_send_reported = set()

        self.master_panel_config = self.config.get("master_panel", {})
        self.api_url = self.master_panel_config.get("baseurl", "")
        self.api_key = self.master_panel_config.get("api_key", "")
        self.transaction_fee_rate = float(self.master_panel_config.get("transaction_fee_rate", 0.025))
        self.withdrawal_fee_rate = float(self.master_panel_config.get("withdrawal_fee_rate", 0.01))
        self.balance_label = self.master_panel_config.get("label", "")

        # USD->CNY rate. Sourced from BUFF user info (buff_price_currency_rate_base_usd)
        # and refreshed hourly by the balance report worker; 7.1098 is only a startup fallback.
        self.usd_to_cny_rate = 7.1098
        self.exchange_rate_lock = threading.Lock()

    def init(self) -> bool:
        return False

    def _get_proxies(self):
        """Return proxy config if enabled, else None."""
        if self.config["buff_auto_accept_offer"].get("use_proxies", False):
            return self.config.get("proxies")
        return None

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
    
    def _apply_rate_from_user_info(self, user_info):
        """Update the cached USD->CNY rate from a BUFF user info payload."""
        rate = user_info.get("buff_price_currency_rate_base_usd")
        if not rate:
            return
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return
        with self.exchange_rate_lock:
            old_rate = self.usd_to_cny_rate
            self.usd_to_cny_rate = rate
            if old_rate != self.usd_to_cny_rate:
                logger.info(f"Updated USD to CNY exchange rate from BUFF: {self.usd_to_cny_rate}")

    def _report_balance(self, user_info):
        """Report the account balance (cash + pending divide, converted to USD) to the master panel."""
        if not self.api_url or not self.api_key:
            return
        account_id = user_info.get("id")
        if not account_id:
            logger.warning("No BUFF account id in user info; skipping balance report")
            return
        balance = self.buff_account.get_balance()
        with self.exchange_rate_lock:
            rate = self.usd_to_cny_rate
        if not rate:
            return
        total_cny = balance["cash_amount"] + balance["pending_divide_amount"]
        balance_usd = round(total_cny / rate, 2)
        label = self.balance_label or user_info.get("nickname", "")
        payload = {"accountId": account_id, "balanceUsd": balance_usd, "label": label}
        if self._post_to_master_panel("/balances", payload, "balance"):
            logger.info(f"Reported balance to master panel: {balance_usd} USD (account {account_id})")

    def _balance_report_worker(self, initial_user_info=None):
        """Background worker: report the balance immediately (first iteration on
        startup), then refresh and report every hour. Each iteration uses a
        single get_user_info() call to drive both the rate update and the balance
        report (userid + label); the first iteration reuses the user_info already
        fetched at startup so get_user_info is never called twice in a row."""
        user_info = initial_user_info
        while True:
            try:
                if user_info is None:
                    user_info = self.buff_account.get_user_info()
                if user_info:
                    self._apply_rate_from_user_info(user_info)
                    self._report_balance(user_info)
            except Exception as e:
                logger.warning(f"Balance/rate refresh failed: {str(e)}. Using cached rate: {self.usd_to_cny_rate}")
            user_info = None  # subsequent iterations fetch fresh user_info + balance
            time.sleep(3600)
    
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
        
        actual_price = platform_price / (1 + self.transaction_fee_rate)
        actual_price = actual_price / (1 + self.withdrawal_fee_rate)
        actual_price = self.round_price(actual_price)
        
        return platform_price, actual_price
    
    def truncate_float(self, value, decimals=16):
        """Truncate float to specified decimal places (not rounded)"""
        multiplier = 10 ** decimals
        return int(float(value) * multiplier) / multiplier
    
    def _post_to_master_panel(self, path, payload, description="data") -> bool:
        """POST a JSON payload to the master panel. Returns True on success.
        Single source for the URL, auth header, timeout and error handling shared
        by item and balance reporting."""
        if not self.api_url or not self.api_key:
            return False
        try:
            response = requests.post(
                f"{self.api_url}{path}",
                json=payload,
                headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
                timeout=10,
            )
            if response.status_code in (200, 201):
                return True
            # Servers that are down/misconfigured (e.g. 404) can return huge HTML
            # error pages; truncate so a single failure doesn't flood the log.
            body = (response.text or "")[:300]
            logger.warning(f"Failed to post {description} to master panel. Status: {response.status_code}, Response: {body}")
            return False
        except Exception as e:
            logger.error(f"Error posting {description} to master panel: {str(e)}", exc_info=True)
            return False

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
        except Exception as e:
            logger.error(f"Error preparing item for master panel: {str(e)}", exc_info=True)
            return False
        return self._post_to_master_panel("/items", item_data, "item")

    def _encrypted_steam_cookies(self, client) -> str:
        """Encrypt a Steam client's steamcommunity.com cookies for BUFF seller_send_offer.
        Format matches BuffAutoOnSale: 'key=value; key=value; '."""
        cookies_dict = client._session.cookies.get_dict("steamcommunity.com")
        cookie_str = "".join(f"{k}={v}; " for k, v in cookies_dict.items())
        return BuffApiCrypt().encrypt(cookie_str)

    def _wait_for_tradeofferid(self, bill_id, attempts=6, delay=5):
        """After a seller_send_offer call, poll BUFF until it populates the
        tradeofferid for the newly-created outgoing offer (or give up)."""
        for i in range(attempts):
            time.sleep(delay)
            try:
                info = self.buff_account.get_bill_order_info([bill_id])
            except Exception as e:
                logger.debug(f"bill_order info poll failed for {bill_id}: {str(e)}")
                continue
            items = info.get("items", []) if info else []
            if items and items[0].get("tradeofferid"):
                return items[0]["tradeofferid"]
            logger.info(f"Waiting for BUFF to create the Steam offer for {bill_id} ({i + 1}/{attempts})...")
        return None

    def _report_seller_send_item(self, item, market_hash_name):
        """Report a seller-send delivered item to the master panel (price + float),
        deduped per bill order. The to_deliver payload already carries both."""
        if not (self.api_url and self.api_key):
            return
        bill_id = item.get("id")
        if bill_id in self.seller_send_reported:
            return
        cny_price = item.get("price")
        float_value = item.get("asset_info", {}).get("paintwear")
        if not (cny_price and float_value):
            logger.warning(f"Order {bill_id}: missing price/float; not reported to master panel")
            return
        try:
            platform_price, actual_price = self.calculate_prices(cny_price)
            if self.post_to_master_panel(float_value, platform_price, actual_price, market_hash_name):
                self.seller_send_reported.add(bill_id)
                logger.info(f"Reported seller-send item {market_hash_name} (order {bill_id}) to master panel")
        except Exception as e:
            logger.warning(f"Failed to report seller-send item {market_hash_name} to master panel: {str(e)}")

    def _handle_seller_send_order(self, item, market_hash_name, multi_account_manager):
        """Deliver an order that requires the SELLER to send the Steam offer
        (is_seller_asked_to_send_offer=True), which the buyer-initiated accept
        path can never satisfy. Two phases, naturally retried across cycles:

          1. tradeofferid not set yet -> upload the seller's encrypted Steam cookies
             via seller_send_offer so BUFF creates the outgoing offer, then poll for
             the id.
          2. tradeofferid present (BUFF created the offer) -> mobile-confirm it.
        """
        bill_id = item.get("id")
        seller_steamid = str(item.get("seller_steamid", ""))
        offer_id = item.get("tradeofferid")

        # Surface BUFF-side blockers instead of silently looping forever.
        fail_confirm = item.get("fail_confirm")
        if fail_confirm and fail_confirm.get("message"):
            logger.error(f"Order {bill_id} blocked by BUFF: {fail_confirm.get('message')}")
            return
        if item.get("seller_cookie_invalid"):
            logger.warning(f"Order {bill_id}: BUFF reports seller Steam cookies invalid; re-uploading via seller_send_offer.")

        if not seller_steamid:
            logger.warning(f"Seller-send order {bill_id} has no seller_steamid; skipping")
            return

        target_client = multi_account_manager.get_client_for_steamid(seller_steamid)
        if not target_client:
            logger.warning(f"No Steam client for seller_steamid {seller_steamid} (order {bill_id}); skipping")
            return

        # Phase 1: have BUFF send the offer if it hasn't been created yet.
        if not offer_id:
            logger.info(f"Seller-send order {bill_id}: uploading Steam session and asking BUFF to send the offer...")
            try:
                encrypted = self._encrypted_steam_cookies(target_client)
            except Exception as e:
                logger.error(f"Failed to read/encrypt Steam cookies for {seller_steamid} (order {bill_id}): {str(e)}", exc_info=True)
                return
            try:
                resp = self.buff_account.seller_send_offer(seller_steamid, encrypted, [bill_id])
            except Exception as e:
                logger.error(f"seller_send_offer request failed for {bill_id}: {str(e)}", exc_info=True)
                return
            if resp.get("code") != "OK":
                logger.error(f"BUFF rejected seller_send_offer for {bill_id}: {resp}")
                return
            logger.info(f"BUFF accepted seller_send_offer for {bill_id}; waiting for offer creation...")
            offer_id = self._wait_for_tradeofferid(bill_id)
            if not offer_id:
                logger.info(f"Offer not created yet for {bill_id}; will confirm on a later cycle.")
                return

        # Phase 2: mobile-confirm the outgoing offer (Steam Guard).
        logger.info(f"Seller-send order {bill_id}: confirming Steam offer {offer_id} via Steam Guard...")
        try:
            with self.steam_client_mutex:
                target_client._confirm_transaction(str(offer_id))
            logger.info(f"Confirmed seller-send offer {offer_id} (order {bill_id}); delivery complete.")
        except Exception as e:
            logger.error(f"Failed to confirm seller-send offer {offer_id} (order {bill_id}): {str(e)}", exc_info=True)
            return

        self._report_seller_send_item(item, market_hash_name)

    def exec(self):
        logger.info("BUFF auto-accept offer plugin started. Please wait...")
        proxies = self._get_proxies()
        if proxies:
            logger.info("Detected Steam proxy settings, applying same proxy to BUFF...")

        session = get_valid_session_for_buff(self.steam_client, logger, proxies=proxies)
        self.buff_account = BuffAccount(session, proxies=proxies)

        try:
            user_info = self.buff_account.get_user_info()
            steamid_buff = user_info['steamid']
            self._apply_rate_from_user_info(user_info)
            logger.info('Sleeping 5s to avoid hitting APIs too frequently...')
            time.sleep(5)
            steam_info = self.buff_account.get_steam_info()
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

        if self.api_url and self.api_key:
            logger.info("Reporting initial balance to master panel and starting hourly updates...")
            # Pass the startup user_info so the worker's first iteration reuses it
            # instead of calling get_user_info a second time.
            self.balance_report_thread = threading.Thread(
                target=self._balance_report_worker, args=(user_info,), daemon=True
            )
            self.balance_report_thread.start()

        ignored_offer = {}
        retry_counts = {}   # offer_id -> number of processing attempts so far
        MAX_RETRIES = 3     # add to ignored_offer only after this many attempts
        deferred_offers = {}  # offer_id -> defer count, for offers waiting on float data
        MAX_DEFER_COUNT = 3
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
                        retry_counts.clear()
                    last_ignore_clear = current_time
                
                logger.info("Checking BUFF items to deliver / to confirm...")
                username = self.check_buff_account_state()
                if username == "":
                    logger.info("BUFF login expired. Attempting re-login...")
                    proxies = self._get_proxies()
                    session = get_valid_session_for_buff(self.steam_client, logger, proxies=proxies)
                    if session == "":
                        logger.error("BUFF login expired and auto re-login failed!")
                        return
                    self.buff_account = BuffAccount(session, proxies=proxies)

                notification = self.buff_account.get_notification()
                if 'error' in notification:
                    logger.error(f"Failed to fetch pending orders. Error: {notification['error']}. Falling back to direct delivery check...")
                    notification = None
                else:
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

                if not notification or any(list(notification["to_deliver_order"].values()) + list(notification["to_confirm_sell"].values())):
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
                            goods_infos = response_data.get("goods_infos", {})
                            for trade_offer in trade_supply:
                                # Seller-send orders: BUFF expects US to send the Steam offer
                                # (the buyer never initiates one), so the accept path below can
                                # never deliver them. Handle send+confirm in a dedicated pass.
                                if trade_offer.get("is_seller_asked_to_send_offer"):
                                    goods_info = goods_infos.get(str(trade_offer.get("goods_id")), {})
                                    mhn = goods_info.get("market_hash_name") or goods_info.get("name", "Unknown")
                                    try:
                                        self._handle_seller_send_order(trade_offer, mhn, multi_account_manager)
                                    except Exception as e:
                                        handle_caught_exception(e, "BuffAutoAcceptOffer")
                                        logger.error(f"Error handling seller-send order {trade_offer.get('id')}: {str(e)}")
                                    continue
                                if trade_offer["tradeofferid"] is not None and trade_offer["tradeofferid"] != "":
                                    self.order_info[trade_offer["tradeofferid"]] = trade_offer
                                    if not any(trade_offer["tradeofferid"] == trade["tradeofferid"] for trade in trades):
                                        # Current BUFF API returns seller_steamid on to_deliver items;
                                        # fall back to it when the legacy user_steamid is absent.
                                        user_steamid = str(trade_offer.get('user_steamid', '') or trade_offer.get('seller_steamid', ''))

                                        if not user_steamid:
                                            logger.warning(f"No user_steamid/seller_steamid found in offer {trade_offer['tradeofferid']}")
                                            continue
                                            
                                        target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                        if not target_client:
                                            logger.warning(f"No Steam client found for user_steamid: {user_steamid}")
                                            continue
                                            
                                        trade_offer['target_client'] = target_client
                                        trade_offer['user_steamid'] = user_steamid
                                        offer_goods_id = str(trade_offer["goods_id"])
                                        for goods_id, goods_info in response_data["goods_infos"].items():
                                            if str(goods_id) == offer_goods_id:
                                                trade_offer["goods_infos"] = {str(goods_id): goods_info}
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
                                                        
                                                        # Try to get item name from the row
                                                        item_name_tag = row.find("a", class_="item-detail-name") or row.find("div", class_="item-detail-name")
                                                        row_item_name = item_name_tag.get_text(strip=True) if item_name_tag else None

                                                        if assetid and float_value and cny_price:
                                                            float_map[assetid] = {
                                                                "float": float_value,
                                                                "cny_price": cny_price,
                                                                "market_hash_name": row_item_name
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
                                        retry_counts.pop(offer_id, None)
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
                                    # Build a default name from goods_infos (fallback for items not in float_map)
                                    default_item_name = "Unknown"
                                    default_market_hash_name = "Unknown"
                                    if "goods_infos" in trade and trade["goods_infos"]:
                                        for goods_id, goods_info in trade["goods_infos"].items():
                                            default_item_name = goods_info.get("name", "Unknown")
                                            default_market_hash_name = goods_info.get("market_hash_name", default_item_name)
                                            break

                                    # Collect data for ALL items in this trade
                                    trade_items = []
                                    for item in trade.get("items_to_trade", []):
                                        assetid = item.get("assetid")
                                        item_float = None
                                        item_cny_price = None
                                        item_market_hash_name = default_market_hash_name
                                        if assetid and assetid in float_map:
                                            item_data = float_map[assetid]
                                            if isinstance(item_data, dict):
                                                item_float = item_data.get("float")
                                                item_cny_price = item_data.get("cny_price")
                                                if item_data.get("market_hash_name"):
                                                    item_market_hash_name = item_data["market_hash_name"]
                                            else:
                                                item_float = item_data
                                        trade_items.append({
                                            "assetid": assetid,
                                            "float": item_float,
                                            "cny_price": item_cny_price,
                                            "market_hash_name": item_market_hash_name,
                                        })

                                    item_count = len(trade_items)
                                    if item_count > 1:
                                        logger.info(f"Trade offer {offer_id} contains {item_count} items (multi-item trade)")

                                    for ti in trade_items:
                                        if ti["float"]:
                                            logger.info(f"  Item {ti['market_hash_name']} float: {ti['float']}")
                                        else:
                                            logger.info(f"  Item {ti['market_hash_name']} (no float yet)")

                                    # If master panel is configured, check that all items have float data.
                                    # If not, defer the offer so it can be retried next cycle when floats are available.
                                    if self.api_url and self.api_key:
                                        missing_floats = [ti for ti in trade_items if not ti["float"]]
                                        defer_count = deferred_offers.get(offer_id, 0)
                                        if missing_floats and defer_count < MAX_DEFER_COUNT:
                                            deferred_offers[offer_id] = defer_count + 1
                                            logger.info(
                                                f"Deferring offer {offer_id}: {len(missing_floats)}/{item_count} item(s) missing float data. "
                                                f"Will retry next cycle (attempt {defer_count + 1}/{MAX_DEFER_COUNT})"
                                            )
                                            continue

                                    # Clean up defer counter once we proceed
                                    deferred_offers.pop(offer_id, None)

                                    desc = self.format_item_info(trade)

                                    user_steamid = trade.get('user_steamid', '') or trade.get('seller_steamid', '')
                                    if not user_steamid:
                                        logger.error(f"No user_steamid/seller_steamid found for offer {offer_id}")
                                        continue

                                    target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                    if not target_client:
                                        logger.error(f"No Steam client found for user_steamid: {user_steamid}")
                                        continue

                                    if accept_trade_offer(target_client, self.steam_client_mutex, offer_id, desc=desc):
                                        attempt = retry_counts.get(offer_id, 0) + 1
                                        retry_counts[offer_id] = attempt
                                        if attempt >= MAX_RETRIES:
                                            ignored_offer[offer_id] = 1
                                            retry_counts.pop(offer_id, None)
                                            logger.info(f"Accepted (attempt {attempt}/{MAX_RETRIES}). Offer permanently added to ignore list.")
                                        else:
                                            logger.info(f"Accepted (attempt {attempt}/{MAX_RETRIES}). Will retry to confirm full delivery.")

                                        # Post ALL items to master panel
                                        panel_sent = 0
                                        for ti in trade_items:
                                            item_float = ti["float"]
                                            item_cny_price = ti["cny_price"]
                                            item_mhn = ti["market_hash_name"]

                                            # Try to get price from order_info if not in float_map
                                            if not item_cny_price and offer_id in self.order_info:
                                                try:
                                                    item_cny_price = str(self.order_info[offer_id].get("price", ""))
                                                    if item_cny_price:
                                                        logger.info(f"Using price from order_info for {item_mhn}: {item_cny_price}")
                                                except Exception as e:
                                                    logger.debug(f"Could not get price from order_info: {str(e)}")

                                            if item_float and item_cny_price and self.api_url and self.api_key:
                                                try:
                                                    platform_price, actual_price = self.calculate_prices(item_cny_price)
                                                    if self.post_to_master_panel(item_float, platform_price, actual_price, item_mhn):
                                                        panel_sent += 1
                                                        logger.info(f"Successfully sent item {item_mhn} to master panel")
                                                except Exception as e:
                                                    logger.warning(f"Failed to process prices for master panel ({item_mhn}): {str(e)}")
                                            elif self.api_url and self.api_key:
                                                missing = []
                                                if not item_float:
                                                    missing.append("float_value")
                                                if not item_cny_price:
                                                    missing.append("cny_price")
                                                logger.warning(f"Item {item_mhn} (offer {offer_id}) not reported to master panel: missing {', '.join(missing)}")

                                        if item_count > 1:
                                            logger.info(f"Multi-item trade {offer_id}: sent {panel_sent}/{item_count} items to master panel")
                                    else:
                                        attempt = retry_counts.get(offer_id, 0) + 1
                                        retry_counts[offer_id] = attempt
                                        if attempt >= MAX_RETRIES:
                                            ignored_offer[offer_id] = 1
                                            retry_counts.pop(offer_id, None)
                                            logger.info(f"Offer processing failed (attempt {attempt}/{MAX_RETRIES}). Added to ignore list.")
                                        else:
                                            logger.info(f"Offer processing failed (attempt {attempt}/{MAX_RETRIES}). Will retry next cycle.")

                                    if i != len(filtered_trades) - 1:
                                        logger.info("Waiting 5s before next offer to reduce Steam API pressure...")
                                        time.sleep(5)
                                except Exception as e:
                                    attempt = retry_counts.get(offer_id, 0) + 1
                                    retry_counts[offer_id] = attempt
                                    logger.error(f"Error while processing offer (attempt {attempt}/{MAX_RETRIES}): {str(e)}", exc_info=True)
                                    if attempt >= MAX_RETRIES:
                                        ignored_offer[offer_id] = 1
                                        retry_counts.pop(offer_id, None)
                                        logger.info("Error occurred. Max retries reached. Offer added to ignore list.")
                                    else:
                                        logger.info(f"Error occurred. Will retry next cycle ({attempt}/{MAX_RETRIES}).")

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
