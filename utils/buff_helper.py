import os
import time
from json import JSONDecodeError
from typing import Dict

import json5
import qrcode
import qrcode_terminal
import requests
from bs4 import BeautifulSoup
from requests_toolbelt.multipart.encoder import MultipartEncoder

from steampy.client import SteamClient
from utils.logger import handle_caught_exception
from utils.notifier import send_notification
from utils.static import BUFF_COOKIES_FILE_PATH
from utils.tools import get_encoding, logger


def parse_openid_params(response: str) -> Dict[str, str]:
    bs = BeautifulSoup(response, "html.parser")
    params_to_find = ["action", "openid.mode", "openidparams", "nonce"]
    input_form = bs.find("form", {"id": "openidForm"})
    params = {}
    for param in params_to_find:
        params[param] = input_form.find("input", {"name": param}).attrs["value"]  # type: ignore
    return params


def get_openid_params(steam_client: SteamClient, proxies=None):
    session = requests.Session()
    session.proxies = proxies
    response = requests.get("https://buff.163.com/account/login/steam?back_url=/", allow_redirects=False)
    location_url = response.headers["Location"]
    response = steam_client._session.get(location_url)
    return parse_openid_params(response.text), location_url, session


# Return the cookies of buff
def login_to_buff_by_steam(steam_client: SteamClient, proxies=None):
    params, location_url, session = get_openid_params(steam_client, proxies)
    multipart_data = MultipartEncoder(fields=params)
    headers = {
        'Content-Type': multipart_data.content_type,
        'Host': 'steamcommunity.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.70 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Referer': location_url,
    }
    response = steam_client._session.post("https://steamcommunity.com/openid/login", data=multipart_data, headers=headers, allow_redirects=False)
    while response.status_code == 302:
        response = session.get(response.headers["Location"], allow_redirects=False)
    # Test availability
    data = session.get("https://buff.163.com/account/api/steam/info").json()
    if data["code"] != "OK":
        return ""
    data = session.get(url="https://buff.163.com/account/api/login/status").json()["data"]
    if data["state"] == 2:
        return session.cookies.get_dict(domain="buff.163.com").get("session", "")
    else:
        return ""


def login_to_buff_by_qrcode(steam_client, proxies=None) -> str:
    session = requests.session()
    session.proxies = proxies
    response_json = session.get("https://buff.163.com/account/api/qr_code_login_open", params={"_": str(int(time.time() * 1000))}).json()
    if response_json["code"] != "OK":
        return ""
    qr_code_create_url = "https://buff.163.com/account/api/qr_code_create"
    response_json = session.post(qr_code_create_url, json={"code_type": 1, "extra_param": "{}"}).json()
    if response_json["code"] != "OK":
        logger.error("Failed to get QR code")
        return ""
    code_id = response_json["data"]["code_id"]
    qr_code_url = response_json["data"]["url"]
    qrcode_terminal.draw(qr_code_url)
    img = qrcode.make(qr_code_url)
    img.save("qrcode.png")  # type: ignore
    url = "https://api.cl2wm.cn/api/qrcode/code?text=" + qr_code_url
    send_notification(f"BUFF login expired! Please use your phone to open the following link to view the QR code, then scan it with BUFF to log in: {url}", "BUFF Login QR Code")
    logger.info("Please use your phone to scan the QR code above to log in to BUFF or open qrcode.png in the program directory to scan")
    status = 0
    scanned = False
    while status != 3:
        time.sleep(1)
        response_json = session.get("https://buff.163.com/account/api/qr_code_poll", params={"_": str(int(time.time() * 1000)), "item_id": code_id}).json()
        status = response_json["data"]["state"]
        if status == 4 or response_json["code"] != "OK":
            logger.error("QR code has expired")
            return ""
        if status == 2 and not scanned:
            scanned = True
            logger.info("Scan successful. Please confirm login on your phone (recommended: check 'stay logged in for 10 days')")
    response = session.post(
        "https://buff.163.com/account/api/qr_code_login",
        json={"item_id": code_id},
    )
    logger.debug(json5.dumps(response.json()))
    cookies = response.cookies.get_dict(domain="buff.163.com")
    if os.path.exists("qrcode.png"):
        try:
            os.remove("qrcode.png")
        except:
            pass
    send_notification("BUFF login successful!", "BUFF Login")
    return cookies["session"]


