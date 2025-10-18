import base64
import json
import os
import threading
import time
from datetime import datetime
from ssl import SSLCertVerificationError, SSLError
from typing import Optional, Dict, Any

import json5
import requests
from requests.exceptions import RequestException

import steampy.exceptions
from steampy.client import SteamClient
from steampy.exceptions import ApiException
from steampy.models import GameOptions
from utils import static
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.static import SESSION_FOLDER, STEAM_ACCOUNT_INFO_FILE_PATH, CONFIG_FILE_PATH
from utils.tools import accelerator, get_encoding, pause

logger = PluginLogger('SteamClient')

steam_client_mutex = threading.Lock()
steam_client: Optional[SteamClient] = None
token_refresh_thread = None  # background refresh thread reference

try:
    with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
        config = json5.loads(f.read())
except Exception:
    pass

# ================= JWT parsing and cache helpers ===================

def _parse_jwt_exp(jwt_token: Optional[str]) -> int:
    if not jwt_token:
        return 0
    try:
        parts = jwt_token.split('.')
        if len(parts) != 3:
            return 0
        payload = parts[1]
        payload += '=' * (4 - len(payload) % 4)
        decoded_payload = base64.b64decode(payload)
        payload_data = json.loads(decoded_payload)
        return payload_data.get('exp', 0)
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.warning("Failed to parse JWT expiration")
        return 0

def _get_token_cache_path(username: str) -> str:
    return os.path.join(SESSION_FOLDER, f"steam_account_{username.lower()}.json")

def _load_token_cache(username: str) -> dict:
    cache_path = _get_token_cache_path(username)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.warning(f"Failed to read token cache file: {cache_path}")
    return {}

def _save_token_cache(username: str, auth_info: Dict[str, Any]):
    """
    Expected auth_info structure:
    {
        steamid: str,
        access_token: Optional[str],
        refresh_token: Optional[str]
    }
    """
    cache_path = _get_token_cache_path(username)
    steamid = auth_info.get("steamid")
    access_token = auth_info.get("access_token")
    refresh_token = auth_info.get("refresh_token")

    access_exp = _parse_jwt_exp(access_token)
    refresh_exp = _parse_jwt_exp(refresh_token)

    cache_data = {
        "steamid": steamid,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_exp_timestamp": access_exp,
        "refresh_token_exp_timestamp": refresh_exp,
    }

    # Human-readable times
    try:
        if access_exp:
            cache_data["access_token_exp_readable"] = datetime.fromtimestamp(access_exp).strftime("%Y-%m-%d %H:%M:%S")
        if refresh_exp:
            cache_data["refresh_token_exp_readable"] = datetime.fromtimestamp(refresh_exp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.info("Saved token cache: %s", cache_path)
        if access_exp:
            logger.info(" access_token expires at: %s", cache_data.get("access_token_exp_readable"))
        if refresh_exp:
            logger.info(" refresh_token expires at: %s", cache_data.get("refresh_token_exp_readable"))
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"Failed to save token cache: {cache_path}")

# ================== Session and proxy settings ======================

def _setup_client_session(client: SteamClient, config: dict):
    if config["steam_login_ignore_ssl_error"]:
        logger.warning("Warning: SSL verification disabled. Ensure your network is trusted.")
        client._session.verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore
    else:
        client._session.verify = True

    if config["steam_local_accelerate"]:
        logger.info("Built-in accelerator enabled")
        client._session.auth = accelerator()

    if config.get("use_proxies", False):
        client._session.proxies = config["proxies"]
        logger.info("Steam proxy enabled")

def _check_proxy_availability(config: dict) -> bool:
    if not config.get("use_proxies", False):
        return True
    if not isinstance(config["proxies"], dict):
        logger.error("Invalid proxies format. Check your config.")
        return False
    logger.info("Checking proxy availability...")
    try:
        requests.get("https://steamcommunity.com", proxies=config["proxies"], timeout=10)
        logger.info("Proxy reachable")
        return True
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("Proxy unreachable. Check config or set use_proxies=false")
        return False

