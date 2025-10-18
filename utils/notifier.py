import os

import apprise
import json5

from utils import static
from utils.logger import PluginLogger, handle_caught_exception
from utils.static import CONFIG_FILE_PATH
from utils.tools import get_encoding

logger = PluginLogger('Notifier')
config = {}
try:
    if os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, 'r', encoding=get_encoding(CONFIG_FILE_PATH)) as file:
            config = json5.load(file)
        config = config.get('notify_service', {})
        if config == {}:
            logger.warning('Notification service not configured. Notifications will be unavailable. Configure it in the config file.')
        elif config.get('notifiers'):
            logger.info(f'Configured {len(config.get("notifiers"))} notifier(s).')
except Exception as e:
    logger.warning('Notification service error. Check your config file.')
    handle_caught_exception(e)
    pass


def send_notification(message, title=''):
    if config.get('notifiers', False):
        for black in config.get('blacklist_words', []):
            if black in message or black in title:
                logger.debug(f'Blacklisted word found: {black}. Message filtered.')
                return
        for notifier in config.get('notifiers', []):
            try:
                title = title if title else 'Steamauto Notification'
                if config.get('custom_title'):
                    message = f'{title}\n{message}'
                    title = config.get('custom_title')
                if config.get('include_steam_info', False):
                    message += f'\nSteam username: {static.STEAM_ACCOUNT_NAME}\nSteam ID: {static.STEAM_64_ID}'
                apobj = apprise.Apprise()
                apobj.add(notifier)
                apobj.notify(title=title, body=message)  # type: ignore
            except Exception as e:
                handle_caught_exception(e)
                logger.error(f'Failed to send notification: {str(e)}')
