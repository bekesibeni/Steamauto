import os
import time
from traceback import print_exc

from colorama import Fore, Style

import uuyoupinapi
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import UU_TOKEN_FILE_PATH
from utils.tools import get_encoding

logger = PluginLogger("UULoginSolver")


def get_valid_token_for_uu():
    logger.info("Acquiring a valid UU Youpin token...")
    if os.path.exists(UU_TOKEN_FILE_PATH):
        with open(UU_TOKEN_FILE_PATH, "r", encoding=get_encoding(UU_TOKEN_FILE_PATH)) as f:
            try:
                token = f.read().strip()
                uuyoupin = uuyoupinapi.UUAccount(token)
                logger.info("UU Youpin login successful, username: " + uuyoupin.get_user_nickname())
                return token
            except Exception as e:
                print_exc()
                logger.warning("Cached UU Youpin token is invalid")
    else:
        logger.info("No stored UU token detected")
    logger.info("Re-logging into UU Youpin now.")
    token = str(get_token_automatically())
    try:
        uuyoupin = uuyoupinapi.UUAccount(token)
        logger.info("UU Youpin login successful, username: " + uuyoupin.get_user_nickname())
        with open(UU_TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(token)
        logger.info("UU Youpin token cached locally")
        return token
    except TypeError:
        logger.error('Failed to get token. The code may be wrong or the SMS was not sent.')
        return False
    except Exception as e:
        handle_caught_exception(e, "[UULoginSolver]")
        return False


def get_token_automatically():
    """
    Guide user to enter phone number, send SMS code, input code, auto login, and return token.
    :return: token
    """
    device_info = uuyoupinapi.generate_random_string(10)
    headers = uuyoupinapi.generate_headers(device_info, device_info)

    phone_number = input(f"{Style.BRIGHT+Fore.RED}Enter phone number (+86). Ignore other plugin output. Press Enter after input: {Style.RESET_ALL}")
    token_id = device_info
    logger.debug("Random token_id: " + token_id)
    uk = ''
    try:
        from utils.cloud_service import get_uu_uk_from_cloud

        uk = get_uu_uk_from_cloud()
    except Exception:
        logger.warning("Cloud service unavailable. Cannot get UK. Using defaults.")
        pass
    result = uuyoupinapi.UUAccount.send_login_sms_code(phone_number, token_id, headers=headers, uk=uk)
    response = {}
    if '成功' in result.get('Msg', ''):
        logger.info("SMS send result: " + result["Msg"])
        sms_code = input(f"{Style.BRIGHT+Fore.RED}Enter SMS code. Ignore other plugin output. Press Enter after input: {Style.RESET_ALL}")
        response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, sms_code, token_id, headers=headers)
    else:
        logger.info("This phone requires manual SMS verification. Fetching info...")
        result = uuyoupinapi.UUAccount.get_smsUpSignInConfig(headers).json()
        if result["Code"] == 0:
            logger.info("Request result: " + result["Msg"])
            logger.info(
                f"{Style.BRIGHT+Fore.RED}Send SMS {Fore.YELLOW+result['Data']['SmsUpContent']} {Fore.RED}to {Fore.YELLOW+result['Data']['SmsUpNumber']} {Fore.RED}. "
                f"(Ignore other plugin output.) After sending, press Enter.{Style.RESET_ALL}",
            )
            input()
            logger.info("Please wait...")
            time.sleep(3)  # allow for SMS delay
            response = uuyoupinapi.UUAccount.sms_sign_in(phone_number, "", token_id, headers=headers)
    logger.info("Login result: " + response["Msg"])
    try:
        got_token = response["Data"]["Token"]
    except (KeyError, TypeError, AttributeError):
        return False
    return got_token
