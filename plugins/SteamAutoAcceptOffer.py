import json
import os
import pickle
import time

from utils.logger import PluginLogger, handle_caught_exception
from utils.static import SESSION_FOLDER


class SteamAutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.logger = PluginLogger('SteamAutoAcceptOffer')
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        self.ignored_trade_offers = []

    def init(self):
        return False

    def exec(self):
        while True:
            try:
                with self.steam_client_mutex:
                    if not self.steam_client.is_session_alive():
                        self.logger.info("Steam session expired. Relogging...")
                        self.steam_client.relogin()
                        self.logger.info("Steam session refreshed")
                        steam_session_path = os.path.join(SESSION_FOLDER, self.steam_client.username.lower() + ".pkl")
                        with open(steam_session_path, "wb") as f:
                            pickle.dump(self.steam_client.session, f)
                self.logger.info('Checking pending trade offers...')
                with self.steam_client_mutex:
                    trade_summary = self.steam_client.get_trade_offers(merge=False)["response"]
                # Filter out already-ignored offers
                for key, offer in trade_summary.items():
                    if isinstance(offer, list):
                        original_count = len(offer)
                        filtered_offers = [
                            trade_offer for trade_offer in offer
                            if trade_offer.get('tradeofferid', None) not in self.ignored_trade_offers
                        ]
                        trade_summary[key] = filtered_offers

                        filtered_count = original_count - len(filtered_offers)
                        if filtered_count > 0:
                            self.logger.debug(f'Filtered out {filtered_count} ignored trade offers')
                self.logger.info(f"Detected {len(trade_summary['trade_offers_received'])} pending trade offers")
                self.logger.debug(f'Pending trade offer summary: {json.dumps(trade_summary, ensure_ascii=False)}')

                if len(trade_summary["trade_offers_received"]) > 0:
                    with self.steam_client_mutex:
                        trade_offers = self.steam_client.get_trade_offers(merge=False)["response"]
                    self.logger.debug(
                        f'Pending trade offer details: {json.dumps(trade_offers, ensure_ascii=False)}'
                    )
                    if len(trade_offers["trade_offers_received"]) > 0:
                        for trade_offer in trade_offers["trade_offers_received"]:
                            self.logger.debug(
                                f'\nOffer[{trade_offer["tradeofferid"]}] '
                                f'\nitems_to_give: {len(trade_offer.get("items_to_give", {}))}'
                                f'\nitems_to_receive: {len(trade_offer.get("items_to_receive", {}))}'
                            )
                            if len(trade_offer.get("items_to_give", {})) == 0:
                                self.logger.info(
                                    f'Offer[{trade_offer["tradeofferid"]}] is a gift offer. Accepting...'
                                )
                                try:
                                    with self.steam_client_mutex:
                                        self.steam_client.accept_trade_offer(trade_offer["tradeofferid"])
                                    self.logger.info(f'Offer[{trade_offer["tradeofferid"]}] accepted successfully')
                                except Exception as e:
                                    if 'Invalid trade offer state' in str(e):
                                        self.logger.warning(
                                            f'Offer[{trade_offer["tradeofferid"]}] already accepted or canceled. Ignoring'
                                        )
                                        self.ignored_trade_offers.append(trade_offer["tradeofferid"])
                                        continue
                                    handle_caught_exception(e, "SteamAutoAcceptOffer", known=True)
                                    self.logger.error("Steam error. Try later")

                            else:
                                self.logger.info(
                                    f'Offer[{trade_offer["tradeofferid"]}] requires giving items. Skipping'
                                )
            except Exception as e:
                handle_caught_exception(e, "SteamAutoAcceptOffer")
                self.logger.error("Unknown error. Try later")
            time.sleep(self.config["steam_auto_accept_offer"]["interval"])
