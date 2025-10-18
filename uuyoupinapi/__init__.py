import json
import random
import string
import time

import requests

from utils.logger import PluginLogger
from uuyoupinapi import models

logger = PluginLogger("uuyoupinapi")


def generate_random_string(length):
    """
    Generate a random string with A–Z, a–z, and digits.
    :param length: target length
    :return: random string
    """
    letters_and_digits = string.ascii_letters + string.digits
    return "".join(random.choice(letters_and_digits) for i in range(length))


def generate_headers(devicetoken, deviceid, token=""):
    return {
        "uk": generate_random_string(65),
        "authorization": "Bearer " + token,
        "content-type": "application/json; charset=utf-8",
        "user-agent": "okhttp/3.14.9",
        "App-Version": "5.28.3",
        "AppType": "4",
        "deviceType": "1",
        "package-type": "uuyp",
        "DeviceToken": devicetoken,
        "DeviceId": deviceid,
        "platform": "android",
        "accept-encoding": "gzip",
        "Gameid": "730",
        "Device-Info": json.dumps(
            {
                "deviceId": deviceid,
                "deviceType": deviceid,
                "hasSteamApp": 1,
                "requestTag": generate_random_string(32).upper(),
                "systemName ": "Android",
                "systemVersion": "15",
            },
            ensure_ascii=False,
        ),
    }


def is_json(data):
    try:
        json.loads(data)
    except Exception:
        return False
    return True