# ================== Background refresh thread ========================

class TokenRefreshThread(threading.Thread):
    """
    Maintain access_token / refresh_token in background.
    Strategy:
      - Each loop, check access_token expiry.
      - If < 3600s to expire, try refresh (loginByRefreshToken).
      - If session invalid or refresh fails -> relogin().
      - On total failure -> notify.
    """
    def __init__(self, username: str, config: dict):
        super().__init__(daemon=True)
        self.username = username
        self.config = config
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._refresh_cycle()
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("Background token refresh loop error")
            # Next check interval
            wait_seconds = self._compute_wait_interval()
            self.stop_event.wait(wait_seconds)

    def _compute_wait_interval(self) -> int:
        """
        Decide next check based on access_token expiry:
          - >6h remaining: check in 3h
          - 1h~6h: check in 1h
          - <1h: check in 10m
          - unknown: 6h
        """
        try:
            cache = _load_token_cache(self.username)
            exp = cache.get("access_token_exp_timestamp", 0)
            if not exp:
                return 6 * 3600
            now = int(time.time())
            remain = exp - now
            if remain <= 0:
                return 300  # expired, retry in 5m
            if remain > 6 * 3600:
                return 3 * 3600
            if remain > 3600:
                return 3600
            return 600
        except Exception:
            return 6 * 3600

    def _refresh_cycle(self):
        try:
            global steam_client
            with steam_client_mutex:
                if not steam_client:
                    return
                # Check token window
                cache = _load_token_cache(self.username)
                access_exp = cache.get("access_token_exp_timestamp", 0)
                now = int(time.time())
                need_refresh = bool(access_exp and access_exp - now < 3600)

                if not steam_client.is_session_alive():
                    logger.info("Session invalid. Attempting refresh...")
                    # Prefer refresh_token
                    cache = _load_token_cache(self.username)
                    refresh_token = cache.get("refresh_token")
                    steamid = cache.get("steamid")
                    if refresh_token and steamid:
                        logger.info("Refreshing access_token via refresh_token...")
                        try:
                            auth_info = steam_client.loginByRefreshToken(refresh_token, steamid, steam_client.steam_guard)
                            if auth_info and isinstance(auth_info, dict):
                                _save_token_cache(self.username, auth_info)
                                logger.info("Background refresh_token succeeded")
                                return
                            else:
                                raise Exception("loginByRefreshToken returned no valid auth_info")
                        except Exception as e:
                            handle_caught_exception(e, known=True)
                            logger.warning("Refresh via refresh_token failed: %s", e)
                    # Fallback to relogin
                    logger.info("Refresh failed or unavailable. Trying username/password relogin...")
                    try:
                        auth_info = steam_client.relogin()
                        if auth_info and isinstance(auth_info, dict):
                            _save_token_cache(self.username, auth_info)
                            logger.info("Relogin succeeded")
                            return
                        else:
                            raise Exception("relogin returned no valid auth_info")
                    except Exception as e:
                        handle_caught_exception(e, known=True)
                        logger.error("Session invalid and refresh failed")
                        send_notification("Steam session refresh failed", "After invalidation both refresh_token and relogin failed. Check account or network.")
                        return

                if need_refresh:
                    # Try refresh_token
                    cache = _load_token_cache(self.username)
                    refresh_token = cache.get("refresh_token")
                    steamid = cache.get("steamid")
                    if refresh_token and steamid:
                        logger.info("Refreshing access_token via refresh_token...")
                        try:
                            auth_info = steam_client.loginByRefreshToken(refresh_token, steamid, steam_client.steam_guard)
                            if auth_info and isinstance(auth_info, dict):
                                _save_token_cache(self.username, auth_info)
                                logger.info("Background refresh_token succeeded")
                                return
                            else:
                                raise Exception("loginByRefreshToken returned no valid auth_info")
                        except Exception as e:
                            handle_caught_exception(e, known=True)
                            logger.warning("Refresh via refresh_token failed: %s", e)

                    # Try relogin
                    try:
                        auth_info = steam_client.relogin()
                        if auth_info and isinstance(auth_info, dict):
                            _save_token_cache(self.username, auth_info)
                            logger.info("Relogin succeeded (refresh phase)")
                            return
                    except Exception as e:
                        handle_caught_exception(e, known=True)

                    logger.error("Background refresh failed. Unable to extend session")
                    send_notification("Steam session maintenance failed", "Automatic refresh and relogin both failed. Check account or network.")
        except requests.exceptions.RequestException:
            logger.error('Cannot check Steam session state. Check network or proxy settings.')
        except Exception as e:
            handle_caught_exception(e, known=False)

    def stop(self):
        self.stop_event.set()

