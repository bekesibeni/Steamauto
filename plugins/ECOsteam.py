import copy
import datetime
import json
import os
import time
from threading import Thread
from typing import Dict, List, Union

from BuffApi import BuffAccount
from BuffApi.models import BuffOnSaleAsset
from PyECOsteam import ECOsteamClient, models
from steampy.client import SteamClient
from utils import static
from utils.buff_helper import get_valid_session_for_buff
from utils.logger import LogFilter, PluginLogger, handle_caught_exception
from utils.models import Asset, LeaseAsset, ModelEncoder
from utils.static import ECOSTEAM_RSAKEY_FILE
from utils.steam_client import accept_trade_offer, external_handler, get_cs2_inventory
from utils.tools import exit_code, get_encoding
from utils.uu_helper import get_valid_token_for_uu
from uuyoupinapi import UUAccount

sync_sell_shelf_enabled = False
sync_lease_shelf_enabled = False

uu_queue = None
eco_queue = None

logger = PluginLogger("ECOsteam.cn")
sell_logger = PluginLogger("[ECOsteam.cn] [Sync multi-platform sales]")
lease_logger = PluginLogger("[ECOsteam.cn] [Sync lease shelves]")
accept_offer_logger = PluginLogger("[ECOsteam.cn] [Auto delivery]")


def compare_shelves(A: List[Asset], B: List[Asset], ratio: float) -> Union[bool, dict[str, list[Asset]]]:
    result = {"add": [], "delete": [], "change": []}
    ratio = round(ratio, 2)

    # Warn and remove elements that are not Asset
    for asset in A.copy():
        if not isinstance(asset, Asset):
            sell_logger.debug("List A may contain items not yet off-shelved")
            A.remove(asset)
    for asset in B.copy():
        if not isinstance(asset, Asset):
            sell_logger.debug("List B may contain items not yet off-shelved")
            B.remove(asset)

    # Dicts for quick lookup
    A_dict = {item.assetid: item for item in A}
    B_dict = {item.assetid: item for item in B}

    # Added and deleted
    for assetid in A_dict:
        if assetid not in B_dict:
            adjusted_dict = A_dict[assetid].model_copy()
            adjusted_dict.price = round(adjusted_dict.price / ratio, 2)
            result["add"].append(adjusted_dict)

    for assetid in B_dict:
        if assetid not in A_dict:
            result["delete"].append(B_dict[assetid])

    # Price changes
    for assetid in A_dict:
        if assetid in B_dict:
            A_price = A_dict[assetid].price
            B_price = B_dict[assetid].price
            if abs(round(A_price / B_price - ratio, 2)) > 0.01:
                adjusted_dict = B_dict[assetid].model_copy()
                adjusted_dict.price = round(A_price / ratio, 2)
                result["change"].append(adjusted_dict)

    return result


def compare_lease_shelf(A: List[LeaseAsset], B: List[LeaseAsset], ratio: float) -> Dict[str, List[LeaseAsset]]:
    result = {"add": [], "delete": [], "change": []}
    ratio = round(ratio, 2)

    # Warn and remove elements that are not Asset
    for asset in A:
        if not isinstance(asset, Asset):
            sell_logger.debug("List A may contain items not yet off-shelved")
            A.remove(asset)
    for asset in B:
        if not isinstance(asset, Asset):
            sell_logger.debug("List B may contain items not yet off-shelved")
            B.remove(asset)

    A_dict = {item.assetid: item for item in A}
    B_dict = {item.assetid: item for item in B}

    for assetid in A_dict:
        if assetid not in B_dict:
            result["add"].append(A_dict[assetid])

    for assetid in B_dict:
        if assetid not in A_dict:
            result["delete"].append(B_dict[assetid])

    for assetid in A_dict:
        if assetid in B_dict:
            A_item = A_dict[assetid]
            B_item = B_dict[assetid]

            changes_needed = False
            if A_item.LeaseDeposit != B_item.LeaseDeposit:
                changes_needed = True
            if A_item.LeaseMaxDays != B_item.LeaseMaxDays:
                changes_needed = True
            if round(abs(A_item.LeaseUnitPrice / B_item.LeaseUnitPrice - ratio), 2) >= 0.01:
                changes_needed = True
            if A_item.LongLeaseUnitPrice and B_item.LongLeaseUnitPrice:
                if round(abs(A_item.LongLeaseUnitPrice / B_item.LongLeaseUnitPrice - ratio), 2) > 0.01:
                    changes_needed = True
            elif A_item.LongLeaseUnitPrice != B_item.LongLeaseUnitPrice:
                changes_needed = True

            if changes_needed:
                adjusted_dict = A_item.model_copy()
                adjusted_dict.orderNo = B_item.orderNo
                adjusted_dict.LeaseUnitPrice = round(A_item.LeaseUnitPrice / ratio, 2)
                if A_item.LongLeaseUnitPrice:
                    adjusted_dict.LongLeaseUnitPrice = round(A_item.LongLeaseUnitPrice / ratio, 2)
                result["change"].append(adjusted_dict)

    return result


