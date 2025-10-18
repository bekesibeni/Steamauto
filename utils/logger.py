import datetime
import logging
import os
import platform
import re
import sys

import colorlog
import json5
import requests
from requests.exceptions import ConnectionError, ReadTimeout

import utils.static as static
from steampy.exceptions import (
    ApiException,
    ConfirmationExpected,
    EmptyResponse,
    InvalidCredentials,
    InvalidResponse,
    SteamError,
)
from utils.static import (
    BUILD_INFO,
    CONFIG_FILE_PATH,
    CURRENT_VERSION,
    LOGS_FOLDER,
    STEAM_ERROR_CODES,
)

sensitive_data = []
sensitive_keys = ["ApiKey", "TradeLink", "JoinTime", "NickName", "access_token", "trade_url", "TransactionUrl", "RealName", "IdCard"]

if not os.path.exists(LOGS_FOLDER):
    os.mkdir(LOGS_FOLDER)

class LogFilter(logging.Filter):
    @staticmethod
    def add_sensitive_data(data):
        sensitive_data.append(data)

    def filter(self, record):
        if not isinstance(record.msg, str):
            return True
        for sensitive in sensitive_data:
            record.msg = record.msg.replace(sensitive, "*" * len(sensitive))

        def mask_value(value):
            return "*" * len(value)

        # Mask sensitive JSON fields
        for key in sensitive_keys:
            pattern = rf'"{key}"\s*:\s*("(.*?)"|(\d+)|(true|false|null))'

            def replace_match(match):
                if match.group(2):
                    return f'"{key}": "{mask_value(match.group(2))}"'
                elif match.group(3):
                    return f'"{key}": {mask_value(match.group(3))}'
                elif match.group(4):
                    return f'"{key}": {mask_value(match.group(4))}'

            record.msg = re.sub(pattern, replace_match, record.msg, flags=re.IGNORECASE)  # type: ignore

        # Mask sensitive URL params
        for key in sensitive_keys:
            pattern = rf"({key}=)([^&\s]+)"

            def replace_url_match(match):
                return f"{match.group(1)}{mask_value(match.group(2))}"

            record.msg = re.sub(pattern, replace_url_match, record.msg, flags=re.IGNORECASE)

        return True

log_retention_days = None
log_level = None
try:
    with open(CONFIG_FILE_PATH, "r", encoding='utf-8') as f:
        config = json5.loads(f.read())
        if isinstance(config, dict):
            log_level = str(config.get("log_level", "DEBUG")).upper()
            log_retention_days = int(config.get("log_retention_days", 7))
except Exception:
    pass

if log_retention_days:
    for log_file in os.listdir(LOGS_FOLDER):
        if log_file.endswith(".log"):
            log_file_path = os.path.join(LOGS_FOLDER, log_file)
            if (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(log_file_path))) > datetime.timedelta(days=log_retention_days):
                os.remove(log_file_path)

logger = logging.getLogger()
logger.setLevel(0)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.INFO)
log_formatter_colored = colorlog.ColoredFormatter(
    fmt="%(log_color)s[%(asctime)s] - %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red"},
)
s_handler.setFormatter(log_formatter_colored)
log_formatter = logging.Formatter("[%(asctime)s] - %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")
logger.addHandler(s_handler)
f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + ".log"), encoding="utf-8")
if log_level and log_level.isdigit():
    f_handler.setLevel(int(log_level))
elif log_level == "INFO":
    f_handler.setLevel(logging.INFO)
elif log_level == "WARNING":
    f_handler.setLevel(logging.WARNING)
elif log_level == "ERROR":
    f_handler.setLevel(logging.ERROR)
else:
    f_handler.setLevel(logging.DEBUG)