# ================== Main login flow ==========================

def login_to_steam_single_account(account_info: dict, config: dict):
    """
    Login a single Steam account with the provided account information.
    Returns SteamClient instance or None if login fails.
    """
    username = account_info.get("steam_username", "")
    password = account_info.get("steam_password", "")
    if not username or not password:
        logger.error(f"Account {account_info.get('name', 'Unknown')}: Username or password is empty")
        return None

    config["use_proxies"] = config.get("use_proxies", False)
    if not _check_proxy_availability(config):
        return None

    token_cache = _load_token_cache(username)
    now = int(time.time())

    # 1. Use cached access_token
    access_token = token_cache.get("access_token")
    access_exp = token_cache.get("access_token_exp_timestamp", 0)
    steamid_cache = token_cache.get("steamid")
    if access_token and steamid_cache and access_exp and access_exp - now > 60:
        logger.info(f"Using cached access token for {username}")
        try:
            if config.get("use_proxies", False):
                client = SteamClient(api_key="", proxies=config["proxies"])
            else:
                client = SteamClient(api_key="")
            _setup_client_session(client, config)
            if client.set_and_verify_access_token(steamid_cache, access_token, account_info):
                logger.info(f"Cached access token login succeeded for {username}")
                return client
        except Exception as e:
            logger.warning(f"Cached access token failed for {username}: {str(e)}")

    # 2. Try refresh_token login
    refresh_token = token_cache.get("refresh_token")
    if refresh_token:
        logger.info(f"Trying refresh token login for {username}")
        try:
            if config.get("use_proxies", False):
                client = SteamClient(api_key="", proxies=config["proxies"])
            else:
                client = SteamClient(api_key="")
            _setup_client_session(client, config)
            auth_info = client.loginByRefreshToken(refresh_token, steamid_cache, account_info)
            if client.is_session_alive():
                logger.info(f"Refresh token login succeeded for {username}")
                if auth_info and isinstance(auth_info, dict):
                    _save_token_cache(username, auth_info)
                return client
        except Exception as e:
            logger.warning(f"Refresh token login failed for {username}: {str(e)}")

    # 3. Username/password login
    logger.info(f"Logging in to Steam with username/password for {username}")
    try:
        if config.get("use_proxies", False):
            client = SteamClient(api_key="", proxies=config["proxies"])
        else:
            client = SteamClient(api_key="")
        _setup_client_session(client, config)
        if config['use_proxies'] and config['steam_local_accelerate']:
            logger.warning('Both built-in accelerator and proxy are enabled. This is not recommended.')
        logger.info("Signing in...")
        auth_info = client.login(username, password, account_info)
        if client.is_session_alive():
            logger.info(f"Username/password login succeeded for {username}")
            if auth_info and isinstance(auth_info, dict):
                _save_token_cache(username, auth_info)
            return client
        else:
            logger.error(f"Login failed for {username}")
            return None
    except Exception as e:
        logger.error(f"Login error for {username}: {str(e)}")
        handle_caught_exception(e, known=True)
        return None