class tasks:
    def __init__(self, client, steamid) -> None:
        self.sell_queue = []
        self.sell_change_queue = []
        self.lease_queue = []
        self.lease_change_queue = []
        self.client = client
        self.steamid = steamid
        if isinstance(self.client, ECOsteamClient):
            self.platform = "ECOsteam"
        elif isinstance(self.client, UUAccount):
            self.platform = "UUyoupin"

    def sell_add(self, assets: List[Asset]):
        self.sell_queue += assets

    def sell_change(self, assets: List[Asset]):
        self.sell_change_queue += assets

    def sell_remove(self, assetId: str):
        for asset in self.sell_queue:
            if asset.assetid == assetId:
                self.sell_queue.remove(asset)
                break

    def lease_add(self, assets: List[LeaseAsset]):
        self.lease_queue += assets

    def lease_change(self, assets: List[LeaseAsset]):
        self.lease_change_queue += assets

    def lease_remove(self, assetId: str):
        for asset in self.lease_queue:
            if asset.assetid == assetId:
                self.lease_queue.remove(asset)
                break

    def process(self):
        logger.debug(self.platform + " sell queue: " + json.dumps(self.sell_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + " lease queue: " + json.dumps(self.lease_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + " sell reprice queue: " + json.dumps(self.sell_change_queue, cls=ModelEncoder, ensure_ascii=False))
        logger.debug(self.platform + " lease reprice queue: " + json.dumps(self.lease_change_queue, cls=ModelEncoder, ensure_ascii=False))
        if len(self.sell_queue) > 0 or len(self.lease_queue) > 0 or len(self.sell_change_queue) > 0 or len(self.lease_change_queue) > 0:
            logger.info(self.platform + " task queue start")
        else:
            logger.info(self.platform + " task queue empty. Nothing to do")

        if len(self.sell_queue) > 0 or len(self.lease_queue) > 0:
            logger.info(f"Will list {len(self.sell_queue)} items to sale shelf")
            logger.info(f"Will list {len(self.lease_queue)} items to lease shelf")
            success_count, failure_count = 0, 0
            try:
                if isinstance(self.client, ECOsteamClient):
                    success_count, failure_count = self.client.PublishRentAndSaleGoods(self.steamid, 1, self.sell_queue, self.lease_queue)
                elif isinstance(self.client, UUAccount):
                    success_count, failure_count = self.client.onshelf_sell_and_lease(self.sell_queue, self.lease_queue)
            except Exception as e:
                handle_caught_exception(e, known=False)
                logger.error(f"Error during listing: {e}")
            self.sell_queue = []
            self.sell_change_queue = []
            if failure_count != 0:
                logger.error(f"Listing failed for {failure_count} items")
            logger.info(f"Listing succeeded for {success_count} items")

        if len(self.sell_change_queue) > 0 or len(self.lease_change_queue) > 0:
            logger.info(f"Will reprice {len(self.sell_change_queue)} sale items")
            logger.info(f"Will reprice {len(self.lease_change_queue)} lease items")
            success_count, failure_count = 0, 0
            if isinstance(self.client, ECOsteamClient):
                success_count, failure_count = self.client.PublishRentAndSaleGoods(self.steamid, 2, self.sell_change_queue, self.lease_change_queue)
            elif isinstance(self.client, UUAccount):
                success_count, failure_count = self.client.change_price_sell_and_lease(self.sell_change_queue, self.lease_change_queue)
            self.lease_queue = []
            self.lease_change_queue = []
            if failure_count != 0:
                logger.error(f"Reprice failed for {failure_count} items")
            logger.info(f"Reprice succeeded for {success_count} items")


class ECOsteamPlugin:
    def __init__(self, steam_client: SteamClient, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.ignored_offer = []
        self.steam_id = static.STEAM_64_ID

    def init(self):
        if not os.path.exists(ECOSTEAM_RSAKEY_FILE):
            with open(ECOSTEAM_RSAKEY_FILE, "w", encoding="utf-8") as f:
                f.write("")
            return True
        return False

    def exec(self):
        logger.info("ECOsteam plugin started")
        logger.info("Logging in to ECOsteam...")
        try:
            with open(ECOSTEAM_RSAKEY_FILE, "r", encoding=get_encoding(ECOSTEAM_RSAKEY_FILE)) as f:
                rsa_key = f.read()
            if "PUBLIC" in rsa_key:
                logger.error("The rsakey file contains a PUBLIC key. Put the PRIVATE key in rsakey.txt.")
                return 1
            LogFilter.add_sensitive_data(self.config["ecosteam"]["partnerId"])
            self.client = ECOsteamClient(
                self.config["ecosteam"]["partnerId"],
                rsa_key,
                qps=self.config["ecosteam"]["qps"],
            )
            user_info = self.client.GetTotalMoney().json()
            if user_info["ResultData"].get("UserName", None):
                logger.info(f'Login success. User ID {user_info["ResultData"]["UserName"]}. Balance {user_info["ResultData"]["Money"]} RMB')
            else:
                raise Exception
        except Exception as e:
            logger.error(f"Login failed. Check {ECOSTEAM_RSAKEY_FILE} and partnerId. Exiting plugin.")
            handle_caught_exception(e, known=True)
            exit_code.set(1)
            return 1

        # Check if current Steam account is bound in ECOsteam
        exist = False
        accounts_list = self.client.QuerySteamAccountList().json()["ResultData"]
        for account in accounts_list:
            if account["SteamId"] == self.steam_id:
                exist = True
                break
        if not exist:
            logger.error(f"Current Steam account {self.steam_id} is not bound in ECOsteam. Exiting.")
            exit_code.set(1)
            return 1
        if exist and len(accounts_list) > 1:
            logger.warning(f"Multiple Steam accounts bound in ECOsteam. All actions apply only to SteamID {self.steam_id}. Start multiple Steamauto instances for multi-account ops.")

        threads = []
        threads.append(Thread(target=self.auto_accept_offer))
        if self.config["ecosteam"]["auto_sync_sell_shelf"]["enable"] or self.config["ecosteam"]["auto_sync_lease_shelf"]["enable"]:
            threads.append(Thread(target=self.auto_sync_shelves))
        if not len(threads) == 1:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        else:
            self.auto_accept_offer()

    # Fetch sales shelves for platforms
    def get_shelf(self, platform, inventory):
        assets = list()
        if platform == "eco":
            result = self.client.getFullSellGoodsList(self.steam_id)
            if not inventory:
                raise SystemError
            for item in result:
                asset = Asset(assetid=item["AssetId"], orderNo=item["GoodsNum"], price=float(item["Price"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(f"ECOsteam listed item {item['GoodsName']} not found in Steam inventory")
                    assets.append(asset.orderNo)
            return assets
        elif platform == "buff":
            data = self.buff_client.get_on_sale().json()["data"]
            items = data["items"]
            if data['total_count'] > 500:
                items += self.buff_client.get_on_sale(page_num=2).json()["data"]["items"]
            for item in items:
                asset = Asset(assetid=item["asset_info"]["assetid"], orderNo=item["id"], price=float(item["price"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(f"BUFF listed item {data['goods_infos'][str(item['goods_id'])]['market_hash_name']} not found in Steam inventory")
                    assets.append(asset.orderNo)
            return assets
        elif platform == "uu":
            data = self.uu_client.get_sell_list()
            for item in data:
                asset = Asset(assetid=str(item["steamAssetId"]), orderNo=item["id"], price=float(item["sellAmount"]))
                try:
                    asset.appid = inventory[asset.assetid]["appid"]
                    asset.classid = inventory[asset.assetid]["classid"]
                    asset.contextid = inventory[asset.assetid]["contextid"]
                    asset.instanceid = inventory[asset.assetid]["instanceid"]
                    asset.market_hash_name = inventory[asset.assetid]["market_hash_name"]
                    assets.append(asset)
                except KeyError:
                    sell_logger.warning(f"UU listed item {item['name']} not found in Steam inventory")
                    assets.append(asset.orderNo)
            return assets

    # Auto delivery thread
    def auto_accept_offer(self):
        while True:
            try:
                self.__auto_accept_offer()
            except Exception as e:
                handle_caught_exception(e, "ECOsteam.cn")
                accept_offer_logger.error("Unknown error. Retry later.")
                time.sleep(self.config["ecosteam"]["auto_accept_offer"]["interval"])

    # Auto delivery implementation
    def __auto_accept_offer(self):
        accept_offer_logger.info("Checking pending deliveries...")
        today = datetime.datetime.today()
        tomorrow = datetime.datetime.today() + datetime.timedelta(days=1)
        last_month = today - datetime.timedelta(days=30)
        tomorrow = tomorrow.strftime("%Y-%m-%d")
        last_month = last_month.strftime("%Y-%m-%d")
        wait_deliver_orders = self.client.getFullSellerOrderList(last_month, tomorrow, DetailsState=8, SteamId=self.steam_id)
        accept_offer_logger.info(f"Found {len(wait_deliver_orders)} pending orders")
        if len(wait_deliver_orders) > 0:
            for order in wait_deliver_orders:
                if order['OrderStateCode'] == 1:
                    if not external_handler('ECO-' + str(order['OrderNum']), desc=f"Platform: ECOsteam\nItem: {order['GoodsName']}\nOrder price: {order['OrderAmount']}"):
                        accept_offer_logger.info(f"Order {order['OrderNum']} ignored by external handler. Skip sending offer")
                        continue
                    logger.info(f"Order {order['OrderNum']} has no offer. Sending offer...")
                    try:
                        self.client.SellerSendOffer(OrderNum=order["OrderNum"], GameId=730)
                        accept_offer_logger.info(f"Order {order['OrderNum']} offer sent")
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn")
                        accept_offer_logger.error(f"Order {order['OrderNum']} offer failed. Retry later.")
                        continue
                accept_offer_logger.debug(f"Fetching details for order {order['OrderNum']}")
                detail = self.client.GetSellerOrderDetail(OrderNum=order["OrderNum"]).json()["ResultData"]
                time.sleep(0.3)
                tradeOfferId = detail["TradeOfferId"]
                goodsName = detail["GoodsName"]
                sellingPrice = detail["TotalMoney"]
                buyerNickName = detail["BuyerNickname"]
                if not tradeOfferId:
                    accept_offer_logger.warning(f"Item {goodsName} has no trade offer id yet (ECO may still be sending). Skip for now")
                    continue
                if tradeOfferId not in self.ignored_offer:
                    accept_offer_logger.info(f"Delivering {goodsName}, offer {tradeOfferId}...")
                    if accept_trade_offer(self.steam_client, self.steam_client_mutex, tradeOfferId, desc=f"Platform: ECOsteam\nItem: {goodsName}\nOrder price: {sellingPrice}\nBuyer: {buyerNickName}", reportToExternal=False):
                        accept_offer_logger.info(f"Delivered {goodsName}, offer {tradeOfferId}")
                        self.ignored_offer.append(tradeOfferId)
                else:
                    accept_offer_logger.info(f"Ignored offer {tradeOfferId} for {goodsName} as already processed")
        interval = self.config["ecosteam"]["auto_accept_offer"]["interval"]
        accept_offer_logger.info(f"Wait {interval}s then re-check pending deliveries")
        time.sleep(interval)

    # Auto sync shelves thread launcher
    def auto_sync_shelves(self):
        global sync_lease_shelf_enabled
        global sync_sell_shelf_enabled
        # Config checks
        if self.config["ecosteam"]["auto_sync_sell_shelf"]["enable"]:
            config_sync_sell_shelf = self.config["ecosteam"]["auto_sync_sell_shelf"]
            sync_sell_shelf_enabled = True
            config_sync_sell_shelf["enabled_platforms"].append("eco")
            if not config_sync_sell_shelf["main_platform"] in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.error("Main platform must be in enabled_platforms")
                sync_sell_shelf_enabled = False
            platforms = list(copy.deepcopy(config_sync_sell_shelf["enabled_platforms"]))
            while len(platforms) > 0:
                platform = platforms.pop()
                if not (platform == "uu" or platform == "eco" or platform == "buff"):
                    sell_logger.error("Only UU/ECO/BUFF supported. Check config.")
                    sync_sell_shelf_enabled = False
                    break
            if not config_sync_sell_shelf["main_platform"] in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.error("Main platform disabled. Auto sync turned off")
                sync_sell_shelf_enabled = False
            if not sync_sell_shelf_enabled:
                sell_logger.error("Auto sync turned off due to config errors")
                return

            # BUFF login
            if "buff" in config_sync_sell_shelf["enabled_platforms"]:
                sell_logger.info("BUFF enabled. Getting valid session from BuffLoginSolver...")
                buff_session = ""
                with self.steam_client_mutex:
                    buff_session = get_valid_session_for_buff(self.steam_client, sell_logger)
                if not buff_session:
                    sell_logger.warning("No valid BUFF session. Disabling BUFF in sync")
                    config_sync_sell_shelf["enabled_platforms"].remove("buff")
                else:
                    self.buff_client = BuffAccount(buff_session)
                    sell_logger.info("Valid BUFF session acquired")

            # UU login
            if "uu" in config_sync_sell_shelf["enabled_platforms"] and not (hasattr(self, "uu_client") and self.uu_client):
                sell_logger.info("UU enabled. Getting valid token from UULoginSolver...")
                token = get_valid_token_for_uu()
                if token:
                    self.uu_client = UUAccount(token)
                else:
                    sell_logger.warning("No valid UU token. Disabling UU sale sync")
                    config_sync_sell_shelf["enabled_platforms"].remove("uu")

            # Any platform left?
            if len(config_sync_sell_shelf["enabled_platforms"]) == 1:
                sell_logger.error("No usable platform. Disabling sale shelf sync")
                sync_sell_shelf_enabled = False

        if self.config["ecosteam"]['auto_sync_lease_shelf']['enable']:
            # Ensure UU login
            if not (hasattr(self, "uu_client") and self.uu_client):
                lease_logger.info("UU enabled. Getting valid token from UULoginSolver...")
                token = get_valid_token_for_uu()
                if token:
                    self.uu_client = UUAccount(token)
                else:
                    lease_logger.warning("No valid UU token. Disabling lease sync")
                    return
            self.lease_main_platform = self.config["ecosteam"]["auto_sync_lease_shelf"]["main_platform"]
            if self.lease_main_platform != "uu" and self.lease_main_platform != "eco":
                lease_logger.error("Main platform must be 'uu' or 'eco'")
                return
            sync_lease_shelf_enabled = True
        global uu_queue
        global eco_queue
        if hasattr(self, "uu_client") and self.uu_client:
            uu_queue = tasks(self.uu_client, self.steam_id)
        eco_queue = tasks(self.client, self.steam_id)

        while True:
            if sync_sell_shelf_enabled:
                self.sync_sell_shelves()
            if sync_lease_shelf_enabled:
                self.sync_lease_shelves()
            eco_queue.process()
            if isinstance(uu_queue, tasks):
                uu_queue.process()
            logger.info(f'Wait {self.config["ecosteam"]["sync_interval"]}s then re-check multi-platform shelves')
            time.sleep(self.config["ecosteam"]["sync_interval"])

    # Lease shelf sync implementation
    def sync_lease_shelves(self):
        lease_logger.info("Fetching ECOsteam lease listings...")
        lease_shelves = {}
        lease_shelves['eco'] = self.client.getFulRentGoodsList(self.steam_id)
        lease_logger.debug(f'ECO lease shelf: {json.dumps(lease_shelves["eco"], cls=ModelEncoder)}')
        lease_logger.info(f"ECOsteam has {len(lease_shelves['eco'])} lease items")

        lease_logger.info("Fetching UU lease listings...")
        lease_shelves['uu'] = self.uu_client.get_uu_leased_inventory()
        lease_logger.debug(f'UU lease shelf: {json.dumps(lease_shelves["uu"], cls=ModelEncoder)}')
        lease_logger.info(f"UU has {len(lease_shelves['uu'])} lease items")

        if self.lease_main_platform == "eco":
            lease_logger.info("Lease sync main platform: ECOsteam")
            self.lease_other_platform = "uu"
        else:
            lease_logger.info("Lease sync main platform: UU")
            self.lease_other_platform = "eco"

        lease_logger.debug(f'Comparing {self.lease_main_platform.upper()} vs {self.lease_other_platform.upper()} for lease listings')
        difference = compare_lease_shelf(
            lease_shelves[self.lease_main_platform],
            lease_shelves[self.lease_other_platform],
            self.config['ecosteam']['auto_sync_lease_shelf']['ratio'][self.lease_main_platform]
            / self.config['ecosteam']['auto_sync_lease_shelf']['ratio'][self.lease_other_platform],
        )
        lease_logger.debug(f"Lease - target platform: {self.lease_other_platform.upper()}\nDifference: {json.dumps(difference, cls=ModelEncoder)}")
        if difference != {"add": [], "delete": [], "change": []}:
            lease_logger.warning(f"{self.lease_other_platform.upper()} needs lease listing/price updates")
            if self.lease_other_platform == "uu":
                # Add
                if len(difference['add']) > 0:
                    if isinstance(uu_queue, tasks):
                        uu_queue.lease_add(difference['add'])
                        lease_logger.info(f"Queued {len(difference['add'])} items for UU lease listing")
                    else:
                        lease_logger.error("UU task queue not initialized")
                # Remove
                if len(difference['delete']) > 0:
                    lease_logger.info(f"Off-shelving {len(difference['delete'])} UU lease items")
                    rsp = self.uu_client.off_shelf([item.orderNo for item in difference["delete"]]).json()
                    if rsp['Code'] == 0:
                        lease_logger.info(f"Off-shelved {len(difference['delete'])} UU items")
                    else:
                        lease_logger.error(f"Off-shelf failed. Error: {rsp['Msg']}")
                # Change
                if len(difference['change']) > 0:
                    if isinstance(uu_queue, tasks):
                        uu_queue.lease_change(difference['change'])
                        lease_logger.info(f"Queued {len(difference['change'])} items for UU lease repricing")
                    else:
                        lease_logger.error("UU task queue not initialized")
            elif self.lease_other_platform == "eco":
                # Add
                if len(difference['add']) > 0:
                    if isinstance(eco_queue, tasks):
                        eco_queue.lease_add(difference['add'])
                        lease_logger.info(f"Queued {len(difference['add'])} items for ECO lease listing")
                    else:
                        lease_logger.error("ECOsteam task queue not initialized")

                # Remove
                if len(difference['delete']) > 0:
                    lease_logger.info(f"Off-shelving {len(difference['delete'])} ECO lease items")
                    batches = [difference['delete'][i: i + 100] for i in range(0, len(difference['delete']), 100)]
                    success_count = 0
                    for batch in batches:
                        try:
                            rsp = self.client.OffshelfRentGoods([models.GoodsNum(AssetId=item.assetid, SteamGameId=str(item.appid)) for item in batch]).json()
                            if rsp['ResultCode'] == '0':
                                success_count += len(batch)
                            else:
                                lease_logger.error(f"Lease off-shelf failed. Error: {rsp['ResultMsg']}")
                        except Exception as e:
                            handle_caught_exception(e, "ECOsteam.cn", known=True)
                            lease_logger.error("Unknown error. Retry later.")
                    lease_logger.info(f"Off-shelved {success_count} ECO lease items")

                # Change
                if len(difference['change']) > 0:
                    if isinstance(eco_queue, tasks):
                        eco_queue.lease_change(difference['change'])
                        lease_logger.info(f"Queued {len(difference['change'])} items for ECO lease repricing")
                    else:
                        lease_logger.error("ECOsteam task queue not initialized")

    # Sale shelf sync implementation
    def sync_sell_shelves(self):
        tc = copy.deepcopy(self.config["ecosteam"]["auto_sync_sell_shelf"])
        main_platform = tc["main_platform"]
        shelves = {}
        ratios = {}
        for platform in tc["enabled_platforms"]:
            shelves[platform] = list()
            ratios[platform] = tc["ratio"][platform]
        sell_logger.info("Fetching Steam inventory...")
        inventory = get_cs2_inventory(self.steam_client, self.steam_client_mutex)
        if not inventory:
            sell_logger.error("Steam error. Cannot fetch inventory now.")
            return
        else:
            sell_logger.info(f"Steam inventory has {len(inventory)} items")

        try:
            for platform in tc["enabled_platforms"]:
                sell_logger.info(f"Fetching {platform.upper()} listings...")
                shelves[platform] = self.get_shelf(platform, inventory)
                sell_logger.info(f"{platform.UPPER()} has {len(shelves[platform])} listed items")
                # Off-shelve items not in Steam inventory
                if len(shelves[platform]) > 0:
                    offshelf_list = []
                    for good in shelves[platform]:
                        if not isinstance(good, Asset):
                            offshelf_list.append(good)
                    if len(offshelf_list) > 0:
                        sell_logger.warning(f"Detected {len(offshelf_list)} {platform.upper()} items not in Steam inventory. Off-shelving")
                        if platform == "eco":
                            success_count, failure_count = self.client.OffshelfGoods([models.GoodsNum(GoodsNum=good, SteamGameId='730') for good in offshelf_list])
                            sell_logger.info(f"Off-shelved {success_count} items")
                            if failure_count != 0:
                                sell_logger.error(f"Failed to off-shelf {failure_count} items")
                        elif platform == "buff":
                            try:
                                count, problems = self.buff_client.cancel_sale(offshelf_list)
                                sell_logger.info(f"Off-shelved {count} BUFF items. Failed {len(problems)}")
                            except Exception as e:
                                handle_caught_exception(e, "ECOsteam.cn", known=True)
                                sell_logger.error("Off-shelf failed. Some may have succeeded")
                        elif platform == "uu":
                            response = self.uu_client.off_shelf(offshelf_list)
                            if int(response.json()["Code"]) == "0":
                                sell_logger.info(f"Off-shelved {len(offshelf_list)} UU items")
                            else:
                                sell_logger.error(f"Off-shelved {len(offshelf_list)} UU items failed. {str(response.json())}")
                        shelves[platform] = self.get_shelf(platform, inventory)
        except Exception as e:
            handle_caught_exception(e, "ECOsteam.cn")

        for platform in tc["enabled_platforms"]:
            if platform != main_platform:
                sell_logger.debug(f"Comparing {main_platform.upper()} vs {platform.upper()} shelves")
                difference = compare_shelves(
                    shelves[main_platform],
                    shelves[platform],
                    ratios[main_platform] / ratios[platform],
                )
                sell_logger.debug(f"Platform: {platform.upper()}\nDifference: {json.dumps(difference, cls=ModelEncoder, ensure_ascii=False)}")
                if difference != {"add": [], "delete": [], "change": []}:
                    sell_logger.warning(f"{platform.upper()} requires listing/price updates")
                    try:
                        self.solve_platform_difference(platform, difference)
                    except Exception as e:
                        handle_caught_exception(e, "ECOsteam.cn")
                        sell_logger.error("Unknown error. Retry later.")
                else:
                    sell_logger.info(f"{platform.upper()} already in sync")

    def solve_platform_difference(self, platform, difference):
        if platform == "eco":
            # Add
            if len(difference["add"]) > 0:
                if isinstance(eco_queue, tasks):
                    eco_queue.sell_add(difference["add"])
                    sell_logger.info(f"Queued {len(difference['add'])} items for ECO sale listing")
                else:
                    sell_logger.error("ECOsteam task queue not initialized")

            # Delete
            assets = [asset.orderNo for asset in difference["delete"]]
            if len(assets) > 0:
                sell_logger.info(f"Off-shelving {len(assets)} items on ECO")
                success_count, failure_count = self.client.OffshelfGoods([models.GoodsNum(GoodsNum=goodsNum, SteamGameId='730') for goodsNum in assets])
                sell_logger.info(f"Off-shelved {success_count} items")
                if failure_count != 0:
                    sell_logger.error(f"Failed to off-shelf {failure_count} items")

            # Change
            if len(difference["change"]) > 0:
                if isinstance(eco_queue, tasks):
                    eco_queue.sell_change(difference["change"])
                    sell_logger.info(f"Queued {len(difference['change'])} items for ECO sale repricing")
                else:
                    sell_logger.error("ECOsteam task queue not initialized")

        elif platform == "buff":
            # Add
            assets = difference["add"]
            if len(assets) > 0:
                buff_assets = [BuffOnSaleAsset.from_Asset(asset) for asset in assets]
                sell_logger.info(f"Listing {len(assets)} items on BUFF")
                try:
                    total_success, total_failure = [], []
                    for batch in [buff_assets[i: i + 200] for i in range(0, len(buff_assets), 200)]:
                        success, failure = self.buff_client.on_sale(batch)
                        total_success += success
                        total_failure += failure
                        if batch == 200:
                            time.sleep(3)
                    for asset in assets:
                        if asset.assetid in failure:
                            sell_logger.error(f"List {asset.market_hash_name}(ID:{asset.assetid}) failed. Error: {failure[asset.assetid]}")
                    sell_logger.info(f"Listed {len(success)} on BUFF. Failed {len(failure)}")
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    sell_logger.error("Listing failed. Some may have succeeded")

            # Delete
            assets = difference["delete"]
            if len(assets) > 0:
                sell_orders = [asset.orderNo for asset in difference["delete"]]
                sell_logger.info(f"Off-shelving {len(assets)} items on BUFF")
                try:
                    success, problem = self.buff_client.cancel_sale(sell_orders)
                    for asset in assets:
                        if asset.orderNo in problem:
                            sell_logger.error(f"Off-shelf {asset.market_hash_name}(ID:{asset.assetid}) failed. Error: {problem[asset.orderNo]}")
                    sell_logger.info(f"Off-shelved {success}. Failed {len(problem)}")
                except Exception as e:
                    handle_caught_exception(e, "ECOsteam.cn")
                    sell_logger.error("Off-shelf failed. Some may have succeeded")

            # Change
            assets = difference["change"]
            if len(assets) > 0:
                sell_orders = [
                    {
                        "sell_order_id": asset.orderNo,
                        "price": asset.price,
                        "desc": "",
                    }
                    for asset in assets
                ]
                sell_logger.info(f"Repricing {len(assets)} items on BUFF")
                success, problem_sell_orders = self.buff_client.change_price(sell_orders)
                for asset in assets:
                    if asset.orderNo in problem_sell_orders.keys():
                        sell_logger.error(f"Reprice {asset.market_hash_name}(ID:{asset.assetid}) failed. Error: {problem_sell_orders[asset.orderNo]}")
                sell_logger.info(f"Repriced {success}. Failed {len(problem_sell_orders)}")

        elif platform == "uu":
            # Add
            if len(difference["add"]) > 0:
                if isinstance(uu_queue, tasks):
                    uu_queue.sell_add(difference["add"])
                    sell_logger.info(f"Queued {len(difference['add'])} items for UU sale listing")
                else:
                    sell_logger.error("UU task queue not initialized")

            # Delete
            delete = difference["delete"]
            assets = [str(item.orderNo) for item in delete]
            if len(assets) > 0:
                sell_logger.info(f"Off-shelving {len(assets)} items on UU")
                response = self.uu_client.off_shelf(assets)
                if int(response.json()["Code"]) == 0:
                    sell_logger.info(f"Off-shelved {len(assets)} items")
                else:
                    sell_logger.error(f"Off-shelf failed for {len(assets)}. Error: {str(response.json()['Msg'])}")

            # Change
            if len(difference["change"]) > 0:
                if isinstance(uu_queue, tasks):
                    uu_queue.sell_change(difference["change"])
                    sell_logger.info(f"Queued {len(difference['change'])} items for UU sale repricing")
                else:
                    sell_logger.error("UU task queue not initialized")
