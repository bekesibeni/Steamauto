#
#   ____         __  __                  _
#  |  _ \       / _|/ _|     /\         (_)
#  | |_) |_   _| |_| |_     /  \   _ __  _
#  |  _ <| | | |  _|  _|   / /\ \ | '_ \| |
#  | |_) | |_| | | | |    / ____ \| |_) | |
#  |____/ \__,_|_| |_|   /_/    \_\ .__/|_|
#                                 | |
#                                 |_|
# Buff-Api By jiajiaxd(https://github.com/jiajiaxd)
# Please use this API in compliance with the GPL-3.0 license.
# For learning and communication purposes only. Users are responsible for all consequences!

import copy
import json
import random
import time
from typing import no_type_check, Dict, List, Union

import requests

from utils.logger import PluginLogger
from BuffApi import models

logger = PluginLogger("BuffApi")

def get_ua():
    first_num = random.randint(55, 62)
    third_num = random.randint(0, 3200)
    fourth_num = random.randint(0, 140)
    os_type = [
        "(Windows NT 6.1; WOW64)",
        "(Windows NT 10.0; WOW64)",
        "(X11; Linux x86_64)",
        "(Macintosh; Intel Mac OS X 10_12_6)",
    ]
    chrome_version = f"Chrome/{first_num}.0.{third_num}.{fourth_num}"

    ua = " ".join(
        [
            "Mozilla/5.0",
            random.choice(os_type),
            "AppleWebKit/537.36",
            "(KHTML, like Gecko)",
            chrome_version,
            "Safari/537.36",
        ]
    )
    return ua

def get_random_header() -> dict:
    return {"User-Agent": get_ua()}