def login_to_steam(config: dict):
    """
    Login strategy priority:
    1) Cached access_token (not expired)
    2) refresh_token login
    3) Username/password login
    """
    global steam_client, token_refresh_thread

    # Read Steam account info
    try:
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
            try:
                steam_account_info = json5.loads(f.read())
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("Detected invalid format in " + STEAM_ACCOUNT_INFO_FILE_PATH + ". Check config file.")
                pause()
                return None
    except FileNotFoundError:
        logger.error("Missing " + STEAM_ACCOUNT_INFO_FILE_PATH + ". Add it first.")
        pause()
        return None

    if not isinstance(steam_account_info, dict):
        logger.error("Invalid config structure. Check config file.")
        return None
    for key, value in steam_account_info.items():
        if not value:
            logger.error(f"Key {key} in Steam account config is empty. Check config.")
            return None

    username = steam_account_info.get("steam_username", "")
    password = steam_account_info.get("steam_password", "")
    if not username or not password:
        logger.error("Steam username or password is empty. Check config.")
        return None

    config["use_proxies"] = config.get("use_proxies", False)
    if not _check_proxy_availability(config):
        pause()
        return None

    token_cache = _load_token_cache(username)
    now = int(time.time())

    # 1. Use cached access_token
    access_token = token_cache.get("access_token")
    access_exp = token_cache.get("access_token_exp_timestamp", 0)
    steamid_cache = token_cache.get("steamid")
    if access_token and steamid_cache and access_exp and access_exp - now > 60:
        logger.info("Found cached non-expired access_token. Restoring session...")
        try:
            if config.get("use_proxies", False):
                client = SteamClient(api_key="", proxies=config["proxies"])
            else:
                client = SteamClient(api_key="")
            _setup_client_session(client, config)
            if client.set_and_verify_access_token(steamid_cache, access_token, steam_account_info):
                logger.info("Login with cached access_token succeeded")
                steam_client = client
                static.STEAM_ACCOUNT_NAME = client.username or username
                static.STEAM_64_ID = client.get_steam64id_from_cookies()
                # Start refresh thread
                if token_refresh_thread is None or not token_refresh_thread.is_alive():
                    _start_token_refresh_thread(username, config)
                return steam_client
            else:
                logger.warning("Cached access_token invalid. Falling back to refresh_token flow")
        except Exception as e:
            handle_caught_exception(e, known=True)
            logger.warning("Restore via cached access_token failed")

    # 2. Try refresh_token login
    refresh_token = token_cache.get("refresh_token")
    refresh_exp = token_cache.get("refresh_token_exp_timestamp", 0)
    if refresh_token and steamid_cache:
        if refresh_exp and refresh_exp <= now:
            logger.warning("refresh_token expired. Falling back to username/password login")
        else:
            remaining = refresh_exp - now if refresh_exp else None
            if remaining:
                hours = remaining // 3600
                if hours > 0:
                    logger.info(f"refresh_token expires in ~{hours} hour(s)")
            logger.info("Trying login via refresh_token...")
            try:
                if config.get("use_proxies", False):
                    client = SteamClient(api_key="", proxies=config["proxies"])
                else:
                    client = SteamClient(api_key="")
                _setup_client_session(client, config)
                auth_info = client.loginByRefreshToken(refresh_token, steamid_cache, steam_account_info)
                if auth_info and client.is_session_alive():
                    logger.info("Login via refresh_token succeeded")
                    steam_client = client
                    _save_token_cache(username, auth_info)
                    static.STEAM_ACCOUNT_NAME = client.username or username
                    static.STEAM_64_ID = client.get_steam64id_from_cookies()
                    if token_refresh_thread is None or not token_refresh_thread.is_alive():
                        _start_token_refresh_thread(username, config)
                    return steam_client
                else:
                    logger.warning("refresh_token login failed. Falling back to username/password")
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.warning("refresh_token login failed. Falling back to username/password")

    # 3. Username/password login
    logger.info("Logging in to Steam with username/password...")
    try:
        if config.get("use_proxies", False):
            client = SteamClient(api_key="", proxies=config["proxies"])
        else:
            client = SteamClient(api_key="")
        _setup_client_session(client, config)
        if config['use_proxies'] and config['steam_local_accelerate']:
            logger.warning('Both built-in accelerator and proxy are enabled. This is not recommended.')
        logger.info("Signing in...")
        auth_info = client.login(username, password, steam_account_info)
        if client.is_session_alive():
            logger.info("Username/password login succeeded")
            steam_client = client
            if auth_info and isinstance(auth_info, dict):
                _save_token_cache(username, auth_info)
            static.STEAM_ACCOUNT_NAME = client.username
            static.STEAM_64_ID = client.get_steam64id_from_cookies()
            if token_refresh_thread is None or not token_refresh_thread.is_alive():
                _start_token_refresh_thread(username, config)
            return steam_client
        else:
            logger.error("Login failed")
            return None
    except FileNotFoundError as e:
        handle_caught_exception(e, known=True)
        logger.error("Missing " + STEAM_ACCOUNT_INFO_FILE_PATH + ". Add it first.")
        pause()
        return None
    except (SSLCertVerificationError, SSLError):
        if config["steam_local_accelerate"]:
            logger.error("Login failed. Local acceleration enabled but SSL verification not disabled. Set steam_login_ignore_ssl_error=true.")
        else:
            logger.error("Login failed. SSL certificate verification error. If your network is trusted, set steam_login_ignore_ssl_error=true.")
        pause()
        return None
    except (requests.exceptions.ConnectionError, TimeoutError):
        logger.error(
            "Network error.\nUse the built-in accelerator by setting steam_login_ignore_ssl_error=true and steam_local_accelerate=true.\n"
            "Note: game VPNs are not enough. Use real proxy tools like Clash/Proxifier."
        )
        pause()
        return None
    except (ApiException):
        logger.error("Login failed. Check network or possible Steam IP block.")
        pause()
        return None
    except (TypeError, AttributeError):
        logger.error("Login failed. Possible causes:\n 1) Proxy issues. Do not enable both proxy and built-in accelerator, or unstable proxy.\n 2) Steam server fluctuation.")
        pause()
        return None
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("Login failed. Check the format and contents of " + STEAM_ACCOUNT_INFO_FILE_PATH + ".")
        pause()
        return None