f_handler.setFormatter(log_formatter)
logger.addHandler(f_handler)
logger.addFilter(LogFilter())
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("apprise").setLevel(logging.WARNING)
logger.debug(f"Steamauto {CURRENT_VERSION} started")
logger.debug(f"Running on {platform.system()} {platform.release()}({platform.version()})")
logger.debug(f"Python version: {os.sys.version}")  # type: ignore
logger.debug(f"Build info: {BUILD_INFO}")
logger.debug("Logs are sanitized. Safe to share publicly.")

def handle_caught_exception(e: Exception, prefix: str = "", known: bool = False):
    plogger = logger
    if prefix and not prefix.endswith(" "):
        plogger = PluginLogger(prefix)
    if (not static.is_latest_version) and not known:
        plogger.warning("Your Steamauto version may be outdated. Update to the latest version and try again.")
    logger.debug(e, exc_info=True)

    if isinstance(e, KeyboardInterrupt):
        plogger.info("KeyboardInterrupt detected. Exiting...")
        exit(0)
    elif isinstance(e, SystemExit):
        plogger.info("System exit requested. Exiting...")
        exit(0)
    elif isinstance(e, requests.exceptions.SSLError):
        plogger.error("Proxy/VPN TLS issue. Change your proxy/VPN.")
    elif isinstance(e, EmptyResponse):
        plogger.error("Steam returned an empty response. Your IP may be limited. Change IP or try later.")
    elif isinstance(e, requests.exceptions.ProxyError):
        plogger.error("Proxy error. Disable the proxy. If Steam is hard to reach, enable only the Steam proxy in config.")
    elif isinstance(e, (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError, ReadTimeout, InvalidResponse)):
        plogger.error("Network error. Check your connection.")
        plogger.error("This may be caused by a proxy or VPN. The app can run without any proxy or VPN.")
        plogger.error("If you use a proxy or VPN, disable it and restart the app.")
        plogger.error("If you do not use a proxy or VPN, check your network.")
    elif isinstance(e, InvalidCredentials):
        plogger.error("Invalid mafile. Verify it, especially identity_secret.")
        plogger.error(str(e))
    elif isinstance(e, ConfirmationExpected):
        plogger.error("Steam session expired. Delete the session folder and restart Steamauto.")
    elif isinstance(e, SystemError):
        plogger.error("Cannot connect to Steam. Check account status, network, or restart Steamauto.")
    elif isinstance(e, SteamError):
        plogger.error("Steam error, id:" + str(e.error_code) + ", message:" + STEAM_ERROR_CODES.get(e.error_code, "Unknown Steam error"))
    elif isinstance(e, ApiException):
        if 'Invalid trade offer state' in str(e):
            if 'Canceled' in str(e):
                plogger.error("Trade canceled. Cannot accept offer.")
            elif 'Accepted' in str(e):
                plogger.error("Trade already accepted. Cannot repeat.")
            else:
                plogger.error("Invalid trade state. Cannot accept offer. Details: " + str(e))
        else:
            plogger.error("Steam API error. Details: " + str(e))
    else:
        if not known:
            plogger.error(
                f"Steamauto version: {CURRENT_VERSION}\nPython: {os.sys.version}\nSystem: {platform.system()} {platform.release()}({platform.version()})\nBuild: {BUILD_INFO}\n"  # type: ignore
            )
            plogger.error("Unknown exception. Message: " + str(e) + ", Type: " + str(type(e)) + ". Please report to the developer. A screenshot is not helpful. Include the log file.")
        if BUILD_INFO == 'Running from source':
            plogger.error(e, exc_info=True)


class PluginLogger:
    def __init__(self, pluginName):
        if '[' and ']' not in pluginName:
            self.pluginName = f'[{pluginName}]'
        else:
            self.pluginName = pluginName

    def debug(self, msg, *args, **kwargs):
        logger.debug(f"{self.pluginName} {msg}", *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        logger.info(f"{self.pluginName} {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        logger.warning(f"{self.pluginName} {msg}", *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        logger.error(f"{self.pluginName} {msg}", *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        logger.critical(f"{self.pluginName} {msg}", *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        logger.log(level, f"{self.pluginName} {msg}", *args, **kwargs)