class BuffAccount:
    """
    Supports custom User-Agent
    Parameter is Buff cookie
    Reference format:
    session=*******
    If error occurs, it's likely because you've been detected by BUFF's anti-crawler mechanism, please try multiple times!

    Note:
    Each item's each wear (quality) in Buff has an independent goods_id, and each item has an independent id
    """
    
    BASE_URL = "https://buff.163.com"

    def __init__(self, buffcookie, user_agent=get_ua()):
        self.session = requests.session()
        self.session.headers = {"User-Agent": user_agent}
        headers = copy.deepcopy(self.session.headers)
        headers["Cookie"] = buffcookie
        self.get_notification(headers=headers)

    def get(self, url, **kwargs):
        response = self.session.get(url, **kwargs)
        logger.debug(f"GET {url} {response.status_code} {json.dumps(response.json(),ensure_ascii=False)}")
        return response

    def post(self, url, **kwargs):
        response = self.session.post(url, **kwargs)
        logger.debug(f"POST {url} {response.status_code} {json.dumps(response.json(),ensure_ascii=False)}")
        return response

    def get_user_nickname(self) -> str:
        """
        :return: str
        """
        try:
            user_info = self.get_user_info()
            if user_info and "nickname" in user_info:
                self.username = user_info["nickname"]
                return self.username
        except AttributeError:
            raise ValueError("Buff login failed! Please try again later or check if the cookie is filled correctly.")
        return ""

    def get_user_info(self) -> Dict:
        """Get user information, including SteamID and other data"""
        response = self.get(f"{self.BASE_URL}/account/api/user/info")
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data:
                return data["data"]
        return {}

    def set_force_buyer_send_offer(self) -> bool:
        """Set to only allow buyers to initiate trade offers"""
        headers = self.CSRF_Fucker()
        headers["Referer"] = f"{self.BASE_URL}/user-center/profile"
        data = {"force_buyer_send_offer": "true"}
        
        resp = self.post(
            f"{self.BASE_URL}/account/api/prefer/force_buyer_send_offer",
            json=data,
            headers=headers
        )
        
        if resp.status_code == 200 and resp.json()["code"] == "OK":
            return True
        return False

    def get_sell_order_to_deliver(self, game: str, appid: Union[str, int]) -> Dict:
        """Get orders waiting for delivery"""
        params = {
            "game": game,
            "appid": str(appid)
        }
        response = self.get(f"{self.BASE_URL}/api/market/sell_order/to_deliver", params=params)
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data:
                return data["data"]
        return {}

    def get_sell_order_history(self, appid: Union[str, int]) -> List:
        """Get sales history records"""
        params = {
            "appid": str(appid),
            "mode": "1"
        }
        response = self.get(f"{self.BASE_URL}/api/market/sell_order/history", params=params)
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data and "items" in data["data"]:
                return data["data"]["items"]
        return []

    def get_user_brief_assest(self) -> dict:
        """
        Contains user balance and other information
        :return: dict
        """
        return json.loads(self.get(f"{self.BASE_URL}/api/asset/get_brief_asset").text).get("data")

    def search_goods(self, key: str, game_name="csgo") -> list:
        return (
            json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/search/suggest",
                    params={"text": key, "game": game_name},
                ).text
            )
            .get("data")
            .get("suggestions")
        )

    def get_sell_order(self, goods_id, page_num=1, game_name="csgo", sort_by="default", proxy=None, min_paintseed=None, max_paintseed=None) -> dict:
        """
        Get on-sale items for specified skins
        :return: dict
        """
        params = {
            "game": game_name,
            "goods_id": goods_id,
            "page_num": page_num,
            "sort_by": sort_by,
        }
        need_login = False
        if min_paintseed:
            params["min_paintseed"] = min_paintseed
            need_login = True
        if max_paintseed:
            params["max_paintseed"] = max_paintseed
            need_login = True
        if sort_by != "default":
            need_login = True
        if need_login:
            return json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/goods/sell_order",
                    params=params,
                    headers=get_random_header(),
                    proxies=proxy,
                ).text
            ).get("data")
        else:
            return json.loads(
                requests.get(
                    f"{self.BASE_URL}/api/market/goods/sell_order",
                    params=params,
                    headers=get_random_header(),
                    proxies=proxy,
                ).text
            ).get("data")

    def get_available_payment_methods(self, sell_order_id, goods_id, price, game_name="csgo") -> dict:
        """
        :param game_name: Default is csgo
        :param sell_order_id:
        :param goods_id:
        :param price: Skin price
        :return: dict key will only contain buff-alipay and buff-bankcard, if key doesn't exist, it means this payment method is unavailable. value is current balance
        """

        methods = (
            json.loads(
                self.get(
                    f"{self.BASE_URL}/api/market/goods/buy/preview",
                    params={
                        "game": game_name,
                        "sell_order_id": sell_order_id,
                        "goods_id": goods_id,
                        "price": price,
                    },
                ).text
            )
            .get("data")
            .get("pay_methods")
        )
        available_methods = dict()
        if methods[0].get("error") is None:
            available_methods["buff-alipay"] = methods[0].get("balance")
        if methods[2].get("error") is None:
            available_methods["buff-bankcard"] = methods[2].get("balance")
        return available_methods

    def buy_goods(
        self,
        sell_order_id,
        goods_id,
        price,
        pay_method: str,
        ask_seller_send_offer: bool,
        game_name="csgo",
    ):
        """
        Since some sellers have disabled seller-initiated offers, this API is not recommended
        :param sell_order_id:
        :param goods_id:
        :param price:
        :param pay_method: Only supports buff-alipay or buff-bankcard.
        :param ask_seller_send_offer: Whether to ask seller to send offer
        If False, then buyer sends offer
        Warning: This API does not automatically initiate offers, offers need to be initiated by user on mobile BUFF!!!
        If seller has disabled seller-initiated offers, it will automatically change to buyer sending offer!!!
        Recommend using with github.com/jiajiaxd/Buff-Bot for better results!
        :param game_name: Default is csgo
        :return: If purchase successful returns 'Purchase successful', if failed returns error message
        """
        load = {
            "game": game_name,
            "goods_id": goods_id,
            "price": price,
            "sell_order_id": sell_order_id,
            "token": "",
            "cdkey_id": "",
        }
        if pay_method == "buff-bankcard":
            load["pay_method"] = 1
        elif pay_method == "buff-alipay":
            load["pay_method"] = 3
        else:
            raise ValueError("Invalid pay_method")
        headers = copy.deepcopy(self.session.headers)
        headers["accept"] = "application/json, text/javascript, */*; q=0.01"
        headers["content-type"] = "application/json"
        headers["dnt"] = "1"
        headers["origin"] = self.BASE_URL
        headers["referer"] = f"{self.BASE_URL}/goods/{str(goods_id)}?from=market"
        headers["x-requested-with"] = "XMLHttpRequest"
        # Get latest csrf_token
        self.get(f"{self.BASE_URL}/api/message/notification")
        self.session.cookies.get("csrf_token")
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))
        response = json.loads(self.post(f"{self.BASE_URL}/api/market/goods/buy", json=load, headers=headers).text)
        bill_id = response.get("data").get("id")
        self.get(
            f"{self.BASE_URL}/api/market/bill_order/batch/info",
            params={"bill_orders": bill_id},
        )
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))
        time.sleep(0.5)  # Since Buff server needs time to process payment, sleep must be added here, otherwise next request cannot be sent
        if ask_seller_send_offer:
            load = {"bill_orders": [bill_id], "game": game_name}
            response = self.post(
                f"{self.BASE_URL}/api/market/bill_order/ask_seller_to_send_offer",
                json=load,
                headers=headers,
            )
        else:
            load = {"bill_order_id": bill_id, "game": game_name}
            response = self.post(
                f"{self.BASE_URL}/api/market/bill_order/notify_buyer_to_send_offer",
                json=load,
                headers=headers,
            )
        response = json.loads(response.text)
        if response.get("msg") is None and response.get("code") == "OK":
            return "Purchase successful"
        else:
            return response

    def get_notification(self, headers=None) -> dict:
        """
        Get notification
        :return: dict
        """
        if headers:
            self.session.headers = headers
        response = self.get(f"{self.BASE_URL}/api/message/notification")
        data = response.json()
        if response.status_code == 200:
            return data["data"]
        elif 'error' in data:
            return data
        else:
            return {}

    def get_steam_trade(self) -> list:
        response = self.get(f"{self.BASE_URL}/api/market/steam_trade")
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK":
                return data["data"]
        return []

    def on_sale(self, assets: list[models.BuffOnSaleAsset]):
        """
        Only supports CSGO, returns successfully listed item ids
        """
        response = self.post(
            f"{self.BASE_URL}/api/market/sell_order/create/manual_plus",
            json={
                "appid": "730",
                "game": "csgo",
                "assets": [asset.model_dump(exclude_none=True) for asset in assets],
            },
            headers=self.CSRF_Fucker(),
        )
        success = []
        problem_assets = {}
        for good in response.json()["data"].keys():
            if response.json()["data"][good] == "OK":
                success.append(good)
            else:
                problem_assets[good] = response.json()["data"][good]
        return success, problem_assets

    def cancel_sale(self, sell_orders: list, exclude_sell_orders: list = []):
        """
        Returns number of successfully delisted items
        """
        success = 0
        problem_sell_orders = {}
        for index in range(0, len(sell_orders), 50):
            response = self.post(
                f"{self.BASE_URL}/api/market/sell_order/cancel",
                json={
                    "game": "csgo",
                    "sell_orders": sell_orders[index : index + 50],
                    "exclude_sell_orders": exclude_sell_orders,
                },
                headers=self.CSRF_Fucker(),
            )
            if response.json()["code"] != "OK":
                raise Exception(response.json().get("msg", None))
            for key in response.json()["data"].keys():
                if response.json()["data"][key] == "OK":
                    success += 1
                else:
                    problem_sell_orders[key] = response.json()["data"][key]
        return success, problem_sell_orders

    def get_on_sale(self, page_num=1, page_size=500, mode="2,5", fold="0"):
        return self.get(
            f"{self.BASE_URL}/api/market/sell_order/on_sale",
            params={
                "page_num": page_num,
                "page_size": page_size,
                "mode": mode,
                "fold": fold,
                "game": "csgo",
                "appid": 730,
            },
        )

    def change_price(self, sell_orders: list):
        """
        problem's key is order ID
        """
        success = 0
        problems = {}
        for index in range(0, len(sell_orders), 50):
            response = self.post(
                f"{self.BASE_URL}/api/market/sell_order/change",
                json={
                    "appid": "730",
                    "sell_orders": sell_orders[index : index + 50],
                },
                headers=self.CSRF_Fucker(),
            )
            if response.json()["code"] != "OK":
                raise Exception(response.json().get("msg", None))
            for key in response.json()["data"].keys():
                if response.json()["data"][key] == "OK":
                    success += 1
                else:
                    problems[key] = response.json()["data"][key]
        return success, problems

    @no_type_check
    def CSRF_Fucker(self):
        self.get(f"{self.BASE_URL}/api/market/steam_trade")
        csrf_token = self.session.cookies.get("csrf_token", domain="buff.163.com")
        headers = copy.deepcopy(self.session.headers)
        headers.update(
            {
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
                "Referer": f"{self.BASE_URL}/market/sell_order/create?game=csgo",
            }
        )  
        return headers