def _start_token_refresh_thread(username: str, config: dict):
    global token_refresh_thread
    try:
        token_refresh_thread = TokenRefreshThread(username, config)
        token_refresh_thread.start()
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error("Failed to start TokenRefreshThread")

# For external offer handler integration.
# Ensure external_offer_handler is correctly configured in your config.
def external_handler(tradeOfferId, desc) -> bool:
    """
    Interact with external offer handler (aligned with plugins/ExternalAutoAcceptOffer.py):
    1. Query /getToAcceptOffers. If offerId exists, call /deleteOffer to remove it, then return True.
    2. Otherwise POST to /submit and let the external handler decide via the 'deliver' field.
    """
    if not isinstance(config, dict):
        return True
    external_handler = config.get("external_offer_handler", "").strip()
    if not external_handler:
        return True

    base_url = external_handler.rstrip("/")

    # Check pending-accept list first
    try:
        get_url = f"{base_url}/getToAcceptOffers"
        logger.info(f'Checking external handler pending list {get_url} for offer {tradeOfferId} ...')
        resp = requests.get(get_url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        offers = payload.get("data", []) if isinstance(payload, dict) else []

        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offer_id = offer.get("offerId")
            if offer_id is None:
                continue
            if str(offer_id) == str(tradeOfferId):
                logger.info(f'Offer {tradeOfferId} found in external pending list. Deleting before accepting.')
                try:
                    delete_url = f"{base_url}/deleteOffer"
                    del_resp = requests.post(delete_url, json={"offerId": offer_id}, timeout=10)
                    del_resp.raise_for_status()
                    del_result = del_resp.json()
                    if isinstance(del_result, dict) and del_result.get("status") == "ok":
                        logger.info(f"Removed offer from external handler: {tradeOfferId}")
                    else:
                        logger.error(f"Failed to remove offer from external handler: {tradeOfferId} -> {del_result}")
                except Exception as e:
                    logger.error(f"Error calling external handler to delete offer: {e}")
                return True
    except Exception:
        logger.debug("Failed to check external pending list. Proceeding to /submit")

    # Submit to /submit
    external_handler_url = base_url + "/submit"
    try:
        data = {
            "offerId": tradeOfferId,
            "description": desc
        }
        logger.info(f'Submitting offer {tradeOfferId} to external handler {external_handler_url} ...')
        response = requests.post(external_handler_url, json=data, timeout=15)
        try:
            result = response.json()
        except Exception:
            logger.error(f'Unable to parse JSON from external handler {external_handler_url}. Skipping offer.')
            return False

        if isinstance(result, dict) and result.get('deliver'):
            logger.info(f'External handler accepted offer {tradeOfferId}')
            return True
        else:
            logger.info(f'External handler rejected offer {tradeOfferId}. Skipping.')
            return False
    except Exception:
        logger.error("Cannot connect to external offer handler. Skipping this offer.")
        return False

def accept_trade_offer(client: SteamClient, mutex, tradeOfferId, retry=False, desc="", network_retry_count=0, reportToExternal=True):
    max_network_retries = 3
    network_retry_delay = 5
    
    if reportToExternal:
        if not external_handler(tradeOfferId, desc):
            return True

    try:
        with mutex:
            client.accept_trade_offer(str(tradeOfferId))
        send_notification(f'Offer ID: {tradeOfferId}\n{desc}', title='Offer accepted')
        return True
    except Exception as e:
        if retry:
            logger.error(f"Failed to accept offer {tradeOfferId}.")
            return False

        # Network retry
        if isinstance(e, RequestException):
            if network_retry_count < max_network_retries:
                logger.warning(f"Network error accepting offer {tradeOfferId}. Retrying ({network_retry_count + 1}/{max_network_retries})...")
                handle_caught_exception(e, "SteamClient", known=True)
                time.sleep(network_retry_delay)
                return accept_trade_offer(
                    client, mutex, tradeOfferId, retry=False, desc=desc, network_retry_count=network_retry_count + 1
                )
            else:
                logger.error(f"Max network retries reached for offer {tradeOfferId}. Operation failed.")
                handle_caught_exception(e, "SteamClient", known=True)
                send_notification(f'Offer ID: {tradeOfferId}\n{desc}', title='Offer accept failed (network error)')
                return False

        if isinstance(e, ValueError):
            if 'Accepted' in str(e):
                logger.warning(f'Offer {tradeOfferId} already processed. Skipping.')
                handle_caught_exception(e, "SteamClient", known=True)
                return True
        if isinstance(e, (steampy.exceptions.ConfirmationExpected, steampy.exceptions.InvalidCredentials)):
            logger.error(f"Failed to accept offer {tradeOfferId}: session or credentials invalid. Aborting.")
            handle_caught_exception(e, "SteamClient", known=True)
            send_notification(f'Offer ID: {tradeOfferId}\n{desc}', title='Offer accept failed (invalid session)')
            return False
        if isinstance(e, KeyError):
            logger.error(f"Failed to accept offer {tradeOfferId}: offer not found or expired.")
            return False

        # Other errors
        handle_caught_exception(e, "SteamClient")
        logger.error(f"Failed to accept offer {tradeOfferId}.")

        if 'substring not found' in str(e):
            logger.error(f'Offer {tradeOfferId} failed due to Steam risk control. Check IP/accelerator/proxy.')
            handle_caught_exception(e, "SteamClient", known=True)
            return False

        send_notification(f'Offer ID: {tradeOfferId}\n{desc}', title='Offer accept failed')
        return False

def get_cs2_inventory(client: SteamClient, mutex):
    inventory = None
    try:
        with mutex:
            inventory = client.get_my_inventory(game=GameOptions.CS)  # type: ignore
            logger.log(5, 'Fetched Steam inventory: ' + json.dumps(inventory, ensure_ascii=False))
    except Exception as e:
        handle_caught_exception(e, "SteamClient", known=True)
        send_notification('Failed to fetch inventory. Check server network.', title='Inventory fetch failed')
    return inventory
