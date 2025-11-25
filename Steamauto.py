import importlib
import importlib.util
import inspect
import os
import re
import shutil
import signal
import sys
import threading
import time
from typing import no_type_check

import json5
from colorama import Fore, Style

import utils.static as static
from steampy.client import SteamClient
# Auto-update functionality removed
from utils.logger import handle_caught_exception, logger
from utils.notifier import send_notification
# Old version patches removed (auto-update disabled)
from utils.static import (BUILD_INFO, CONFIG_FILE_PATH, CONFIG_FOLDER,
                          CURRENT_VERSION, DEFAULT_CONFIG_JSON,
                          DEFAULT_STEAM_ACCOUNT_JSON, INTERNAL_PLUGINS,
                          PLUGIN_FOLDER, SESSION_FOLDER,
                          STEAM_ACCOUNT_INFO_FILE_PATH)
from utils.steam_client import login_to_steam, steam_client_mutex
from utils.multi_account_manager import initialize_multi_account_manager, get_multi_account_manager
from utils.tools import (calculate_sha256, exit_code, get_encoding, jobHandler,
                         pause)


def handle_global_exception(exc_type, exc_value, exc_traceback):
    logger.exception(
        "A fatal error occurred. Screenshot this screen and submit the latest log file at https://github.com/jiajiaxd/Steamauto/issues",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    logger.error("Exiting due to a fatal error...")
    try:
        pause()
    except Exception:
        # Handle cases where pause() also fails in non-interactive environments
        logger.info("Program exits")


def set_exit_code(code):
    global exit_code
    exit_code = code


# Return 0 for missing/invalid files, 1 for first run, 2 for non-first run
def init_files_and_params() -> int:
    global config
    # patch() removed - auto-update disabled
    logger.info("Welcome to the Steamauto GitHub repo: https://github.com/Steamauto/Steamauto")
    logger.info("If Steamauto helps, please star the repo. Thanks!\n")
    logger.info(f"{Fore.RED+Style.BRIGHT}!!! This program is {Fore.YELLOW}free and open source{Fore.RED}. If anyone sells it to you, complain and request a refund. !!!\n")
    logger.info(f"Current version: {CURRENT_VERSION}   Build info: {BUILD_INFO}")
    try:
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            config = json5.load(f)
    except:
        config = {}
    # Auto-update functionality completely disabled
    try:
        from utils import cloud_service
        cloud_service.checkVersion()
        cloud_service.getAds()
    except Exception as e:
        logger.warning('Cloud service unavailable')
    logger.info("Initializing...")
    first_run = False
    if not os.path.exists(CONFIG_FOLDER):
        os.mkdir(CONFIG_FOLDER)
    if not os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_JSON)
        logger.info("First run detected. Generated " + CONFIG_FILE_PATH + ". Fill it following the README.")
        first_run = True
    else:
        with open(CONFIG_FILE_PATH, "r", encoding=get_encoding(CONFIG_FILE_PATH)) as f:
            try:
                config = json5.load(f)
            except Exception as e:
                handle_caught_exception(e, known=True)
                logger.error("Invalid " + CONFIG_FILE_PATH + " format. Check your config.")
                return 0
    if not os.path.exists(STEAM_ACCOUNT_INFO_FILE_PATH):
        with open(STEAM_ACCOUNT_INFO_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_STEAM_ACCOUNT_JSON)
            logger.info("First run detected. Generated " + STEAM_ACCOUNT_INFO_FILE_PATH + ". Fill it following the README.")
            first_run = True

    if not first_run:
        if "no_pause" in config:
            static.no_pause = config["no_pause"]
        if "steam_login_ignore_ssl_error" not in config:
            config["steam_login_ignore_ssl_error"] = False
        if "steam_local_accelerate" not in config:
            config["steam_local_accelerate"] = False

    if first_run:
        return 1
    else:
        return 2


