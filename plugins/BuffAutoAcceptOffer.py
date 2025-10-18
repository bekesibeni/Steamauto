import time

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
                # Check whether steam_trade endpoint is accessible
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

            break  # Only show the first item; batch purchases are usually identical items

        return result

    def exec(self):
        logger.info("BUFF auto-accept offer plugin started. Please wait...")

        session = get_valid_session_for_buff(self.steam_client, logger)
        self.buff_account = BuffAccount(session)

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

        # Check if any of our configured accounts are bound to this BUFF account
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

        ignored_offer = {}  # Track ignore counts per offer
        REPROCESS_THRESHOLD = 10  # Re-process after being ignored this many times
        interval = self.config["buff_auto_accept_offer"]["interval"]
        dota2_support = self.config["buff_auto_accept_offer"].get("dota2_support", False)

        if 'sell_protection' in self.config['buff_auto_accept_offer']:
            logger.warning('You are using an old config. BUFF auto-fulfillment was rewritten and simplified. Delete config and regenerate!')

        if dota2_support:
            self.SUPPORT_GAME_TYPES.append({"game": "dota2", "app_id": 570})

        while True:
            try:
                logger.info("Checking BUFF items to deliver / to confirm...")
                username = self.check_buff_account_state()
                if username == "":
                    logger.info("BUFF login expired. Attempting re-login...")
                    session = get_valid_session_for_buff(self.steam_client, logger)
                    if session == "":
                        logger.error("BUFF login expired and auto re-login failed!")
                        return
                    self.buff_account = BuffAccount(session)

                notification = self.buff_account.get_notification()
                if 'error' in notification:
                    logger.error(f"Failed to fetch pending orders. Error: {notification['error']}")
                    logger.info(f"Retrying in {interval} seconds...")
                    time.sleep(interval)
                    continue

                # Parse counts
                if isinstance(notification, dict) and "to_deliver_order" in notification:
                    to_deliver_order = notification["to_deliver_order"]
                    try:
                        csgo_count = 0 if "csgo" not in to_deliver_order else int(to_deliver_order["csgo"])
                        # If DOTA2 support is off or key is missing, count is 0
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
                    # Fetch pending trades
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
                                        # Get the correct Steam client for this user_steamid
                                        user_steamid = str(trade_offer.get('user_steamid', ''))
                                        
                                        if not user_steamid:
                                            logger.warning(f"No user_steamid found in offer {trade_offer['tradeofferid']}")
                                            continue
                                            
                                        # Check if we have a client for this steamid
                                        target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                        if not target_client:
                                            logger.warning(f"No Steam client found for user_steamid: {user_steamid}")
                                            continue
                                            
                                        # Store the target client and user_steamid for later use
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

                    unprocessed_count = len(trades)
                    logger.info(f"Found {unprocessed_count} BUFF offer(s) to process")

                    try:
                        if len(trades) != 0:
                            for i, trade in enumerate(trades):
                                offer_id = trade["tradeofferid"]
                                logger.info(f"Processing offer {i+1} / {len(trades)}. Offer ID: {offer_id}")

                                process_this_offer = False

                                if offer_id in ignored_offer:
                                    ignored_offer[offer_id] += 1
                                    if ignored_offer[offer_id] > REPROCESS_THRESHOLD:
                                        logger.warning(f"Offer {offer_id} ignored {ignored_offer[offer_id]-1} times. Above threshold {REPROCESS_THRESHOLD}. Reprocessing.")
                                        del ignored_offer[offer_id]
                                        process_this_offer = True
                                    else:
                                        logger.info("Offer already handled. Skipping.")
                                        process_this_offer = False
                                else:
                                    process_this_offer = True

                                if process_this_offer:
                                    try:
                                        logger.info("Accepting offer...")
                                        desc = self.format_item_info(trade)
                                        
                                        # Get the target client for this offer using the user_steamid
                                        user_steamid = trade.get('user_steamid', '')
                                        if not user_steamid:
                                            logger.error(f"No user_steamid found for offer {offer_id}")
                                            continue
                                            
                                        # Get the correct client for this user_steamid
                                        target_client = multi_account_manager.get_client_for_steamid(user_steamid)
                                        if not target_client:
                                            logger.error(f"No Steam client found for user_steamid: {user_steamid}")
                                            continue
                                            
                                        
                                        if accept_trade_offer(target_client, self.steam_client_mutex, offer_id, desc=desc):
                                            # On success, add to ignore list with count 1
                                            ignored_offer[offer_id] = 1
                                            logger.info("Accepted. Offer added to ignore list.")

                                        if trades.index(trade) != len(trades) - 1:
                                            logger.info("Waiting 5s before next offer to reduce Steam API pressure...")
                                            time.sleep(5)
                                    except Exception as e:
                                        logger.error(f"Error while processing offer: {str(e)}", exc_info=True)
                                        logger.info("Error occurred. Will retry later.")

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