def is_session_has_enough_permission(session: str, proxies=None) -> bool:
    if not session.startswith("session="):
        session = "session=" + session
    try:
        response_json = requests.get("https://buff.163.com/api/market/steam_trade", headers={"Cookie": session}, proxies=proxies).json()
        if "data" not in response_json:
            return False
        return True
    except:
        return False


def get_valid_session_for_buff(steam_client: SteamClient, logger, proxies=None) -> str:
    logger.info("[BuffLoginSolver] Getting and checking BUFF session...")
    if proxies:
        logger.info("[BuffLoginSolver] Detected Steam proxy settings, applying same proxy to BUFF...")
    global session
    session = ""
    # Note: For multi-account support, we use a single buff_cookies.txt file
    # The original repo uses per-username files, but we keep single file for compatibility
    if not os.path.exists(BUFF_COOKIES_FILE_PATH):
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=")
    else:
        with open(BUFF_COOKIES_FILE_PATH, "r", encoding=get_encoding(BUFF_COOKIES_FILE_PATH)) as f:
            session = f.read().replace("\n", "")
        if session and session != "session=":
            logger.info("[BuffLoginSolver] Using cached session")
            logger.info("[BuffLoginSolver] Checking if session is valid...")
            if not is_session_has_enough_permission(session, proxies):
                logger.error("[BuffLoginSolver] Cached session is invalid")
                session = ""
            else:
                logger.info("[BuffLoginSolver] Cached session is valid")
        else:
            session = ""
    if not session:  # Try via Steam
        logger.info("[BuffLoginSolver] Trying to log in to BUFF via Steam...")
        try:
            got_cookies = login_to_buff_by_steam(steam_client, proxies)
            if is_session_has_enough_permission(got_cookies, proxies):
                logger.info("[BuffLoginSolver] Logged in to BUFF via Steam successfully")
                session = got_cookies
            else:
                logger.error("[BuffLoginSolver] Failed to log in to BUFF via Steam")

        except Exception as e:
            handle_caught_exception(e)
            logger.error("[BuffLoginSolver] Failed to log in to BUFF via Steam")

    if not session:  # Try via QR code
        logger.info("[BuffLoginSolver] Trying to log in to BUFF via QR code...")
        try:
            session = login_to_buff_by_qrcode(steam_client, proxies)
            if (not session) or (not is_session_has_enough_permission(session, proxies)):
                logger.error("[BuffLoginSolver] Failed to log in to BUFF via QR code")
            else:
                logger.info("[BuffLoginSolver] Logged in to BUFF via QR code successfully")
        except JSONDecodeError:
            logger.error("[BuffLoginSolver] Your server IP is blocked by BUFF. Try another server.")
            session = ""
    if not session:  # Unable to log in to BUFF
        logger.error("[BuffLoginSolver] Unable to log in to BUFF, please manually update BUFF cookies!")
        send_notification("Unable to log in to BUFF, please manually update BUFF cookies!", "BUFF Login Failed")
    else:
        with open(BUFF_COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("session=" + session.replace("session=", ""))
    if "session=" not in session:
        session = "session=" + session
    return session


def get_buff_username(session) -> str:
    if "session=" not in session:
        session = "session=" + session
    response_json = requests.get("https://buff.163.com/account/api/user/info", headers={"Cookie": session}).json()
    if response_json["code"] == "OK":
        if "data" in response_json:
            if "nickname" in response_json["data"]:
                return response_json["data"]["nickname"]
    return ""