@no_type_check
def get_plugins_folder():
    base_path = os.path.dirname(os.path.abspath(__file__))
    if hasattr(sys, '_MEIPASS'):
        base_path = os.path.dirname(sys.executable)
        if not os.path.exists(os.path.join(base_path, PLUGIN_FOLDER)):
            shutil.copytree(os.path.join(sys._MEIPASS, PLUGIN_FOLDER), os.path.join(base_path, PLUGIN_FOLDER))
        else:
            plugins = os.listdir(os.path.join(sys._MEIPASS, PLUGIN_FOLDER))
            for plugin in plugins:
                plugin_absolute = os.path.join(sys._MEIPASS, PLUGIN_FOLDER, plugin)
                local_plugin_absolute = os.path.join(base_path, PLUGIN_FOLDER, plugin)
                if os.path.isdir(plugin_absolute):
                    continue
                if os.path.isdir(local_plugin_absolute):
                    continue
                if not os.path.exists(local_plugin_absolute):
                    shutil.copy(plugin_absolute, local_plugin_absolute)
                else:
                    local_plugin_sha256 = calculate_sha256(local_plugin_absolute)
                    plugin_sha256 = calculate_sha256(plugin_absolute)
                    if local_plugin_sha256 != plugin_sha256:
                        if plugin not in config.get('plugin_whitelist', []):
                            logger.info('Detected update for plugin ' + plugin + '. Auto-updated. Add it to plugin_whitelist to skip updates.')
                            shutil.copy(plugin_absolute, local_plugin_absolute)
                        else:
                            logger.info('Plugin ' + plugin + ' differs from bundled version. It is whitelisted, so it will not be auto-updated.')
    return os.path.join(base_path, PLUGIN_FOLDER)


