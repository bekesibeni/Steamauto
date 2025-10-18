import time

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.steam_client import accept_trade_offer
from utils.tools import exit_code
from utils.uu_helper import get_valid_token_for_uu


class UUAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger("UUAutoAcceptOffer")
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config

    def init(self) -> bool:
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("UUYoupin login failed. Exiting.")
            exit_code.set(1)
            return True
        return False

    def exec(self):
        uuyoupin = None
        token = get_valid_token_for_uu()
        if not token:
            self.logger.error("Login failed. Plugin will exit.")
            exit_code.set(1)
            return 1
        else:
            uuyoupin = uuyoupinapi.UUAccount(token)
        ignored_offer = {}
        interval = self.config["uu_auto_accept_offer"]["interval"]
        if uuyoupin is not None:
            while True:
                try:
                    uuyoupin.send_device_info()
                    self.logger.info("Checking UUYoupin pending deliveries...")
                    uu_wait_deliver_list = uuyoupin.get_wait_deliver_list()
                    len_uu_wait_deliver_list = len(uu_wait_deliver_list)
                    self.logger.info(f"{len_uu_wait_deliver_list} UUYoupin pending orders")
                    if len(uu_wait_deliver_list) != 0:
                        for item in uu_wait_deliver_list:
                            accepted = False
                            self.logger.info(
                                f"Accepting UUYoupin pending offer. Item: {item['item_name']}, "
                                f"Offer ID: {item['offer_id']}"
                            )
                            if item["offer_id"] is None:
                                self.logger.warning(
                                    "This order requires manual delivery (or is abnormal). Cannot auto-process. Skipping."
                                )
                            elif item["offer_id"] in ignored_offer and ignored_offer[item["offer_id"]] <= 10:
                                self.logger.info(
                                    "This trade offer was already handled by Steamauto. "
                                    "Likely due to UU system delay or a bulk purchase. This is not an error."
                                )
                                ignored_offer[item["offer_id"]] += 1
                            else:
                                if accept_trade_offer(
                                    self.steam_client,
                                    self.steam_client_mutex,
                                    str(item["offer_id"]),
                                    desc=f"Platform: UUYoupin\nItem: {item['item_name']}"
                                ):
                                    ignored_offer[str(item["offer_id"])] = 1
                                    self.logger.info(f"Offer [{str(item['offer_id'])}] accepted.")
                                    accepted = True
                            if (uu_wait_deliver_list.index(item) != len_uu_wait_deliver_list - 1) and accepted:
                                self.logger.info("Waiting 5 seconds to avoid frequent Steam API calls...")
                                time.sleep(5)
                except Exception as e:
                    if '登录状态失效，请重新登录' in str(e):  # keep original substring match from upstream
                        handle_caught_exception(e, "UUAutoAcceptOffer", known=True)
                        send_notification('UUYoupin login expired. Please log in again', title='UUYoupin login expired')
                        self.logger.error("Detected UUYoupin login expiration. Please log in again.")
                        self.logger.error("Login failed. Plugin will exit.")
                        exit_code.set(1)
                        return 1
                    else:
                        handle_caught_exception(e, "UUAutoAcceptOffer", known=False)
                        self.logger.error("Unknown error. Try again later.")
                self.logger.info("Rechecking pending deliveries in {0} seconds.".format(str(interval)))
                time.sleep(interval)
