import datetime
import time

import json5
import numpy as np
import schedule

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.models import LeaseAsset
from utils.notifier import send_notification
from utils.tools import exit_code, is_subsequence
from utils.uu_helper import get_valid_token_for_uu
from uuyoupinapi import models


class UUAutoLeaseItem:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAutoLeaseItem")
        self.config = config
        self.timeSleep = 10
        self.inventory_list = []
        self.lease_price_cache = {}
        self.compensation_type = 0

    @property
    def leased_inventory_list(self) -> list:
        return self.uuyoupin.get_uu_leased_inventory()

    def init(self) -> bool:
        if not get_valid_token_for_uu():
            self.logger.error("UUYoupin login failed. Exiting.")
            exit_code.set(1)
            return True
        return False

    def get_lease_price(self, template_id, min_price=0, max_price=20000, cnt=15):

        if template_id in self.lease_price_cache:
            if datetime.datetime.now() - self.lease_price_cache[template_id]["cache_time"] <= datetime.timedelta(minutes=20):
                commodity_name = self.lease_price_cache[template_id]["commodity_name"]
                lease_unit_price = self.lease_price_cache[template_id]["lease_unit_price"]
                long_lease_unit_price = self.lease_price_cache[template_id]["long_lease_unit_price"]
                lease_deposit = self.lease_price_cache[template_id]["lease_deposit"]
                self.logger.info(
                    f"Item {commodity_name} uses cached pricing. "
                    f"Short-term: {lease_unit_price:.2f}, Long-term: {long_lease_unit_price:.2f}, Deposit: {lease_deposit:.2f}"
                )
                return {
                    "LeaseUnitPrice": lease_unit_price,
                    "LongLeaseUnitPrice": long_lease_unit_price,
                    "LeaseDeposit": lease_deposit,
                }
        max_price = 20000 if max_price == 0 else max_price
        rsp_list = self.uuyoupin.get_market_lease_price(template_id, min_price=min_price, max_price=max_price, cnt=cnt)
        if len(rsp_list) > 0:
            rsp_cnt = len(rsp_list)
            commodity_name = rsp_list[0].CommodityName

            lease_unit_price_list = []
            long_lease_unit_price_list = []
            lease_deposit_list = []
            for i, item in enumerate(rsp_list):
                if item.LeaseUnitPrice and i < min(10, rsp_cnt):
                    lease_unit_price_list.append(float(item.LeaseUnitPrice))
                    if item.LeaseDeposit:
                        lease_deposit_list.append(float(item.LeaseDeposit))
                if item.LongLeaseUnitPrice:
                    long_lease_unit_price_list.append(float(item.LongLeaseUnitPrice))

            lease_unit_price = float(np.mean(lease_unit_price_list)) * 0.97
            lease_unit_price = max(lease_unit_price, float(lease_unit_price_list[0]), 0.01)

            long_lease_unit_price = min(lease_unit_price * 0.98, float(np.mean(long_lease_unit_price_list)) * 0.95)
            if len(long_lease_unit_price_list) == 0:
                long_lease_unit_price = max(lease_unit_price - 0.01, 0.01)
            else:
                long_lease_unit_price = max(long_lease_unit_price, float(long_lease_unit_price_list[0]), 0.01)

            lease_deposit = max(float(np.mean(lease_deposit_list)) * 0.98, float(min(lease_deposit_list)))

            self.logger.info(f"Short-term ref prices: {lease_unit_price_list}. Long-term ref prices: {long_lease_unit_price_list}")
        else:
            lease_unit_price = long_lease_unit_price = lease_deposit = 0
            commodity_name = ""

        lease_unit_price = round(lease_unit_price, 2)
        long_lease_unit_price = min(round(long_lease_unit_price, 2), lease_unit_price)
        lease_deposit = round(lease_deposit, 2)

        if self.config['uu_auto_lease_item']['enable_fix_lease_ratio'] and min_price > 0:
            ratio = self.config['uu_auto_lease_item']['fix_lease_ratio']
            lease_unit_price = max(lease_unit_price, min_price * ratio)
            long_lease_unit_price = max(long_lease_unit_price, lease_unit_price * 0.98)

            self.logger.info(
                f"Item {commodity_name}. Ratio pricing enabled. Market price {min_price}. Ratio {ratio}"
            )

        self.logger.info(
            f"Item {commodity_name}. "
            f"Short-term: {lease_unit_price:.2f}, Long-term: {long_lease_unit_price:.2f}, Deposit: {lease_deposit:.2f}"
        )
        if lease_unit_price != 0:
            self.lease_price_cache[template_id] = {
                "commodity_name": commodity_name,
                "lease_unit_price": lease_unit_price,
                "long_lease_unit_price": long_lease_unit_price,
                "lease_deposit": lease_deposit,
                "cache_time": datetime.datetime.now(),
            }

        return {
            "LeaseUnitPrice": lease_unit_price,
            "LongLeaseUnitPrice": long_lease_unit_price,
            "LeaseDeposit": lease_deposit,
        }

    def auto_lease(self):
        self.logger.info("UUYoupin auto lease listing started")
        self.operate_sleep()
        if self.uuyoupin is not None:
            try:
                lease_item_list = []
                self.uuyoupin.send_device_info()
                self.logger.info("Fetching UUYoupin inventory...")

                self.inventory_list = self.uuyoupin.get_inventory(refresh=True)

                for i, item in enumerate(self.inventory_list):
                    if item["AssetInfo"] is None:
                        continue
                    asset_id = item["SteamAssetId"]
                    template_id = item["TemplateInfo"]["Id"]
                    short_name = item["ShotName"]
                    price = item["TemplateInfo"]["MarkPrice"]
                    if (
                        price < self.config["uu_auto_lease_item"]["filter_price"]
                        or item["Tradable"] is False
                        or item["AssetStatus"] != 0
                        or any(s != "" and is_subsequence(s, short_name) for s in self.config["uu_auto_lease_item"]["filter_name"])
                    ):
                        continue
                    self.operate_sleep()

                    price_rsp = self.get_lease_price(template_id, min_price=price, max_price=price*2)
                    if price_rsp["LeaseUnitPrice"] == 0:
                        continue
                    
                    lease_item = models.UUOnLeaseShelfItem(
                        AssetId=asset_id,
                        IsCanLease=True,
                        IsCanSold=False,
                        LeaseMaxDays=self.config["uu_auto_lease_item"]["lease_max_days"],
                        LeaseUnitPrice=price_rsp["LeaseUnitPrice"],
                        LongLeaseUnitPrice=price_rsp["LongLeaseUnitPrice"],
                        LeaseDeposit=str(price_rsp["LeaseDeposit"]),
                        CompensationType=self.compensation_type
                    )
                    if self.config["uu_auto_lease_item"]["lease_max_days"] <= 8:
                        lease_item.LongLeaseUnitPrice = None

                    lease_item_list.append(lease_item)

                self.logger.info(f"Total {len(lease_item_list)} items eligible for lease.")

                self.operate_sleep()
                if len(lease_item_list) > 0:
                    success_count = self.uuyoupin.put_items_on_lease_shelf(lease_item_list)
                    if success_count > 0:
                        self.logger.info(f"Successfully listed {success_count} item(s).")
                    else:
                        self.logger.error("Listing failed. Check logs for details.")
                    if len(lease_item_list) - success_count > 0:
                        self.logger.error(f"{len(lease_item_list) - success_count} item(s) failed to list.")

            except TypeError as e:
                handle_caught_exception(e, "UUAutoLeaseItem")
                self.logger.error("UUYoupin leasing encountered an error.")
                exit_code.set(1)
                return 1
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("Unknown error. Try later.")
                try:
                    self.uuyoupin.get_user_nickname()
                except KeyError as e:
                    handle_caught_exception(e, "UUAutoLeaseItem", known=True)
                    send_notification('UUYoupin login expired. Please log in again', title='UUYoupin login expired')
                    self.logger.error("Detected UUYoupin login expiration. Please log in again.")
                    self.logger.error("Login failed. Plugin will exit.")
                    exit_code.set(1)
                    return 1

    def auto_change_price(self):
        self.logger.info("UUYoupin auto lease price updater started")
        self.operate_sleep(15)
        try:
            self.uuyoupin.send_device_info()
            self.logger.info("Fetching UUYoupin leased listings...")
            leased_item_list = self.leased_inventory_list
            for i, item in enumerate(leased_item_list):

                template_id = item.templateid
                short_name = item.short_name
                price = item.price

                if any(s != "" and is_subsequence(s, short_name) for s in self.config["uu_auto_lease_item"]["filter_name"]):
                    continue

                price_rsp = self.get_lease_price(template_id, min_price=price, max_price=price*2)
                if price_rsp["LeaseUnitPrice"] == 0:
                    continue

                item.LeaseUnitPrice = price_rsp["LeaseUnitPrice"]
                item.LongLeaseUnitPrice = price_rsp["LongLeaseUnitPrice"]
                item.LeaseDeposit = price_rsp["LeaseDeposit"]
                item.LeaseMaxDays = self.config["uu_auto_lease_item"]["lease_max_days"]
                if self.config["uu_auto_lease_item"]["lease_max_days"] <= 8:
                    item.LongLeaseUnitPrice = None

            self.logger.info(f"{len(leased_item_list)} items can be repriced for leasing.")
            self.operate_sleep()
            if len(leased_item_list) > 0:
                success_count = self.uuyoupin.change_leased_price(leased_item_list, compensation_type=self.compensation_type)
                self.logger.info(f"Successfully updated lease price for {success_count} item(s).")
                if len(leased_item_list) - success_count > 0:
                    self.logger.error(f"{len(leased_item_list) - success_count} item(s) failed to update lease price.")
            else:
                self.logger.info("No items to update.")

        except TypeError as e:
            handle_caught_exception(e, "UUAutoLeaseItem-AutoChangePrice")
            self.logger.error("UUYoupin leasing encountered an error.")
            exit_code.set(1)
            return 1
        except Exception as e:
            self.logger.error(e, exc_info=True)
            self.logger.info("Unknown error. Try later.")
            try:
                self.uuyoupin.get_user_nickname()
            except KeyError as e:
                handle_caught_exception(e, "UUAutoLeaseItem-AutoChangePrice", known=True)
                self.logger.error("Detected UUYoupin login expiration. Please log in again.")
                self.logger.error("Login failed. Plugin will exit.")
                exit_code.set(1)
                return 1

    def auto_set_zero_cd(self):
        self.logger.info("UUYoupin auto set 0cd started")
        self.operate_sleep()
        if self.uuyoupin is not None:
            try:
                zero_cd_valid_list = self.uuyoupin.get_zero_cd_list()
                enable_zero_cd_list = []
                for order in zero_cd_valid_list:
                    name = order["commodityInfo"]["name"]
                    if any(s != "" and is_subsequence(s, name) for s in self.config["uu_auto_lease_item"]["filter_name"]):
                        continue
                    enable_zero_cd_list.append(int(order["orderId"]))
                self.logger.info(f"Total {len(enable_zero_cd_list)} items can be set to 0cd.")
                if len(enable_zero_cd_list) > 0:
                    self.uuyoupin.enable_zero_cd(enable_zero_cd_list)
            except Exception as e:
                self.logger.error(e, exc_info=True)
                self.logger.info("Unknown error. Try later.")

    def exec(self):
        self.logger.info(f"These items will NOT be leased: {self.config['uu_auto_lease_item']['filter_name']}")
        if "compensation_type" in self.config['uu_auto_lease_item']:
            self.compensation_type = self.config['uu_auto_lease_item']['compensation_type']

        self.uuyoupin = uuyoupinapi.UUAccount(get_valid_token_for_uu())

        self.pre_check_price()
        self.auto_lease()
        self.auto_set_zero_cd()

        run_time = self.config['uu_auto_lease_item']['run_time']
        interval = self.config['uu_auto_lease_item']['interval']
        if "zero_cd_run_time" in self.config['uu_auto_lease_item']:
            zero_cd_run_time = self.config['uu_auto_lease_item']['zero_cd_run_time']
        else:
            zero_cd_run_time = "23:30"
        self.logger.info(f"[Auto lease] Waiting until {run_time} to run.")
        self.logger.info(f"[Auto reprice] Runs every {interval} minutes.")
        self.logger.info(f"[Set 0cd] Waiting until {zero_cd_run_time} to run.")

        schedule.every().day.at(f"{run_time}").do(self.auto_lease)
        schedule.every(interval).minutes.do(self.auto_change_price)
        schedule.every().day.at(f"{zero_cd_run_time}").do(self.auto_set_zero_cd)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def operate_sleep(self, sleep=None):
        if sleep is None:
            time.sleep(self.timeSleep)
        else:
            time.sleep(sleep)

    def pre_check_price(self):
        self.get_lease_price(44444, 1000)
        self.logger.info("Verify deposit retrieval looks correct. If not, stop the program. Otherwise the plugin will proceed.")
        self.operate_sleep()


if __name__ == "__main__":
    # Debug code
    with open("config/config.json5", "r", encoding="utf-8") as f:
        my_config = json5.load(f)

    uu_auto_lease = UUAutoLeaseItem(None, None, my_config)
    token = get_valid_token_for_uu()
    if not token:
        uu_auto_lease.logger.error("Login failed. Plugin will exit.")
        exit_code.set(1)
    else:
        uu_auto_lease.uuyoupin = uuyoupinapi.UUAccount(token)
    uu_auto_lease.auto_change_price()
