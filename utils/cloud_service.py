import os
import platform
import signal
import sys
import threading
import time
from urllib import response
import uuid

import requests
from colorama import Fore, Style

import utils.static as static
from utils.logger import PluginLogger, handle_caught_exception
from utils.notifier import send_notification
from utils.tools import calculate_sha256, pause

logger = PluginLogger('CloudService')


def get_platform_info():
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        if machine == "amd64":
            return "windows x64"
        else:
            return f"windows {machine}"
    elif system == "linux":
        if machine == "x86_64":
            return "linux x64"
        else:
            return f"linux {machine}"
    elif system == "darwin":
        if machine == "x86_64" or machine == "arm64":
            return "mac x64" if machine == "x86_64" else "mac arm64"
        else:
            return f"mac {machine}"
    else:
        return f"{system} {machine}"


def get_user_uuid():
    app_dir = os.path.expanduser("~/.steamauto")
    uuid_file = os.path.join(app_dir, "uuid.txt")
    if not os.path.exists(app_dir):
        os.makedirs(app_dir)
    if not os.path.exists(uuid_file):
        with open(uuid_file, "w") as f:
            user_uuid = str(uuid.uuid4())
            f.write(user_uuid)
    else:
        with open(uuid_file, "r") as f:
            user_uuid = f.read().strip()
    return user_uuid


session = requests.Session()
session.headers.update({'User-Agent': f'Steamauto {static.CURRENT_VERSION} ({get_platform_info()}) {get_user_uuid()}'})


def compare_version(ver1, ver2):
    version1_parts = ver1.split(".")
    version2_parts = ver2.split(".")

    for i in range(max(len(version1_parts), len(version2_parts))):
        v1 = int(version1_parts[i]) if i < len(version1_parts) else 0
        v2 = int(version2_parts[i]) if i < len(version2_parts) else 0

        if v1 < v2:
            return -1
        elif v1 > v2:
            return 1

    return 0


def get_uu_uk_from_cloud():
    logger.debug('Attempting to fetch UK from cloud...')
    for i in range(3):
        try:
            response = session.get('https://steamauto.jiajiaxd.com/tools/getUUuk', timeout=5)
            response.raise_for_status()
            logger.debug('Server response: %s', response.text)
            data = response.json()
            return data['uk']
        except Exception as e:
            logger.warning('Cloud service error. Unable to get UK. Some features may not work properly')
            handle_caught_exception(e, known=True)
    return ''


def parseBroadcastMessage(message):
    message = message.replace('<red>', Fore.RED)
    message = message.replace('<green>', Fore.GREEN)
    message = message.replace('<yellow>', Fore.YELLOW)
    message = message.replace('<blue>', Fore.BLUE)
    message = message.replace('<magenta>', Fore.MAGENTA)
    message = message.replace('<cyan>', Fore.CYAN)
    message = message.replace('<white>', Fore.WHITE)
    message = message.replace('<reset>', Style.RESET_ALL)
    message = message.replace('<bold>', Style.BRIGHT)
    message = message.replace('<br>', '\n')
    return message


def autoUpdate(downloadUrl, sha256=''):
    import tqdm

    try:
        with session.get(downloadUrl, stream=True, timeout=30) as response:
            response.raise_for_status()

            # Determine filename
            content_disposition = response.headers.get('Content-Disposition')
            if content_disposition and 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[1].strip('\"')
            else:
                # Fallback to URL basename
                filename = downloadUrl.split('/')[-1]
                if not filename.endswith('.exe'):
                    filename += '.exe'  # ensure executable on Windows

            total_size = int(response.headers.get('Content-Length', 0))

            # Download new executable
            with open(filename, 'wb') as file, tqdm.tqdm(
                desc=f'Downloading {filename}',
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                miniters=1,
                dynamic_ncols=True,
            ) as bar:
                for data in response.iter_content(chunk_size=1024):
                    if not data:
                        break
                    file.write(data)
                    bar.update(len(data))
            logger.info('Download complete: %s', filename)
    except Exception as e:
        logger.error('Download failed')
        return False

    if sha256:
        logger.info('Verifying file...')
        if calculate_sha256(filename) != sha256:
            logger.error('Checksum failed. Update aborted')
            return False
        logger.info('Checksum OK')

    # Create update.txt with old executable path so the updater can delete it
    with open('update.txt', 'w') as f:
        f.write(sys.executable)
    os.startfile(filename)  # type: ignore
    os._exit(0)
    sys.exit(0)
    pid = os.getpid()
    os.kill(pid, signal.SIGTERM)

    return True


def getAds():
    # Advertisements disabled - no ads will be displayed
    return True


def checkVersion():
    # Auto-update functionality completely disabled
    static.is_latest_version = True
    logger.info('Auto-update disabled - running current version')
    return True


def adsThread():
    # Advertisements thread disabled
    pass


def versionThread():
    # Auto-update thread disabled
    pass


# Auto-update and advertisement threads completely disabled
# ad = threading.Thread(target=adsThread)
# update = threading.Thread(target=versionThread)
# ad.start()
# update.start()
