import json5
import threading
import time
from typing import Dict, Optional, List
from steampy.client import SteamClient

import utils.static as static
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import login_to_steam_single_account
from utils.tools import get_encoding

logger = PluginLogger('MultiAccountManager')


class MultiAccountManager:
    """
    Manages multiple Steam accounts for BUFF multi-account support.
    Each account is identified by its steamid and can handle delivery offers.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.accounts = {}
        self.steam_clients = {}
        self.account_by_steamid = {}
        self.login_mutex = threading.Lock()
        self.is_initialized = False
        
    def load_accounts_from_config(self) -> bool:
        """Load account configuration from steam_account_info.json5"""
        try:
            with open(static.STEAM_ACCOUNT_INFO_FILE_PATH, "r", encoding=get_encoding(static.STEAM_ACCOUNT_INFO_FILE_PATH)) as f:
                account_config = json5.loads(f.read())
        except FileNotFoundError:
            logger.error(f"Missing {static.STEAM_ACCOUNT_INFO_FILE_PATH}. Add it first.")
            return False
        except Exception as e:
            logger.error(f"Invalid format in {static.STEAM_ACCOUNT_INFO_FILE_PATH}. Check config file.")
            handle_caught_exception(e, known=True)
            return False
            
        # Check if it's the new multi-account format
        if "accounts" in account_config:
            # New multi-account format
            accounts_list = account_config.get("accounts", [])
            max_accounts = account_config.get("max_accounts", 5)
            
            if len(accounts_list) > max_accounts:
                logger.error(f"Too many accounts configured. Maximum allowed: {max_accounts}")
                return False
                
            for i, account in enumerate(accounts_list):
                if not self._validate_account_config(account, i):
                    return False
                self.accounts[account['steamid']] = account
                self.account_by_steamid[account['steamid']] = account
                
        else:
            # Legacy single-account format - convert to multi-account
            logger.info("Detected legacy single-account format. Converting to multi-account format...")
            if not self._validate_legacy_account_config(account_config):
                return False
                
            # Convert to multi-account format
            legacy_account = {
                "name": "Main Account",
                "steam_username": account_config.get("steam_username", ""),
                "steam_password": account_config.get("steam_password", ""),
                "shared_secret": account_config.get("shared_secret", ""),
                "identity_secret": account_config.get("identity_secret", ""),
                "steamid": "",  # Will be filled after login
                "enabled": True
            }
            
            # We'll need to login first to get the steamid
            temp_config = {"use_proxies": self.config.get("use_proxies", False)}
            temp_client = login_to_steam_single_account(legacy_account, temp_config)
            if temp_client:
                legacy_account["steamid"] = str(temp_client.get_steam64id_from_cookies())
                self.accounts[legacy_account["steamid"]] = legacy_account
                self.account_by_steamid[legacy_account["steamid"]] = legacy_account
                self.steam_clients[legacy_account["steamid"]] = temp_client
                logger.info(f"Legacy account converted. SteamID: {legacy_account['steamid']}")
            else:
                logger.error("Failed to login legacy account")
                return False
                
        logger.info(f"Loaded {len(self.accounts)} account(s) from configuration")
        return True
        
    def _validate_account_config(self, account: dict, index: int) -> bool:
        """Validate individual account configuration"""
        required_fields = ["name", "steam_username", "steam_password", "shared_secret", "identity_secret", "steamid", "enabled"]
        
        for field in required_fields:
            if field not in account:
                logger.error(f"Account {index}: Missing required field '{field}'")
                return False
                
        if not account["steam_username"] or not account["steam_password"]:
            logger.error(f"Account {index} ({account['name']}): Username or password is empty")
            return False
            
        if not account["steamid"]:
            logger.error(f"Account {index} ({account['name']}): SteamID is empty")
            return False
            
        return True
        
    def _validate_legacy_account_config(self, account_config: dict) -> bool:
        """Validate legacy single-account configuration"""
        required_fields = ["steam_username", "steam_password", "shared_secret", "identity_secret"]
        
        for field in required_fields:
            if field not in account_config or not account_config[field]:
                logger.error(f"Legacy account: Missing or empty field '{field}'")
                return False
                
        return True
        
    def login_all_accounts(self) -> bool:
        """Login all enabled accounts simultaneously"""
        if self.is_initialized:
            return True
            
        with self.login_mutex:
            if self.is_initialized:
                return True
                
            logger.info("Logging in to all enabled Steam accounts...")
            success_count = 0
            
            for steamid, account in self.accounts.items():
                if not account.get("enabled", True):
                    logger.info(f"Skipping disabled account: {account['name']}")
                    continue
                    
                try:
                    logger.info(f"Logging in to account: {account['name']} (SteamID: {steamid})")
                    client = login_to_steam_single_account(account, self.config)
                    
                    if client and client.is_session_alive():
                        # Get the actual Steam ID from the client
                        actual_steamid = client.get_steam64id_from_cookies()
                        
                        # Store the client with the actual Steam ID as the key
                        self.steam_clients[actual_steamid] = client
                        success_count += 1
                        logger.info(f"Successfully logged in to {account['name']} (SteamID: {actual_steamid})")
                    else:
                        logger.error(f"Failed to login to {account['name']}")
                        
                except Exception as e:
                    logger.error(f"Error logging in to {account['name']}: {str(e)}")
                    handle_caught_exception(e, known=True)
                    
            if success_count == 0:
                logger.error("No accounts successfully logged in")
                return False
                
            logger.info(f"Successfully logged in to {success_count}/{len([a for a in self.accounts.values() if a.get('enabled', True)])} accounts")
            self.is_initialized = True
            return True
            
    def get_client_for_steamid(self, steamid: str) -> Optional[SteamClient]:
        """Get the SteamClient for the specified steamid"""
        steamid_str = str(steamid)
        
        # Instead of relying on keys, check all clients and find the one with matching steamid
        for stored_steamid, client in self.steam_clients.items():
            if client and client.is_session_alive():
                actual_steamid = client.get_steam64id_from_cookies()
                if actual_steamid == steamid_str:
                    return client
        
        logger.warning(f"No Steam client found for SteamID: {steamid_str}")
        return None
        
    def get_all_clients(self) -> Dict[str, SteamClient]:
        """Get all active Steam clients"""
        return {steamid: client for steamid, client in self.steam_clients.items() 
                if client and client.is_session_alive()}
                
    def get_account_info(self, steamid: str) -> Optional[dict]:
        """Get account information for the specified steamid"""
        return self.account_by_steamid.get(str(steamid))
        
    def get_all_accounts(self) -> List[dict]:
        """Get all account information"""
        return list(self.accounts.values())
        
    def refresh_account_sessions(self):
        """Refresh sessions for all accounts that need it"""
        for steamid, client in self.steam_clients.items():
            if client and not client.is_session_alive():
                account = self.account_by_steamid.get(steamid)
                if account:
                    logger.info(f"Refreshing session for {account['name']}")
                    try:
                        new_client = login_to_steam_single_account(account, self.config)
                        if new_client and new_client.is_session_alive():
                            self.steam_clients[steamid] = new_client
                            logger.info(f"Session refreshed for {account['name']}")
                    except Exception as e:
                        logger.error(f"Failed to refresh session for {account['name']}: {str(e)}")
                        handle_caught_exception(e, known=True)
                        
    def shutdown(self):
        """Shutdown all Steam clients"""
        logger.info("Shutting down all Steam clients...")
        for steamid, client in self.steam_clients.items():
            if client:
                try:
                    # SteamClient doesn't have explicit logout, just clear references
                    pass
                except Exception as e:
                    logger.warning(f"Error shutting down client for {steamid}: {str(e)}")
                    
        self.steam_clients.clear()
        self.is_initialized = False


# Global instance
multi_account_manager: Optional[MultiAccountManager] = None


def get_multi_account_manager() -> Optional[MultiAccountManager]:
    """Get the global multi-account manager instance"""
    return multi_account_manager


def initialize_multi_account_manager(config: dict) -> bool:
    """Initialize the global multi-account manager"""
    global multi_account_manager
    
    if multi_account_manager is not None:
        return True
        
    multi_account_manager = MultiAccountManager(config)
    
    if not multi_account_manager.load_accounts_from_config():
        return False
        
    if not multi_account_manager.login_all_accounts():
        return False
        
    return True
