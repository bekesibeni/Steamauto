import json
import requests

from utils.logger import PluginLogger


class C5Account:
    # API reference: https://apifox.com/apidoc/shared-bcbf0c5d-caf4-4ea6-b2c1-0bc292a2e6b2/doc-3014376
    def __init__(self, app_key):
        self.app_key = app_key
        self.client = requests.Session()
        self.client.headers.update({"app-key": self.app_key})
        self.logger = PluginLogger("C5Game API")

    def post(self, path, data):
        url = 'http://openapi.c5game.com/' + path
        resp = self.client.post(url, json=data)
        self.logger.debug(f"POST {path} {json.dumps(data, ensure_ascii=False)} {resp.text}")
        return resp.json()

    def get(self, path, params):
        url = 'http://openapi.c5game.com/' + path
        resp = self.client.get(url, params=params)
        self.logger.debug(f"GET {path} {params} {resp.text}")
        return resp.json()

    def balance(self):
        return self.get('/merchant/account/v1/balance', {})

    def checkAppKey(self):
        resp = self.balance()
        return resp.get('success', False)

    def orderList(self, status=0, page=1, steamId=None):
        """
        Order status:
        not set → all orders
        0 → pending payment
        1 → pending delivery
        2 → delivering
        3 → awaiting receipt
        10 → completed
        11 → canceled
        """
        data = {'status': status, 'page': page}
        if steamId:
            data['steamId'] = steamId
        return self.get('/merchant/order/v1/list', data)

    def deliver(self, order_list: list):
        return self.post('/merchant/order/v1/deliver', order_list)