def import_module_from_file(module_name, file_path):
    """
    Dynamically import a module from a given file path.

    Args:
        module_name (str): Unique name for the module in the current environment.
        file_path (str): Path to the module file.

    Returns:
        module: The imported module object.
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            raise ImportError(f"Failed to create module spec from '{file_path}'")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        sys.modules[module_name] = module
        return module
    except Exception as e:
        handle_caught_exception(e, known=True)
        logger.error(f"Error importing module '{module_name}'")
        return None


def import_all_plugins():
    # Auto import all plugins
    plugin_files = [f for f in os.listdir(get_plugins_folder()) if f.endswith(".py") and f != "__init__.py"]

    for plugin_file in plugin_files:
        module_name = f"{PLUGIN_FOLDER}.{plugin_file[:-3]}"
        import_module_from_file(module_name, os.path.join(get_plugins_folder(), plugin_file))


def camel_to_snake(name):
    if name == "ECOsteamPlugin":  # special case
        return "ecosteam"
    if name == "ECOsteam":  # special case
        return "ecosteam"
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def get_plugin_classes():
    plugin_classes = {}
    for name, obj in sys.modules.items():
        if name.startswith(f"{PLUGIN_FOLDER}.") and name != f"{PLUGIN_FOLDER}.__init__":  # noqa: E501
            plugin_name = name.replace(f"{PLUGIN_FOLDER}.", '')
            plugin_name = camel_to_snake(plugin_name)
            plugin_classes[plugin_name] = obj
    # Returned structure:
    # {
    #     "[plugin_name]": [plugin_class],
    #     ...
    # }
    return plugin_classes


def get_plugins_enabled(steam_client: SteamClient, steam_client_mutex):
    global config
    plugins_enabled = []
    plugin_modules = get_plugin_classes()  # get all plugin classes

    for plugin_key, plugin_module in plugin_modules.items():
        # enabled if exists in config and enable==True,
        # or not in config and not an internal plugin (i.e., user plugin)
        if (plugin_key in config and config[plugin_key].get("enable")) or ((plugin_key not in config) and (plugin_key not in INTERNAL_PLUGINS)):
            if plugin_key not in config:
                logger.info(f'Loaded custom plugin {plugin_key}')
            # iterate classes in the module
            for cls_name, cls_obj in inspect.getmembers(plugin_module, inspect.isclass):
                # build kwargs based on constructor signature
                init_signature = inspect.signature(cls_obj.__init__)
                init_kwargs = {}
                unknown_class = False

                for param_name, param in init_signature.parameters.items():
                    if param_name == "logger":
                        init_kwargs[param_name] = logger
                    elif param_name == "steam_client":
                        init_kwargs[param_name] = steam_client
                    elif param_name == "steam_client_mutex":
                        init_kwargs[param_name] = steam_client_mutex
                    elif param_name == "config":
                        init_kwargs[param_name] = config
                    elif param_name == "self":
                        continue
                    else:
                        unknown_class = True
                        break
                if unknown_class:
                    continue

                # must have init(self) with no extra params
                if not hasattr(cls_obj, "init"):
                    continue
                init_signature = inspect.signature(cls_obj.init)
                if len(init_signature.parameters) != 1:
                    continue
                plugin_instance = cls_obj(**init_kwargs)
                plugins_enabled.append(plugin_instance)

    return plugins_enabled


def plugins_check(plugins_enabled):
    if len(plugins_enabled) == 0:
        logger.error("No plugins enabled. Check " + CONFIG_FILE_PATH + ".")
        return 2
    for plugin in plugins_enabled:
        if plugin.init():
            return 0
    return 1


def get_steam_client_mutexs(num):
    steam_client_mutexs = []
    for i in range(num):
        steam_client_mutexs.append(threading.Lock())
    return steam_client_mutexs


def init_plugins_and_start(steam_client, steam_client_mutex):
    plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex)
    logger.info("Initialization done. Starting plugins!")
    print("\n")
    time.sleep(0.1)
    if len(plugins_enabled) == 1:
        exit_code.set(plugins_enabled[0].exec())
    else:
        threads = []
        for plugin in plugins_enabled:
            threads.append(threading.Thread(target=plugin.exec))
        for thread in threads:
            thread.daemon = True
            thread.start()
        # Use timeout-based join to allow Ctrl+C to work
        while any(thread.is_alive() for thread in threads):
            if should_exit:
                break
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=0.1)
                    break
    if exit_code.get() != 0:
        logger.warning("All plugins have exited. This is abnormal. Check your config.")


tried_exit = False
should_exit = False


def exit_app(signal_, frame):
    global tried_exit, should_exit
    should_exit = True
    if not tried_exit:
        tried_exit = True
        jobHandler.terminate_all()
        os._exit(exit_code.get())
    else:
        pid = os.getpid()
        os.kill(pid, signal.SIGTERM)


# Main
def main():
    global config
    # init
    init_status = init_files_and_params()
    if init_status == 0:
        pause()
        return 1
    elif init_status == 1:
        pause()
        return 0

    # Initialize multi-account manager
    if not initialize_multi_account_manager(config):
        send_notification('Failed to initialize multi-account manager. Program will stop.')
        pause()
        return 1
        
    multi_account_manager = get_multi_account_manager()
    if not multi_account_manager:
        send_notification('Multi-account manager not available. Program will stop.')
        pause()
        return 1
        
    # For backward compatibility, set the first account as the primary client
    all_clients = multi_account_manager.get_all_clients()
    if not all_clients:
        send_notification('No Steam accounts successfully logged in. Program will stop.')
        pause()
        return 1
        
    # Use the first available client as the primary (for plugins that still expect single client)
    steam_client = list(all_clients.values())[0]
    static.STEAM_ACCOUNT_NAME = steam_client.username
    static.STEAM_64_ID = steam_client.get_steam64id_from_cookies()
    # only to discover enabled plugins
    import_all_plugins()
    plugins_enabled = get_plugins_enabled(steam_client, steam_client_mutex)
    # verify plugin init
    plugins_check_status = plugins_check(plugins_enabled)
    if plugins_check_status == 0:
        logger.info("Some plugins failed to initialize. Steamauto will exit.")
        pause()
        return 1

    if steam_client is not None:
        send_notification('Steamauto logged into Steam and started running')
        init_plugins_and_start(steam_client, steam_client_mutex)

    logger.info("All plugins have stopped. Exiting...")
    pause()
    return 1


# Entry
if __name__ == "__main__":
    sys.excepthook = handle_global_exception
    signal.signal(signal.SIGINT, exit_app)
    if not os.path.exists(SESSION_FOLDER):
        os.mkdir(SESSION_FOLDER)
    try:
        exit_code.set(main())  # type: ignore
    except KeyboardInterrupt:
        should_exit = True
        jobHandler.terminate_all()
    exit_app(None, None)
