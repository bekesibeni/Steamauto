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

    def __init__(self, buffcookie, user_agent=None, proxies=None):
        if not user_agent:
            user_agent = get_ua()
        if proxies:
            logger.info("Detected Buff proxy settings, applying same proxy to Buff...")
        self.session = requests.session()
        self.session.proxies = proxies
        self.session.headers = {"User-Agent": user_agent}
        headers = copy.deepcopy(self.session.headers)
        headers["Cookie"] = buffcookie
        self.get_notification(headers=headers)

    def get(self, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = 10
        for i in range(10):
            response = self.session.get(url, **kwargs)
            logger.debug(f"GET {url} {response.status_code} {response.text[:500]}")
            if "系统繁忙" in response.text:
                logger.warning(f"BUFF interface busy, retrying...{i + 1}/10")
                time.sleep(2)
            else:
                break
        return response

    def post(self, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = 10
        for i in range(5):
            response = self.session.post(url, **kwargs)
            logger.debug(f"POST {url} {response.status_code} {response.text[:500]}")
            if "系统繁忙" in response.text:
                logger.warning(f"BUFF interface busy, retrying...{i + 1}/5")
                time.sleep(2)
            else:
                break
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

    def get_sell_order_to_deliver_page(self, game: str) -> str:
        """Get the to_deliver page HTML to extract order IDs"""
        # Build cookie string from session cookies
        cookie_parts = []
        for cookie in self.session.cookies:
            cookie_parts.append(f"{cookie.name}={cookie.value}")
        cookie_string = "; ".join(cookie_parts)
        
        headers = {
            "cookie": cookie_string
        }
        
        url = f"{self.BASE_URL}/market/sell_order/to_deliver?game={game}"
        response = self.session.get(url, headers=headers)
        if response.status_code == 200:
            return response.text
        return ""

    def get_sell_order_to_deliver_batch(self, game: str, bill_orders: List[str]) -> Dict:
        """Get batch orders waiting for delivery with HTML data"""
        params = {
            "game": game,
            "bill_orders": ",".join(bill_orders)
        }
        # Build cookie string from session cookies (like Node.js does)
        cookie_parts = []
        for cookie in self.session.cookies:
            cookie_parts.append(f"{cookie.name}={cookie.value}")
        cookie_string = "; ".join(cookie_parts)
        
        # Only cookie header needed, just like Node.js
        headers = {
            "cookie": cookie_string
        }
        
        # Use session.get directly to avoid JSON parsing in debug log
        url = f"{self.BASE_URL}/market/sell_order/to_deliver/batch"
        response = self.session.get(url, params=params, headers=headers)
        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("code") == "OK" and "data" in data:
                    return data
            except Exception:
                # If JSON parsing fails, return raw text
                return {"code": "OK", "data": response.text}
        return {}

    def seller_send_offer(self, steamid, encrypted_seller_info: str, bill_orders, last_login_time=None) -> Dict:
        """Ask BUFF to create and send the Steam trade offer on the seller's behalf.

        Used for orders where ``is_seller_asked_to_send_offer`` is true (the buyer
        will not initiate the offer). ``encrypted_seller_info`` must be the seller's
        steamcommunity.com cookie string encrypted with BuffApiCrypt; BUFF decrypts
        it server-side and uses that session to send the offer. Uploading fresh
        cookies here also clears a ``seller_cookie_invalid`` state on the order.

        On success BUFF populates the order's ``tradeofferid`` (read it back via
        get_bill_order_info / get_sell_order_to_deliver); the seller must then
        mobile-confirm the now-outgoing offer.
        """
        if not isinstance(bill_orders, (list, tuple)):
            bill_orders = [bill_orders]
        payload = {
            "steamid": str(steamid),
            "seller_info": encrypted_seller_info,
            "bill_orders": list(bill_orders),
        }
        if last_login_time is not None:
            payload["last_login_time"] = last_login_time
        response = self.post(
            f"{self.BASE_URL}/api/market/manual_plus/seller_send_offer",
            json=payload,
            headers=self.CSRF_Fucker(),
        )
        try:
            return response.json()
        except ValueError:
            return {}

    def get_bill_order_info(self, bill_orders) -> Dict:
        """Get bill order info; used to read back the tradeofferid BUFF created
        after a seller_send_offer call."""
        if isinstance(bill_orders, (list, tuple)):
            bill_orders = ",".join(bill_orders)
        response = self.get(
            f"{self.BASE_URL}/api/market/bill_order/batch/info",
            params={"bill_orders": bill_orders},
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == "OK" and "data" in data:
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
        Contains user balance and other information.
        with_pending_divide_amount=1 is required, otherwise BUFF returns
        pending_divide_amount as "0" instead of the real pending total.
        :return: dict
        """
        response = self.get(
            f"{self.BASE_URL}/api/asset/get_brief_asset/",
            params={"with_pending_divide_amount": 1},
        )
        if response.status_code == 200:
            data = response.json()
            if data["code"] == "OK" and "data" in data:
                return data["data"]
        return {}

    def get_balance(self) -> Dict[str, float]:
        """
        Get the account balance from the brief asset endpoint.
        - cash_amount: available wallet balance
        - pending_divide_amount: amount pending to be divided/settled
        :return: dict with "cash_amount" and "pending_divide_amount" as floats
        """
        data = self.get_user_brief_assest()

        def _to_float(value) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        return {
            "cash_amount": _to_float(data.get("cash_amount")),
            "pending_divide_amount": _to_float(data.get("pending_divide_amount")),
        }

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
        need_login = (
            (min_paintseed is not None) or
            (max_paintseed is not None) or
            (sort_by != "default")
        )
        if min_paintseed is not None:
            params["min_paintseed"] = min_paintseed
        if max_paintseed is not None:
            params["max_paintseed"] = max_paintseed
        request_method = self if need_login else requests
        url = f"{self.BASE_URL}/api/market/goods/sell_order"
        headers = get_random_header()
        try:
            return request_method.get(url, params=params, headers=headers, proxies=proxy, timeout=10).json().get("data")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
        except ValueError as e:
            logger.error(f"Response is not JSON: {e}")
            return None

    def get_available_payment_methods(self, sell_order_id, goods_id, price, game_name="csgo") -> dict:
        """
        :param game_name: Default is csgo
        :param sell_order_id:
        :param goods_id:
        :param price: Skin price
        :return: dict key will only contain buff-alipay and buff-bankcard, if key doesn't exist, it means this payment method is unavailable. value is current balance
        """
        try:
            data = self.get(
                f"{self.BASE_URL}/api/market/goods/buy/preview",
                params={
                    "game": game_name,
                    "sell_order_id": sell_order_id,
                    "goods_id": goods_id,
                    "price": price,
                },
            ).json().get("data", {})
            if not data:
                raise ValueError("Unable to get payment methods. Check params and account status.")
            methods = data.get("pay_methods", [])
            available_methods = dict()
            if not methods or len(methods) < 3:
                raise ValueError("Unable to get payment methods. Check params and account status.")
            if methods[0].get("error") is None:
                available_methods["buff-alipay"] = methods[0].get("balance")
            if methods[2].get("error") is None:
                available_methods["buff-bankcard"] = methods[2].get("balance")
            return available_methods
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

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
        :return: Response JSON from the send-offer step. Caller inspects code/msg to determine success.
        """
        PAY_METHOD_MAP = {
            "buff-bankcard": 1,
            "buff-alipay": 3,
        }
        if pay_method not in PAY_METHOD_MAP:
            raise ValueError("Invalid pay_method")
        load = {
            "game": game_name,
            "goods_id": goods_id,
            "price": price,
            "sell_order_id": sell_order_id,
            "token": "",
            "cdkey_id": "",
            "pay_method": PAY_METHOD_MAP[pay_method],
        }
        try:
            # Refresh csrf_token before the buy request
            self.get_notification()
            self.session.cookies.get("csrf_token")
        except Exception as e:
            raise ValueError("Unable to get CSRF token. Check login status.") from e

        headers = copy.deepcopy(self.session.headers)
        headers["accept"] = "application/json, text/javascript, */*; q=0.01"
        headers["content-type"] = "application/json"
        headers["dnt"] = "1"
        headers["origin"] = self.BASE_URL
        headers["referer"] = f"{self.BASE_URL}/goods/{str(goods_id)}?from=market"
        headers["x-requested-with"] = "XMLHttpRequest"
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))

        response = self.post(f"{self.BASE_URL}/api/market/goods/buy", json=load, headers=headers).json()
        data = response.get("data", {})
        bill_id = data.get("id", None)
        if bill_id is None:
            raise ValueError("Unable to get order ID. Check params and account status.")
        self.get(
            f"{self.BASE_URL}/api/market/bill_order/batch/info",
            params={"bill_orders": bill_id},
        )
        headers["x-csrftoken"] = str(self.session.cookies.get("csrf_token"))
        time.sleep(0.5)  # Buff needs a moment to process payment before the next request
        if ask_seller_send_offer:
            return self.ask_seller_to_send_offer(bill_id, headers, game_name)
        else:
            return self.notify_buyer_to_send_offer(bill_id, headers, game_name)

    def ask_seller_to_send_offer(self, bill_id, headers, game_name="csgo"):
        load = {"bill_orders": [bill_id], "game": game_name}
        response = self.post(
            f"{self.BASE_URL}/api/market/bill_order/ask_seller_to_send_offer",
            json=load,
            headers=headers,
        )
        return response.json()

    def notify_buyer_to_send_offer(self, bill_id, headers, game_name="csgo"):
        load = {"bill_order_id": bill_id, "game": game_name}
        response = self.post(
            f"{self.BASE_URL}/api/market/bill_order/notify_buyer_to_send_offer",
            json=load,
            headers=headers,
        )
        return response.json()

    def get_steam_info(self):
        return self.get(f"{self.BASE_URL}/account/api/steam/info").json()["data"]

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
        resp_data = response.json()["data"]
        for good in resp_data.keys():
            if resp_data[good] == "OK":
                success.append(good)
            else:
                problem_assets[good] = resp_data[good]
        return success, problem_assets

    def cancel_sale(self, sell_orders: list, exclude_sell_orders: list = None):
        """
        Returns number of successfully delisted items
        """
        if exclude_sell_orders is None:
            exclude_sell_orders = []
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
            resp_json = response.json()
            if resp_json["code"] != "OK":
                raise Exception(resp_json.get("msg", None))
            resp_data = resp_json["data"]
            for key in resp_data.keys():
                if resp_data[key] == "OK":
                    success += 1
                else:
                    problem_sell_orders[key] = resp_data[key]
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
            resp_json = response.json()
            if resp_json["code"] != "OK":
                raise Exception(resp_json.get("msg", None))
            resp_data = resp_json["data"]
            for key in resp_data.keys():
                if resp_data[key] == "OK":
                    success += 1
                else:
                    problems[key] = resp_data[key]
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