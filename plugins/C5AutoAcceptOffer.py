import time

from PyC5Game import C5Account
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer, external_handler

logger = PluginLogger("C5AutoAcceptOffer")


class C5AutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        with steam_client_mutex:
            self.steam_id = steam_client.get_steam64id_from_cookies()

    def init(self) -> bool:
        return False

    def exec(self):
        ignored_list = []
        try:
            self.interval = self.config.get("c5_auto_accept_offer").get("interval")
        except Exception as e:
            logger.error("Error reading config. Check 'interval' in the config file.")
            return True

        app_key = self.config.get("c5_auto_accept_offer").get("app_key")
        self.client = C5Account(app_key)
        if self.client.checkAppKey:
            logger.info("C5 account login successful")
        else:
            logger.error("C5 account login failed. Check 'app_key' in the config file.")
            return True

        while True:
            try:
                logger.info("Checking for pending delivery orders...")
                notDeliveredOrders = []
                page = 0
                while True:
                    page += 1
                    resp = self.client.orderList(status=1, page=page, steamId=self.steam_id)
                    if resp.get("errorCode", "") == 400001:
                        logger.error("Invalid app_key. Check 'app_key' in the config file.")
                        logger.error("Plugin stopped due to invalid app_key.")
                        return 1
                    notDeliveredOrders = resp.get("data").get("list", [])
                    if len(resp.get("data").get("list", [])) < resp["data"]["limit"]:
                        break
                logger.info(f"Found {len(notDeliveredOrders)} pending delivery orders")
                if notDeliveredOrders:
                    toSendOrderIds = []
                    for order in notDeliveredOrders:
                        if external_handler(
                            "C5-" + str(order["orderId"]),
                            desc=f"Platform: C5Game\nItem: {order['name']}\nOrder price: {order['price']} RMB",
                        ):
                            toSendOrderIds.append(order["orderId"])
                    if len(toSendOrderIds) > 0:
                        logger.info(f"Sending {len(toSendOrderIds)} offer(s)...")
                        self.client.deliver(toSendOrderIds)
                        logger.info("Requested C5 server to send offers. Will fetch offer IDs in 30 seconds")
                        time.sleep(30)

                deliveringOrders = []
                page = 0
                while True:
                    page += 1
                    resp = self.client.orderList(status=2, page=page, steamId=self.steam_id)
                    deliveringOrders = resp.get("data").get("list", [])
                    if len(resp.get("data").get("list", [])) < resp["data"]["limit"]:
                        break
                logger.info(f"Found {len(deliveringOrders)} orders in delivery")
                for deliveringOrder in deliveringOrders:
                    logger.info(f"Processing order {deliveringOrder['name']} ...")
                    offerId = deliveringOrder["orderConfirmInfoDTO"]["offerId"]
                    if offerId in ignored_list:
                        logger.info(f"Order {deliveringOrder['name']} already delivered. Skipping")
                        continue
                    if accept_trade_offer(
                        self.steam_client,
                        self.steam_client_mutex,
                        offerId,
                        desc=f"Platform: C5Game\nItem: {deliveringOrder['name']}\nOrder price: {round(deliveringOrder['price'], 2)} RMB",
                        reportToExternal=False,
                    ):
                        logger.info(f"Order {deliveringOrder['name']} delivered")
                        ignored_list.append(offerId)
                        if deliveringOrders.index(deliveringOrder) != len(deliveringOrders) - 1:
                            logger.info("To avoid frequent Steam requests, waiting 3 seconds before next order")
                            time.sleep(3)
                    else:
                        logger.error(f"Order {deliveringOrder['name']} delivery failed. Check network or Steam account.")
            except Exception as e:
                handle_caught_exception(e, prefix="C5AutoAcceptOffer")
            logger.info(f"Waiting {self.interval} seconds before rechecking for pending delivery orders")
            time.sleep(self.interval)