class UUAccount:
    def __init__(self, token: str, deviceToken="", proxy=None):
        """
        :param token: token captured from network traffic
        """
        self.session = requests.Session()
        self.proxy = proxy
        if isinstance(proxy, dict):
            self.session.proxies = proxy
        elif isinstance(proxy, str):
            self.session.proxies = {
                "http": proxy,
                "https": proxy,
            }
        random.seed(token)
        self.deviceToken = deviceToken
        self.session.headers.update(generate_headers(deviceToken, deviceToken, token=token))
        try:
            info = self.call_api("GET", "/api/user/Account/getUserInfo").json()
            self.nickname = info["Data"]["NickName"]
            self.userId = info["Data"]["UserId"]
        except KeyError:
            raise Exception("UU Youpin login failed. Check that the token is correct.")

    @staticmethod
    def __random_str(length):
        return "".join(random.sample(string.ascii_letters + string.digits, length))

    @staticmethod
    def get_smsUpSignInConfig(headers):
        return requests.get(
            "https://api.youpin898.com/api/user/Auth/GetSmsUpSignInConfig",
            headers=headers,
        )

    @staticmethod
    def send_login_sms_code(phone, session: str, headers={}, region_code=86, uk=""):
        """
        Send login SMS code.
        :param phone: phone number
        :param session: can be obtained via UUAccount.get_random_session_id()
        """
        if uk:
            headers["uk"] = uk
        return requests.post(
            "https://api.youpin898.com/api/user/Auth/SendSignInSmsCode",
            json={"Area": region_code, "Mobile": phone, "Sessionid": session, "Code": ""},
            headers=headers,
        ).json()

    @staticmethod
    def sms_sign_in(phone, code, session, headers={}):
        """
        Log in with SMS code. Returns payload containing Token.
        :param phone: phone used to send code
        :param code: SMS code
        :param session: must match the session used when sending the code
        """
        if code == "":
            url = "https://api.youpin898.com/api/user/Auth/SmsUpSignIn"
        else:
            url = "https://api.youpin898.com/api/user/Auth/SmsSignIn"
        return requests.post(
            url,
            json={
                "Area": 86,
                "Code": code,
                "DeviceName": session,
                "Sessionid": session,
                "Mobile": phone,
            },
            headers=headers,
        ).json()

    def get_user_nickname(self):
        return self.nickname

    def send_device_info(self):
        return self.call_api(
            "GET",
            "/api/common/ClientInfo/AndroidInfo",
            data={
                "DeviceToken": self.deviceToken,
                "Sessionid": self.deviceToken,
            },
        )

    def call_api(self, method, path, data=None, uk_verify=False, pc_platform=False):
        """
        Call UU Youpin API.
        :param method: GET, POST, PUT, DELETE
        :param path: request path
        :param data: payload
        """
        url = "https://api.youpin898.com" + path
        if pc_platform:
            self.session.headers["platform"] = "pc"
        else:
            self.session.headers["platform"] = "android"

        if not uk_verify:
            if "uk" in self.session.headers:
                self.session.headers.pop("uk")
        else:
            try:
                from utils import cloud_service

                if not hasattr(self, "uk") or not hasattr(self, "uk_time") or (time.time() - self.uk_time > 30):
                    if not hasattr(self, "uk"):
                        logger.debug("No cached UK. Fetching verification parameter from cloud...")
                    else:
                        logger.debug("Cached UK expired or invalid. Fetching from cloud...")

                    fetched_uk = cloud_service.get_uu_uk_from_cloud()
                    if fetched_uk:
                        self.uk = fetched_uk
                        self.uk_time = time.time()
                        self.session.headers["uk"] = self.uk
                        logger.debug(
                            f'Fetched UK successfully. Cached. Next refresh: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.uk_time + 30))}'
                        )
                    else:
                        logger.error("Failed to fetch UK from cloud. Using a random UK for this request without caching.")
                        self.session.headers["uk"] = generate_random_string(65)
                else:
                    self.session.headers["uk"] = self.uk
                    logger.debug(
                        "Using cached UK. Next refresh: "
                        + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.uk_time + 30))
                    )

            except ImportError:
                logger.warning("Cloud service unavailable. Using a random UK.")
                self.session.headers["uk"] = generate_random_string(65)
            except Exception as e:
                logger.warning(f"Error fetching or handling UK: {e}. Using a random UK for this request without caching.")
                self.session.headers["uk"] = generate_random_string(65)

        if method == "GET":
            response = self.session.get(url, params=data)
        elif method == "POST":
            response = self.session.post(url, json=data)
        elif method == "PUT":
            response = self.session.put(url, json=data)
        elif method == "DELETE":
            response = self.session.delete(url)
        else:
            raise Exception("Method not supported")
        log_output = response.content.decode()
        if is_json(log_output):
            json_output = json.loads(log_output)
            log_output = json.dumps(json_output, ensure_ascii=False)
            logger.debug(f"{method} {path} {json.dumps(data)} {log_output}")

            if json_output.get("code") == 84101:
                raise Exception("Login state invalid. Please log in again.")
        elif response.status_code == 405:
            logger.error("UK token invalid. Waiting one minute before continuing.")
            time.sleep(60)
        else:
            logger.debug(f"{method} {path} {json.dumps(data)} {log_output}")
            raise Exception("Network error or request blocked by UU. Request failed.")

        return response

    def pre_change_lease_price_post(self, commodity_ids):
        self.call_api(
            "POST",
            "/api/youpin/bff/new/commodity/commodity/change/price/v3/init/info",
            data={
                "changePriceChannel": 0,
                "commodityIdList": [str(commodity_id) for commodity_id in commodity_ids],
                "gameId": "730",
                "Sessionid": self.deviceToken,
            },
        ).json()
        return

    def change_leased_price(self, items: list[models.LeaseAsset], compensation_type=0):
        """
        Request example:
        {
            "Commoditys": [{
                "CommodityId": 819157345,
                "IsCanLease": true,
                "IsCanSold": true,
                "LeaseDeposit": "20000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 222,
                "LongLeaseUnitPrice": 20,
                "Price": 10
            }],
            "Sessionid": "..."
        }
        Response example:
        {
            "Code": 0,
            "Msg": "Success",
            "TipType": 10,
            "Data": {
                "SuccessCount": 1,
                "FailCount": 0,
                "Commoditys": [{
                    "CommodityId": 814953269,
                    "IsSuccess": 1,
                    "Message": null
                }]
            }
        }
        """
        item_infos = list()
        commodity_ids = list()
        for item in items:
            item_info = {
                "CommodityId": int(item.orderNo),  # type: ignore
                "IsCanLease": item.IsCanLease,
                "IsCanSold": item.IsCanSold,
                "LeaseDeposit": item.LeaseDeposit,
                "LeaseMaxDays": item.LeaseMaxDays,
                "LeaseUnitPrice": item.LeaseUnitPrice,
                "CompensationType": compensation_type,
            }
            if item.LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = item.LongLeaseUnitPrice
            if item_info["IsCanSold"]:
                item_info["Price"] = item.price
            commodity_ids.append(item_info["CommodityId"])
            item_infos.append(item_info)
        self.pre_change_lease_price_post(commodity_ids)
        rsp = self.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={
                "Commoditys": item_infos,
                "Sessionid": self.deviceToken,
            },
        ).json()
        if rsp["Data"]["FailCount"] != 0:
            for commodity in rsp["Data"]["Commoditys"]:
                if commodity["IsSuccess"] != 1:
                    logger.error(f"Failed to change price. CommodityId: {commodity[id]}, reason: {commodity['Message']}")
        return rsp["Data"]["SuccessCount"]

    def send_offer(self, orderNo):
        rsp = self.call_api(
            "PUT",
            "/api/youpin/bff/trade/v1/order/sell/delivery/send-offer",
            data={"orderNo": orderNo, "Sessionid": self.deviceToken},
        ).json()
        if rsp["code"] == 0:
            return True
        else:
            return rsp["msg"]

    def get_offer_status(self, orderNo):
        rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/v1/order/sell/delivery/get-offer-status",
            data={"orderNo": orderNo, "Sessionid": self.deviceToken},
        ).json()
        if rsp["code"] == 0:
            return rsp
        else:
            return rsp["msg"]

    def get_wait_deliver_list(self, game_id=730, return_offer_id=True):
        """
        Get pending-delivery list.
        :param return_offer_id: default True. Whether to return Steam trade offer id.
        :param game_id: default 730
        :return: list of dicts like [{'offer_id': '...', 'item_name': '...'}, ...]
        """
        toDoList_response = self.call_api(
            "POST",
            "/api/youpin/bff/trade/todo/v1/orderTodo/list",
            data={
                "userId": self.userId,
                "pageIndex": 1,
                "pageSize": 100,
                "Sessionid": self.deviceToken,
            },
        ).json()
        toDoList = dict()
        for order in toDoList_response["data"]:
            if "赠送" in order["message"]:
                logger.warning(
                    f"[UUAutoAcceptOffer] Order {order['orderNo']} ({order['commodityName']}) is a gift order. Gift orders are not supported."
                )
            elif order["message"] == "有买家下单，待您发送报价":
                logger.info(
                    f"[UUAutoAcceptOffer] Order {order['orderNo']} ({order['commodityName']}) requires sending an offer. Sending..."
                )
                result = self.send_offer(order["orderNo"])
                if result is True:
                    logger.info(
                        f"[UUAutoAcceptOffer] Offer for order {order['orderNo']} ({order['commodityName']}) is being sent. Waiting..."
                    )
                    for i in range(5):
                        result = self.get_offer_status(order["orderNo"])
                        if result["data"]["status"] == 3:
                            logger.info(
                                f"[UUAutoAcceptOffer] Offer for order {order['orderNo']} ({order['commodityName']}) sent. Token confirmation will occur next poll."
                            )
                            break
                        if i == 4:
                            logger.warning(
                                f"[UUAutoAcceptOffer] Offer send wait timeout for order {order['orderNo']} ({order['commodityName']})"
                            )
                            break
                        time.sleep(1.5)
                else:
                    logger.error(
                        f"[UUAutoAcceptOffer] Failed to send offer for order {order['orderNo']} ({order['commodityName']}). Reason: {result}"
                    )
            else:
                toDoList[order["orderNo"]] = order
        data_to_return = []
        # There are three possible ways the platform exposes an offerId
        if len(toDoList.keys()) != 0:
            data = self.call_api(
                "POST",
                "/api/youpin/bff/trade/sale/v1/sell/list",
                data={
                    "keys": "",
                    "orderStatus": "140",
                    "pageIndex": 1,
                    "pageSize": 100,
                },
            ).json()["data"]
            for order in data["orderList"]:
                if int(order["offerType"]) == 2:
                    if order["tradeOfferId"] is not None:
                        if order["orderNo"] in toDoList.keys():
                            del toDoList[order["orderNo"]]
                        data_to_return.append(
                            {
                                "offer_id": order["tradeOfferId"],
                                "item_name": order["productDetail"]["commodityName"],
                            }
                        )
        if len(toDoList.keys()) != 0:
            for order in list(toDoList.keys()):
                time.sleep(3)
                orderDetail = self.call_api(
                    "POST",
                    "/api/youpin/bff/order/v2/detail",
                    data={
                        "orderId": order,
                        "Sessionid": self.deviceToken,
                    },
                ).json()
                if orderDetail["data"] and "orderDetail" in orderDetail["data"]:
                    orderDetail = orderDetail["data"]["orderDetail"]
                    if "offerId" in orderDetail:
                        data_to_return.append(
                            {
                                "offer_id": orderDetail["offerId"],
                                "item_name": orderDetail["productDetail"]["commodityName"],
                            }
                        )
                        if order in toDoList.keys():
                            del toDoList[order]
        if len(toDoList.keys()) != 0:
            for order in list(toDoList.keys()):
                time.sleep(3)
                orderDetail = self.call_api(
                    "POST",
                    "/api/youpin/bff/trade/v1/order/query/detail",
                    data={
                        "orderNo": order,
                        "Sessionid": self.deviceToken,
                    },
                ).json()
                orderDetail = orderDetail["data"]
                if orderDetail and "tradeOfferId" in orderDetail and "系统验证中" not in str(orderDetail):
                    data_to_return.append(
                        {
                            "offer_id": orderDetail["tradeOfferId"],
                            "item_name": orderDetail["commodity"]["name"],
                        }
                    )
                    if order in toDoList.keys():
                        del toDoList[order]
        if len(toDoList.keys()) != 0:
            logger.warning(
                "[UUAutoAcceptOffer] Some orders did not return a Steam trade offer id. OrderNos: " + str(toDoList.keys()),
            )
        return data_to_return

    def get_sell_list(self):
        data = {"pageIndex": 0, "pageSize": 100, "whetherMerge": 0}
        shelf = list()
        while True:
            data["pageIndex"] += 1
            response = self.call_api("POST", "/api/youpin/bff/new/commodity/v1/commodity/list/sell", data=data)
            if response.json()["code"] != 0:
                break
            else:
                for item in response.json()["data"]["commodityInfoList"]:
                    if "steamAssetId" in item:
                        shelf.append(item)
        return shelf

    def put_items_on_lease_shelf(self, item_infos: list[models.UUOnLeaseShelfItem], GameId=730):
        """
        Request example:
        {
            "AppType": 3,
            "AppVersion": "5.20.1",
            "GameId": 730,
            "ItemInfos": [{
                "AssetId": 38872746818,
                "IsCanLease": true,
                "IsCanSold": false,
                "LeaseDeposit": "30000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 10,
                "LongLeaseUnitPrice": 10
            }],
            "Sessionid": "..."
        }
        Response example:
        {
            "Code": 0,
            "Msg": "Success",
            "TipType": 10,
            "Data": [{
                "AssetId": 38872746818,
                "CommodityId": 814835547,
                "CommodityNo": "...",
                "Status": 1,
                "Remark": ""
            }]
        }
        """
        lease_on_shelf_rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameId": GameId,
                "itemInfos": [item.model_dump(exclude_none=True) for item in item_infos],
                "Sessionid": self.deviceToken,
            },
        ).json()
        success_count = 0
        for asset in lease_on_shelf_rsp["Data"]:
            if asset["Status"] == 1:
                success_count += 1
            else:
                logger.error(f"Failed to list item {asset['AssetId']}(AssetId). Reason: {asset['Remark']}")
        return success_count

    def get_uu_leased_inventory(self, pageIndex=1, pageSize=100) -> list[models.LeaseAsset]:
        new_leased_inventory_list = self.get_one_channel_leased_inventory(
            "/api/youpin/bff/new/commodity/v1/commodity/list/lease", pageIndex, pageSize
        )
        zero_leased_inventory_list = self.get_one_channel_leased_inventory(
            "/api/youpin/bff/new/commodity/v1/commodity/list/zeroCDLease", pageIndex, pageSize
        )
        return new_leased_inventory_list + zero_leased_inventory_list

    def get_one_channel_leased_inventory(self, path, pageIndex=1, pageSize=100) -> list[models.LeaseAsset]:
        rsp = self.call_api(
            "POST",
            path,
            data={
                "pageIndex": pageIndex,
                "pageSize": pageSize,
                "whetherMerge": 0,
                "Sessionid": self.deviceToken,
            },
        ).json()
        leased_inventory_list = []
        if rsp["code"] == 0:
            for item in rsp["data"]["commodityInfoList"]:
                leased_inventory_list.append(
                    models.LeaseAsset(
                        assetid=str(item["steamAssetId"]),
                        templateid=item["templateId"],
                        short_name=item["name"],
                        LeaseDeposit=float(item["depositAmount"]),
                        LeaseUnitPrice=float(item["shortLeaseAmount"]),
                        LongLeaseUnitPrice=float(item["longLeaseAmount"]) if item["longLeaseAmount"] else float(0),
                        LeaseMaxDays=item["leaseMaxDays"],
                        IsCanSold=bool(item["commodityCanSell"]),
                        IsCanLease=bool(item["commodityCanLease"]),
                        orderNo=item["id"],
                        price=float(item["referencePrice"][1:]),
                    )
                )
        elif rsp["code"] == 9004001:
            pass
        else:
            raise Exception("Failed to fetch UU leased shelf items.")
        return leased_inventory_list

    def get_inventory(self, refresh=False):
        data_to_send = {
            "pageIndex": 1,
            "pageSize": 1000,
            "AppType": 4,
            "IsMerge": 0,
            "Sessionid": self.deviceToken,
        }
        if refresh:
            data_to_send["IsRefresh"] = True
            data_to_send["RefreshType"] = 2
        inventory_list_rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/GetUserInventoryDataListV3",
            data=data_to_send,
        ).json()
        inventory_list = []
        if inventory_list_rsp["Code"] == 0:  # Inconsistent casing from UU. Sometimes "Code" vs "code".
            inventory_list = inventory_list_rsp["Data"]["ItemsInfos"]
            logger.info(f"Inventory count {len(inventory_list)}")
        else:
            logger.error(inventory_list_rsp)
            logger.error("Failed to fetch UU inventory.")
        return inventory_list

    def get_market_lease_price(
        self, template_id: int, min_price=0, max_price=20000, cnt=15, sortTypeKey="LEASE_DEFAULT"
    ) -> list[models.UUMarketLeaseItem]:
        rsp = self.call_api(
            "POST",
            "/api/homepage/v3/detail/commodity/list/lease",
            data={
                "hasLease": "true",
                "haveBuZhangType": 0,
                "listSortType": "2",
                "listType": 30,
                "mergeFlag": 0,
                "pageIndex": 1,
                "pageSize": 50,
                "sortType": "1",
                "sortTypeKey": sortTypeKey,
                "status": "20",
                "stickerAbrade": 0,
                "stickersIsSort": False,
                "templateId": f"{template_id}",
                "ultraLongLeaseMoreZones": 0,
                "userId": self.userId,
                "Sessionid": self.deviceToken,
            },
        ).json()
        lease_list = []
        if rsp["Code"] == 0:
            rsp_list = rsp["Data"]["CommodityList"]
            rsp_cnt = len(rsp_list)
            cnt = min(cnt, rsp_cnt)
            for i in range(cnt):
                item = rsp_list[i]
                if item["LeaseDeposit"] and min_price < float(item["LeaseDeposit"]) < max_price:
                    lease_list.append(
                        models.UUMarketLeaseItem(
                            LeaseUnitPrice=item["LeaseUnitPrice"] if item["LeaseUnitPrice"] else None,
                            LongLeaseUnitPrice=item["LongLeaseUnitPrice"] if item["LongLeaseUnitPrice"] else None,
                            LeaseDeposit=item["LeaseDeposit"] if item["LeaseDeposit"] else None,
                            CommodityName=item["CommodityName"],
                        )
                    )
        else:
            logger.error(f"Failed to query lease price. Code: {rsp['Code']}. Full response: {rsp}")
        return lease_list

    def get_market_sale_list_with_abrade(
        self, template_id: int, pageIndex: int = 1, pageSize: int = 10, minAbrade: float | None = None, maxAbrade: float | None = None
    ):
        """
        Get market selling items for a template id with optional float range filter.
        """
        data = {
            "pageIndex": pageIndex,
            "pageSize": pageSize,
            "templateId": str(template_id),
        }
        if minAbrade is not None:
            data["minAbrade"] = str(minAbrade)
        if maxAbrade is not None:
            data["maxAbrade"] = str(maxAbrade)

        return self.call_api(
            "POST",
            "/api/homepage/pc/goods/market/queryOnSaleCommodityList",
            data=data,
            uk_verify=True,
            pc_platform=True,
        )

    def off_shelf(self, commodity_ids: list):
        # Works for both sale and lease items
        return self.call_api(
            "PUT",
            "/api/commodity/Commodity/OffShelf",
            data={
                "Ids": ",".join([str(id) for id in commodity_ids]),
                "IsDeleteCommodityCache": 1,
                "IsForceOffline": True,
            },
        )

    def sell_items(self, assets: dict, remark=None):
        item_infos = [{"AssetId": asset, "Price": assets[asset], "Remark": remark} for asset in assets.keys()]
        rsp = self.call_api(
            "POST",
            "/api/commodity/Inventory/SellInventoryWithLeaseV2",
            data={
                "GameID": 730,
                "ItemInfos": item_infos,
            },
        ).json()
        success_count = 0
        for commodity in rsp["Data"]:
            if commodity["Status"] != 1:
                if "不能重复上架" not in commodity["Remark"]:
                    logger.error(f"Failed to list {commodity['AssetId']}. Reason: {commodity['Remark']}")
                else:
                    logger.warning("Likely double-list due to server delay at UU.")
            else:
                success_count += 1
        return success_count

    def change_price(self, assets: dict):
        item_infos = [
            {"CommodityId": int(asset), "Price": str(assets[asset]), "Remark": None, "IsCanSold": True}
            for asset in assets.keys()
        ]
        return self.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={"Commoditys": item_infos},
        )

    def change_items_price_v2(self, items: list[dict]):
        """
        Change prices for multiple listed items (supports both sale and lease fields).
        API: /api/commodity/Commodity/PriceChangeWithLeaseV2
        :param items: list of dicts matching Commoditys structure.
        """
        return self.call_api(
            "PUT",
            "/api/commodity/Commodity/PriceChangeWithLeaseV2",
            data={
                "Commoditys": items,
                "Sessionid": self.deviceToken,  # align with change_leased_price
            },
        )

    def onshelf_sell_and_lease(self, sell_assets: list[models.Asset] = [], lease_assets: list[models.LeaseAsset] = []):
        """
        List items for sale and/or lease in one call.
        """
        item_infos = []
        sell_assets_dict = dict({asset.assetid: asset for asset in sell_assets})
        lease_assets_dict = dict({asset.assetid: asset for asset in lease_assets})
        sell_lease_assets_id = set(sell_assets_dict.keys()) & set(lease_assets_dict.keys())
        # Merge if both provided
        for asset_id in sell_lease_assets_id:
            item_info = {
                "AssetId": asset_id,
                "IsCanLease": True,
                "IsCanSold": True,
                "LeaseDeposit": str(lease_assets_dict[asset_id].LeaseDeposit),
                "LeaseMaxDays": lease_assets_dict[asset_id].LeaseMaxDays,
                "LeaseUnitPrice": lease_assets_dict[asset_id].LeaseUnitPrice,
                "Price": sell_assets_dict[asset_id].price,
            }
            if lease_assets_dict[asset_id].LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = lease_assets_dict[asset_id].LongLeaseUnitPrice
            item_infos.append(item_info)
            del sell_assets_dict[asset_id]
            del lease_assets_dict[asset_id]

        item_infos += [models.UUOnSellShelfItem.fromAsset(asset).model_dump(exclude_none=True) for asset in sell_assets_dict.values()]
        item_infos += [models.UUOnLeaseShelfItem.fromLeaseAsset(asset).model_dump(exclude_none=True) for asset in lease_assets_dict.values()]

        batches = [item_infos[i : i + 50] for i in range(0, len(item_infos), 50)]
        change_price_onshelf_list = []
        success_count = 0
        for batch in batches:
            rsp = self.call_api(
                "POST",
                "/api/commodity/Inventory/SellInventoryWithLeaseV2",
                data={
                    "GameId": 730,
                    "ItemInfos": batch,
                    "Sessionid": self.deviceToken,
                },
            ).json()
            if not rsp.get("Data"):
                logger.error(f"Failed to list {len(batch)} item(s). Response: {rsp}")
                if "Steam服务异常" in rsp.get("Msg", ""):
                    logger.warning("Steam service exception detected. Refreshing inventory...")
                    self.get_inventory(refresh=True)
                    logger.info("Inventory refreshed. Waiting 5 seconds and retrying.")
                    time.sleep(5)
                continue
            for asset in rsp["Data"]:
                if asset["Status"] == 1:
                    success_count += 1
                else:
                    if "不能重复上架" in asset["Remark"]:
                        logger.warning(
                            f"Item {asset['AssetId']} might already be listed for lease/sale. Will try listing via price-change."
                        )
                        for item in batch:
                            if item["AssetId"] == asset["AssetId"]:
                                change_price_onshelf_list.append(item)
                                break
                    else:
                        logger.error(f"Failed to list {asset['AssetId']}. Reason: {asset['Remark']}")
        if change_price_onshelf_list:
            logger.info(f"Listing {len(change_price_onshelf_list)} item(s) via price-change workflow")
            sell_shelf = self.get_sell_list()
            lease_shelf = self.get_uu_leased_inventory()
            for asset in change_price_onshelf_list:
                if asset["IsCanSold"]:
                    for lease_asset in lease_shelf:
                        if asset["AssetId"] == int(lease_asset.assetid):
                            asset["CommodityId"] = lease_asset.orderNo
                            asset["LeaseDeposit"] = str(lease_asset.LeaseDeposit)
                            asset["LeaseMaxDays"] = lease_asset.LeaseMaxDays
                            asset["LeaseUnitPrice"] = lease_asset.LeaseUnitPrice
                            if lease_asset.LongLeaseUnitPrice:
                                asset["LongLeaseUnitPrice"] = lease_asset.LongLeaseUnitPrice
                            asset["IsCanLease"] = True
                            del asset["AssetId"]
                            break
                elif asset["IsCanLease"]:
                    for sell_asset in sell_shelf:
                        if asset["AssetId"] == int(sell_asset["steamAssetId"]):
                            asset["CommodityId"] = sell_asset["id"]
                            asset["Price"] = sell_asset["price"]
                            asset["IsCanSold"] = True
                            del asset["AssetId"]
                            break
            batches = [change_price_onshelf_list[i : i + 50] for i in range(0, len(change_price_onshelf_list), 50)]
            for batch in batches:
                rsp = self.call_api(
                    "PUT",
                    "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                    data={
                        "Commoditys": batch,
                        "Sessionid": self.deviceToken,
                    },
                ).json()
                try:
                    for asset in rsp["Data"]["Commoditys"]:
                        if asset["IsSuccess"] == 1:
                            success_count += 1
                        else:
                            logger.error(
                                f"Failed to list via price-change. CommodityId {asset['CommodityId']}. Reason: {asset['Remark']}"
                            )
                except TypeError:
                    logger.error("Failed to list via price-change. Item may be in pending-delivery list.")
        failure_count = len(item_infos) - success_count
        return success_count, failure_count

    def change_price_sell_and_lease(self, sell_assets: list[models.Asset] = [], lease_assets: list[models.LeaseAsset] = []):
        """
        Change price for items that may be both sellable and leasable.
        Request example:
        {
            "Commoditys": [{
                "CommodityId": 819475347,
                "IsCanLease": true,
                "IsCanSold": true,
                "LeaseDeposit": "100000.0",
                "LeaseMaxDays": 30,
                "LeaseUnitPrice": 100,
                "LongLeaseUnitPrice": 50,
                "Price": 90
            }],
            "Sessionid": "..."
        }
        """
        item_infos = []
        sell_assets_dict = dict({asset.assetid: asset for asset in sell_assets})
        lease_assets_dict = dict({asset.assetid: asset for asset in lease_assets})
        sell_lease_commodityID = set(sell_assets_dict.keys()) & set(lease_assets_dict.keys())
        # Merge if both provided
        for id in sell_lease_commodityID:
            item_info = {
                "CommodityId": id,
                "IsCanLease": True,
                "IsCanSold": True,
                "LeaseDeposit": str(lease_assets_dict[id].LeaseDeposit),
                "LeaseMaxDays": lease_assets_dict[id].LeaseMaxDays,
                "LeaseUnitPrice": lease_assets_dict[id].LeaseUnitPrice,
                "Price": sell_assets_dict[id].price,
            }
            if lease_assets_dict[id].LongLeaseUnitPrice:
                item_info["LongLeaseUnitPrice"] = lease_assets_dict[id].LongLeaseUnitPrice
            item_infos.append(item_info)
            del sell_assets_dict[id]
            del lease_assets_dict[id]

        item_infos += [models.UUChangePriceItem.fromAsset(asset).model_dump(exclude_none=True) for asset in sell_assets_dict.values()]
        item_infos += [models.UUChangePriceItem.fromLeaseAsset(asset).model_dump(exclude_none=True) for asset in lease_assets_dict.values()]

        batches = [item_infos[i : i + 50] for i in range(0, len(item_infos), 50)]
        success_count = 0
        for batch in batches:
            rsp = self.call_api(
                "PUT",
                "/api/commodity/Commodity/PriceChangeWithLeaseV2",
                data={
                    "Commoditys": batch,
                    "Sessionid": self.deviceToken,
                },
            ).json()
            for asset in rsp["Data"]["Commoditys"]:
                if asset["IsSuccess"] == 1:
                    success_count += 1
                else:
                    reason = "Unknown"
                    if asset.get("Remark"):
                        reason = asset["Remark"]
                    elif asset.get("Message"):
                        reason = asset["Message"]
                    logger.error(f"Failed to change price. CommodityId {asset['CommodityId']}. Reason: {reason}")
        failure_count = len(item_infos) - success_count
        return success_count, failure_count

    def get_leased_out_list(self):
        data = {"gameId": 730, "pageIndex": 0, "pageSize": 50, "sortType": 0, "keywords": ""}
        result = []
        while True:
            data["pageIndex"] += 1
            response = self.call_api("POST", "/api/youpin/bff/trade/v1/order/lease/out/list", data=data).json()
            result += response["data"]["orderDataList"]
            if len(response["data"]["orderDataList"]) < 50:
                break
        return result

    def get_template_id_by_order_id(self, order_id):
        response = self.call_api("POST", "/api/youpin/bff/order/v2/detail", data={"orderId": order_id}).json()
        return response["data"]["orderDetail"]["productDetail"]["commodityTemplateId"]

    def get_least_market_price(self, template_id):
        response = self.call_api("POST", "/api/homepage/v2/detail/commodity/list/sell", data={"templateId": template_id}).json()
        if response["Code"] == 84104:
            raise SystemError("UU risk control. Price temporarily unavailable.")
        try:
            return response["Data"]["CommodityList"][0]["Price"]
        except:
            return 0

    def get_trend_inventory(self):
        inventory_list_rsp = self.call_api(
            "POST",
            "/api/youpin/commodity/user/inventory/price/trend",
            data={"pageIndex": 1, "pageSize": 1000, "IsMerge": 0},
        ).json()
        inventory_list = []
        if inventory_list_rsp["code"] == 0:
            inventory_list = inventory_list_rsp["data"]["itemsInfos"]
            logger.info(f"Inventory count {len(inventory_list)}")
        else:
            logger.error(inventory_list_rsp)
            logger.error("Failed to fetch UU inventory.")
        return inventory_list

    def save_buy_price(self, assets: list):
        """
        {"productUniqueKeyList":[{"steamAssetId":"39605491748","marketHashName":"USP-S | Printstream (Minimal Wear)",
        "buyPrice":"341","abrade":"0.1401326358318328900"}]}
        """
        item_infos = [
            {
                "steamAssetId": str(asset["steamAssetId"]),
                "marketHashName": asset["marketHashName"],
                "buyPrice": str(asset["buyPrice"]),
                "abrade": str(asset["abrade"]),
            }
            for asset in assets
        ]
        rsp = self.call_api(
            "POST",
            "/api/youpin/commodity/product/user/batch/save/buy/price",
            data={"productUniqueKeyList": item_infos},
        ).json()
        if "code" in rsp and rsp["code"] == 0:
            logger.info("Saved purchase price successfully.")
        else:
            logger.error(f"Failed to save purchase price. Reason: {rsp}")

    def get_buy_order(self, pageIndex=1):
        buy_order_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/sale/v1/buy/list",
            data={
                "keys": "",
                "orderStatus": 340,
                "pageIndex": pageIndex,
                "pageSize": 20,
                "presenterId": 0,
                "sceneType": 0,
                "Sessionid": self.deviceToken,
            },
        ).json()
        buy_price = []
        if buy_order_rsp["code"] == 0:
            order_list = buy_order_rsp["data"]["orderList"]
            for order in order_list:
                if not order["orderStatusName"] == "已完成":
                    continue
                product_detail_list = order["productDetailList"]
                if order["commodityNum"] <= 3:
                    for product in product_detail_list:
                        buy_price.append(
                            {
                                "order_id": order["orderId"],
                                "abrade": product["abrade"][:11],
                                "buy_asset_id": product["assertId"] if product["assertId"] is not None else product["commodityId"],
                                "buy_price": product["price"] / 100,
                                "name": product["commodityName"],
                                "order_time": int(order["finishOrderTime"]),
                                "type_name": product["typeName"],
                                "buy_from": "uu",
                            }
                        )
                else:
                    buy_price.extend(self.get_buy_batch_order(order["id"], order["buyerUserId"]))
                    time.sleep(10)
            logger.info(f"Fetched purchase orders. Count: {len(buy_price)}")
        else:
            logger.error(f"Failed to fetch purchase orders. Reason: {buy_order_rsp}")

        return buy_price

    def get_buy_batch_order(self, orderNo, userId):
        buy_batch_order_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/v1/order/query/detail",
            data={
                "orderNo": str(orderNo),
                "userId": userId,
                "Sessionid": self.deviceToken,
            },
        ).json()
        buy_price = []
        if buy_batch_order_rsp["code"] == 0:
            data = buy_batch_order_rsp["data"]
            for commodity in data["userCommodityVOList"][0]["commodityVOList"]:
                buy_price.append(
                    {
                        "order_id": orderNo,
                        "abrade": commodity["abrade"][:11],
                        "buy_asset_id": commodity["id"],
                        "buy_price": float(commodity["price"]),
                        "name": commodity["name"],
                        "order_time": int(data["orderCanceledTime"]),
                        "buy_from": "uu",
                    }
                )

            logger.info(f"Fetched batch purchase order. Count: {len(buy_price)}")
        else:
            logger.error(f"Failed to fetch batch purchase order. Reason: {buy_batch_order_rsp}. OrderNo: {orderNo}")

        return buy_price

    def get_zero_cd_list(self, pageIndex=1, pageSize=20):
        zero_cd_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/trade/v1/order/lease/sublet/canEnable/list",
            data={
                "pageIndex": pageIndex,
                "pageSize": pageSize,
            },
        ).json()
        zero_cd_valid_list = []
        if zero_cd_rsp["code"] == 0:
            zero_cd_valid_list = zero_cd_rsp["data"]["orderDataList"]
        return zero_cd_valid_list

    def enable_zero_cd(self, orders_list):
        enable_zero_cd_rsp = self.call_api(
            "POST",
            "/api/youpin/bff/order/sublet/open",
            data={"orderIdList": orders_list, "subletConfig": {"subletSwitchFlag": 1, "subletPricingFlag": 1, "pricingMinPercent": "95"}},
        ).json()

        if enable_zero_cd_rsp["code"] == 0:
            logger.info("Enabled 0cd sublet successfully.")
        else:
            logger.error(f"Failed to enable 0cd sublet. Reason: {enable_zero_cd_rsp}")

    def publish_purchase_order(self, templateId, templateHashName, commodityName, purchasePrice, purchaseNum, orderNo="", supplyQuantity=0):
        data = {
            "templateId": templateId,
            "templateHashName": templateHashName,
            "commodityName": commodityName,
            "purchasePrice": purchasePrice,
            "purchaseNum": purchaseNum,
            "needPaymentAmount": round(purchaseNum * purchasePrice, 2),
            "totalAmount": round(purchaseNum * purchasePrice, 2),
            "incrementServiceCode": [1001],
            "priceDifference": 0,
            "discountAmount": 0,
            "payConfirmFlag": False,
            "repeatOrderCancelFlag": False,
        }
        url = "/api/youpin/bff/trade/purchase/order/savePurchaseOrder"
        if orderNo:
            data["orderNo"] = orderNo
            url = "/api/youpin/bff/trade/purchase/order/updatePurchaseOrder"
            data["templateName"] = commodityName
            data["supplyQuantity"] = supplyQuantity
        response = self.call_api(
            "POST",
            url,
            data=data,
        )
        return response

    def get_template_purchase_order(self, templateId, pageIndex=1, pageSize=30, minAbrade=0, maxAbrade=1, typeId=-1):
        response = self.call_api(
            "POST",
            "/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderList",
            data={
                "templateId": templateId,
                "pageIndex": pageIndex,
                "pageSize": pageSize,
                "minAbrade": minAbrade,
                "maxAbrade": maxAbrade,
                "typeId": typeId,
            },
        )
        return response

    def get_template_purchase_order_pc(self, templateId, pageIndex=1, pageSize=30, minAbrade=0, maxAbrade=1, typeId=-1):
        response = self.call_api(
            "POST",
            "/api/youpin/bff/trade/purchase/order/getTemplatePurchaseOrderListPC",
            data={
                "templateId": templateId,
                "pageIndex": pageIndex,
                "pageSize": pageSize,
                "minAbrade": minAbrade,
                "maxAbrade": maxAbrade,
                "typeId": typeId,
            },
            uk_verify=True,
            pc_platform=True,
        )
        return response

    def search_purchase_order_list(self, pageIndex=1, pageSize=40, status=20):
        response = self.call_api(
            "POST",
            "/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList",
            data={"pageIndex": pageIndex, "pageSize": pageSize, "status": status},
        )
        return response

    def get_full_purchase_order_list(self, status=20):
        index = 1
        purchase_order_list = []
        while True:
            if index != 1:
                time.sleep(2.5)
            response = self.search_purchase_order_list(index, 40, status)
            response.raise_for_status()
            data = response.json()
            if "成功" in data["msg"]:
                purchase_order_list += data["data"]
            if len(data["data"]) < 40:
                break
            index += 1
        return purchase_order_list
