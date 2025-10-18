import datetime
import random
import time

import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception, logger
from utils.notifier import send_notification
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu

# Move sale_price_cache from instance variable to module level
sale_price_cache = {}


class UUAutoSellItem:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAutoSellItem")
        self.config = config
        self.timeSleep = 10.0
        self.inventory_list = []
        self.buy_price_cache = {}
        self.sale_inventory_list = None

    def init(self) -> bool:
        return False

    def get_uu_sale_inventory(self):
        try:
            sale_inventory_list = self.uuyoupin.get_sell_list()
            self.logger.info(f"Listed items: {len(sale_inventory_list)}")
            self.sale_inventory_list = sale_inventory_list
            return sale_inventory_list
        except Exception as e:
            self.logger.error(f"Failed to fetch UU listed items. Error: {e}", exc_info=True)
            return []

    def get_market_sale_price(self, item_id, cnt=10, good_name=None):
        if item_id in sale_price_cache:
            if datetime.datetime.now() - sale_price_cache[item_id]["cache_time"] <= datetime.timedelta(minutes=5):
                commodity_name = sale_price_cache[item_id]["commodity_name"]
                sale_price = sale_price_cache[item_id]["sale_price"]
                self.logger.info(f"{commodity_name} uses cached result. Sale price: {sale_price:.2f}")
                return sale_price

        sale_price_rsp = self.uuyoupin.get_market_sale_list_with_abrade(item_id).json()
        if sale_price_rsp["Code"] == 0:
            rsp_list = sale_price_rsp["Data"]
            rsp_cnt = len(rsp_list)
            if rsp_cnt == 0:
                sale_price = 0
                commodity_name = ""
                self.logger.warning("No market items matched the filter")
                return sale_price
            commodity_name = rsp_list[0]["commodityName"]

            sale_price_list = []
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                if rsp_list[i]["price"] and i < cnt:
                    sale_price_list.append(float(rsp_list[i]["price"]))

            if len(sale_price_list) == 1:
                sale_price = sale_price_list[0]
            elif len(sale_price_list) > 1:
                sale_price_list.sort()
                # Take the lower of the two lowest prices if within 5%; otherwise take the higher one
                minPrice = min(sale_price_list[0], sale_price_list[1])
                if sale_price_list[1] < minPrice * 1.05:
                    sale_price = minPrice
                else:
                    sale_price = sale_price_list[1]

            self.logger.info(f"Item: {commodity_name}, sale price: {sale_price:.2f}, refs: {sale_price_list}")
        else:
            sale_price = 0
            commodity_name = ""
            self.logger.error(f"Sale price query failed. Code: {sale_price_rsp['Code']}, body: {sale_price_rsp}")

        sale_price = round(sale_price, 2)

        if sale_price != 0:
            sale_price_cache[item_id] = {
                "commodity_name": commodity_name,
                "sale_price": sale_price,
                "cache_time": datetime.datetime.now(),
            }

        return sale_price

    def sell_item(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info("No items to list for sale")
            return 0

        try:
            rsp = self.uuyoupin.call_api(
                "POST",
                "/api/commodity/Inventory/SellInventoryWithLeaseV2",
                data={"GameId": "730", "itemInfos": item_infos},  # CS:GO
            ).json()
            if rsp["Code"] == 0:
                success_count = len(item_infos)
                self.logger.info(f"Successfully listed {success_count} item(s)")
                return success_count
            else:
                self.logger.error(f"Listing failed. Code: {rsp['Code']}, body: {rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"SellInventoryWithLeaseV2 call failed: {e}", exc_info=True)
            return -1

    def change_sale_price(self, items):
        item_infos = items
        num = len(item_infos)
        if num == 0:
            self.logger.info("No items to reprice")
            return 0

        try:
            rsp = self.uuyoupin.call_api(
                "PUT",
                "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                data={"Commoditys": item_infos},
            ).json()
            if rsp["Code"] == 0:
                success_count = 0
                fail_count = 0
                data_section = rsp.get('Data', {})

                if isinstance(data_section, dict) and 'Commoditys' in data_section:
                    total_processed = len(data_section['Commoditys'])
                    for commodity_result in data_section['Commoditys']:
                        if commodity_result.get('IsSuccess') == 1:
                            success_count += 1
                        else:
                            fail_count += 1
                            error_msg = commodity_result.get('Message', 'Unknown error')
                            comm_id = commodity_result.get('CommodityId', 'Unknown ID')
                            self.logger.error(f"Failed to change price for {comm_id}: {error_msg}")

                    if 'SuccessCount' in data_section:
                        success_count = data_section.get('SuccessCount', success_count)
                        fail_count = data_section.get('FailCount', fail_count)

                if total_processed == 0 and success_count == 0 and fail_count == 0:
                    success_count = num

                self.logger.info(f"Tried {num} items. Success {success_count}, Fail {fail_count}")
                return success_count
            else:
                self.logger.error(f"Price change failed. Code: {rsp['Code']}, body: {rsp}")
                return -1
        except Exception as e:
            self.logger.error(f"PriceChangeWithLeaseV2 call failed: {e}", exc_info=True)
            return -1

    def auto_sell(self):
        self.logger.info("UUYoupin auto sale listing started")
        self.operate_sleep()

        if self.uuyoupin is not None:
            try:
                sale_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("Fetching UUYoupin inventory...")

                self.inventory_list = self.uuyoupin.get_inventory(refresh=True)

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    item_id = item["TemplateInfo"]["Id"]
                    short_name = item["TemplateInfo"]["CommodityName"]
                    buy_price = float(item.get('AssetBuyPrice', '0').replace('购￥', ''))

                    self.buy_price_cache[item_id] = buy_price

                    if item["Tradable"] is False or item["AssetStatus"] != 0:
                        continue

                    if not any((s and s in short_name) for s in self.config["uu_auto_sell_item"]["name"]):
                        continue

                    blacklist_words = self.config["uu_auto_sell_item"].get('blacklist_words', [])
                    if blacklist_words:
                        if any(s != "" and s in short_name for s in blacklist_words):
                            self.logger.info(f"Item {short_name} hit blacklist. Skip listing")
                            continue

                    try:
                        sale_price = self.get_market_sale_price(item_id, good_name=short_name)
                    except Exception as e:
                        handle_caught_exception(e, "UUAutoSellItem", known=True)
                        logger.error(f"Failed to get market price for {short_name}: {e}. Skip")
                        continue

                    if self.config['uu_auto_sell_item']['take_profile']:
                        self.logger.info(f"Use take-profit ratio {self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}")
                        if buy_price > 0:
                            sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                            self.logger.info(f"Final sale price {sale_price:.2f}")
                        else:
                            self.logger.info("No purchase price found")

                    if sale_price == 0:
                        continue

                    price_threshold = self.config['uu_auto_sell_item'].get('price_adjustment_threshold', 1.0)
                    if self.config['uu_auto_sell_item'].get('use_price_adjustment', True):
                        if sale_price > price_threshold:
                            sale_price = max(price_threshold, sale_price - 0.01)
                            sale_price = round(sale_price, 2)

                    max_price = self.config['uu_auto_sell_item'].get('max_on_sale_price', 0)
                    if max_price > 0 and sale_price > max_price:
                        self.logger.info(f"Item {short_name} exceeds max price. Skip listing")
                        continue

                    self.logger.warning(f"To list: {short_name} at {sale_price}")

                    sale_item = {
                        "AssetId": asset_id,
                        "IsCanLease": False,
                        "IsCanSold": True,
                        "Price": sale_price,
                        "Remark": "",
                    }

                    sale_item_list.append(sale_item)

                self.logger.info(f"Listing {len(sale_item_list)} item(s)...")

                self.operate_sleep()
                self.sell_item(sale_item_list)
                self.logger.info("Listing complete")

            except TypeError as e:
                handle_caught_exception(e, "UUAutoSellItem")
                self.logger.error("UUYoupin auto sale listing error")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("Unknown error. Try later.")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAutoSellItem", known=True)
                    self.logger.error("UUYoupin login expired. Please log in again.")
                    send_notification('UUYoupin login expired. Please log in again', title='UUYoupin login expired')
                    self.logger.error("Login failed. Plugin will exit.")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("UUYoupin auto sale price updater started")
        self.operate_sleep()

        try:
            self.uuyoupin.send_device_info()
            self.logger.info("Fetching UUYoupin listed-for-sale items...")
            self.get_uu_sale_inventory()

            new_sale_item_list = []
            if not self.sale_inventory_list:
                self.logger.info("No items available for repricing")
                return
            for i, item in enumerate(self.sale_inventory_list):
                asset_id = item["id"]
                item_id = item["templateId"]
                short_name = item["name"]
                buy_price = self.buy_price_cache.get(item_id, 0)

                if not any((s and s in short_name) for s in self.config["uu_auto_sell_item"]["name"]):
                    continue

                blacklist_words = self.config["uu_auto_sell_item"].get('blacklist_words', [])
                if blacklist_words:
                    if any(s != "" and s in short_name for s in blacklist_words):
                        self.logger.info(f"Reprice skip: {short_name} hit blacklist")
                        continue

                sale_price = self.get_market_sale_price(item_id, good_name=short_name)

                if self.config['uu_auto_sell_item']['take_profile']:
                    self.logger.info(f"Use take-profit ratio {self.config['uu_auto_sell_item']['take_profile_ratio']:.2f}")
                    if buy_price > 0:
                        self.logger.debug(sale_price)
                        self.logger.debug(self.get_take_profile_price(buy_price))
                        sale_price = max(sale_price, self.get_take_profile_price(buy_price))
                        self.logger.info(f"Final sale price {sale_price:.2f}")
                    else:
                        self.logger.info("No purchase price found")

                if sale_price == 0:
                    continue

                price_threshold = self.config['uu_auto_sell_item'].get('price_adjustment_threshold', 1.0)
                if self.config['uu_auto_sell_item'].get('use_price_adjustment', True):
                    if sale_price > price_threshold:
                        sale_price = max(price_threshold, sale_price - 0.01)
                        sale_price = round(sale_price, 2)

                sale_item = {
                    "CommodityId": asset_id,
                    "IsCanLease": False,
                    "IsCanSold": True,
                    "Price": sale_price,
                    "Remark": ""
                }
                new_sale_item_list.append(sale_item)

            self.logger.info(f"{len(new_sale_item_list)} item(s) can be repriced")
            self.operate_sleep()
            self.change_sale_price(new_sale_item_list)

        except TypeError as e:
            handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice")
            self.logger.error("UUYoupin auto sale listing error")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("Unknown error. Try later.")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAutoSellItem-AutoChangePrice", known=True)
                send_notification('UUYoupin login expired. Please log in again', title='UUYoupin login expired')
                self.logger.error("UUYoupin login expired. Please log in again.")
                self.logger.error("Login failed. Plugin will exit.")
                exit_code.set(1)
                return 1

    def exec(self):
        self.uuyoupin = uuyoupinapi.UUAccount(get_valid_token_for_uu())  # type: ignore
        if not self.uuyoupin:
            self.logger.error("Login failed. Plugin will exit.")
            exit_code.set(1)
            return 1
        self.logger.info(f"These items will be sold: {self.config['uu_auto_sell_item']['name']}")
        self.auto_sell()

        run_time = self.config['uu_auto_sell_item']['run_time']
        interval = self.config['uu_auto_sell_item']['interval']

        self.logger.info(f"[Auto sale] Waiting until {run_time} to run")
        self.logger.info(f"[Auto reprice] Runs every {interval} minutes")

        schedule.every().day.at(f"{run_time}").do(self.auto_sell)
        schedule.every(interval).minutes.do(self.auto_change_price)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            random.seed()
            sleep = random.randint(5, 15)
        self.logger.info(f"To avoid frequent requests, sleep {sleep} seconds between operations")
        time.sleep(sleep)

    def get_take_profile_price(self, buy_price):
        take_profile_ratio = self.config['uu_auto_sell_item']['take_profile_ratio']
        return buy_price * (1 + take_profile_ratio)
