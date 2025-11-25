import os
import sys

from utils.build_info import info

is_latest_version = False
no_pause = False

CURRENT_VERSION = "5.7.4"

VERSION_FILE = "version.json"
LOGS_FOLDER = "logs"
CONFIG_FOLDER = "config"
PLUGIN_FOLDER = "plugins"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, "config.json5")
BUFF_COOKIES_FILE_PATH = os.path.join(CONFIG_FOLDER, "buff_cookies.txt")
UU_TOKEN_FILE_PATH = os.path.join(CONFIG_FOLDER, "uu_token.txt")
STEAM_ACCOUNT_INFO_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_account_info.json5")
STEAM_INVENTORY_FILE_PATH = os.path.join(CONFIG_FOLDER, "steam_inventory.json5")
SESSION_FOLDER = "session"
os.makedirs(SESSION_FOLDER, exist_ok=True)
SUPPORT_GAME_TYPES = [{"game": "csgo", "app_id": 730}]
UU_ARG_FILE_PATH = "uu.txt"
ECOSTEAM_RSAKEY_FILE = os.path.join(CONFIG_FOLDER, "rsakey.txt")
BUILD_INFO = info
if BUILD_INFO == "Running from source":
    if hasattr(sys, "_MEIPASS"):
        BUILD_INFO = "Unofficial binary build"

STEAM_ACCOUNT_NAME = "Not logged in"
STEAM_64_ID = "Not logged in"

INTERNAL_PLUGINS = [
    "buff_auto_accept_offer",
    "buff_auto_comment",
    "buff_profit_report",
    "buff_auto_on_sale",
    "uu_auto_accept_offer",
    "uu_auto_lease_item",
    "uu_auto_sell_item",
    "steam_auto_accept_offer",
    "ecosteam",
    "c5_auto_accept_offer",
]

DEFAULT_STEAM_ACCOUNT_JSON = """
{
  // Multi-account configuration for BUFF support (up to 5 accounts)
  "max_accounts": 5,
  
  "accounts": [
    {
      // Account display name (for logging and notifications)
      "name": "Main Account",
      
      // Steam login credentials
      "steam_username": "",
      "steam_password": "",
      
      // Steam authenticator tokens
      "shared_secret": "",
      "identity_secret": "",
      
      // Steam ID (64-bit) - this must match the seller_steamid from BUFF delivery offers
      "steamid": "",
      
      // Whether this account is enabled
      "enabled": true
    }
    // Add more accounts here (up to 5 total)
    // {
    //   "name": "Alt Account 1",
    //   "steam_username": "",
    //   "steam_password": "",
    //   "shared_secret": "",
    //   "identity_secret": "",
    //   "steamid": "",
    //   "enabled": true
    // }
  ]
}
"""

DEFAULT_CONFIG_JSON = r"""
{
  // Whether to ignore SSL errors when logging into Steam. Do not disable SSL in normal cases.
  "steam_login_ignore_ssl_error": false,
  
  // Local acceleration. Not guaranteed to work. Best solution is an overseas server.
  // Note: enabling this requires steam_login_ignore_ssl_error=true.
  "steam_local_accelerate": false,

  // Proxy notes:
  // If you use Clash/v2RayN/ShadowSocksR, configure here.
  // Use a Steam-only proxy
  "use_proxies": false,

  // Local proxy address. Applied only to Steam. Ensure use_proxies=true first.
  // http and https are usually the same.
  "proxies": {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
  },
  
  "notify_service": {
    // Notifiers in Apprise format. Supports Telegram, DingTalk, Feishu, WxPusher, ServerChan, etc.
    // See https://github.com/caronc/apprise/wiki
    "notifiers": [],
    // Custom title. Empty uses default.
    "custom_title": "",
    // Include Steam account info
    "include_steam_info": true,
    // Blacklist words. If contained, the notification is suppressed.
    "blacklist_words": [
      "blacklist_word_1",
      "blacklist_word_2"
    ]
  },

  // BUFF auto-delivery plugin
  "buff_auto_accept_offer": {
    // Enable auto-accept BUFF delivery offers
    "enable": true,
    // Polling interval in seconds
    "interval": 300,
    // Enable dota2 support
    "dota2_support": false
  },
  // BUFF auto comment purchase price
  "buff_auto_comment": {
    // Enable
    "enable": false
  },
  // BUFF profit report
  "buff_profit_report": {
    // Enable
    "enable": false,
    // Notification servers in Apprise format
    "servers": [
    ],
    // Daily report time, 24h
    "send_report_time": "20:30"
  },
  // BUFF auto list-for-sale
  "buff_auto_on_sale": {
    // Enable list all inventory at lowest price
    "enable": false,
    // Force refresh BUFF inventory each check. If false, refresh may not load latest.
    "force_refresh": true,
    // Use wear-range lowest price. If false, use type lowest price.
    // Note: increases requests. Use with caution.
    "use_range_price": false,
    // Blacklist hours (int). When current hour equals any, do not list.
    "blacklist_time": [],
    // Whitelist hours (int). When current hour not in list, do not list.
    "whitelist_time": [],
    // Random listing chance 0–100
    "random_chance": 100,
    // Listing description. Empty means none.
    "description": "",
    // Inventory check interval (seconds)
    "interval": 1800,
    // Sleep seconds per request to avoid BUFF bans
    "sleep_seconds_to_prevent_buff_ban": 10,
    // Buy-order supply config
    "buy_order": {
      // Supply buy orders
      "enable": true,
      // Only to orders with auto-accept on
      "only_auto_accept": true,
      // Supported payment methods: Alipay, WeChat
      "supported_payment_method": ["Alipay"],
      // Items cheaper than this go to buy orders directly
      "min_price": 5
    },
    // Listing notification (optional; remove to disable)
    "on_sale_notification": {
        // Title
        "title": "Game {game} listed {sold_count} item(s) successfully",
        // Body
        "body": "Details:\n{item_list}"
    },
    // Captcha notification (optional; remove to disable)
    "captcha_notification": {
        // Title
        "title": "Captcha encountered during listing",
        // Body
        "body": "Open in a browser with session={session} and complete verification:\n{captcha_url}"
    },
    // Notification servers in Apprise format
    "servers": [
    ]
  },
  // UU Youpin auto-delivery
  "uu_auto_accept_offer": {
    // Enable
    "enable": false,
    // Polling interval seconds
    "interval": 300
  },
  // UU Youpin auto list-for-lease
  "uu_auto_lease_item": {
    // Enable
    "enable": false,
    // Max lease days
    "lease_max_days": 60,
    // Items below this price are not listed
    "filter_price": 100,
    // Daily run time
    "run_time": "17:30",
    // Adjust price periodically for already-leased items. Interval minutes
    "interval": 31,
    // Do-not-lease item names. Example: ["Item A","Item B"] (partial names allowed)
    "filter_name": ["Item A", "Item B"],
    // Fixed ratio pricing
    "enable_fix_lease_ratio": false,
    // Lease price ratio. Example: current 1000, ratio 0.001 => price 1 (not lower than normal calc)
    "fix_lease_ratio": 0.001,
    // Compensation type: 0(non-member), 7(v1), others unknown
    "compensation_type": 7
  },
  // UU Youpin auto list-for-sale
  "uu_auto_sell_item": {
    // Enable
    "enable": false,
    // Price by take-profit ratio
    "take_profile": false,
    // Take-profit ratio
    "take_profile_ratio": 0.1,
    // Daily run time
    "run_time": "15:30",
    // Reprice interval minutes
    "sell_interval": 20,
    // Do not list items above this price. 0 for no limit
    "max_on_sale_price": 1000,
    // Reprice interval minutes for listed items
    "interval": 51,
    // Names to sell. Partial allowed
    "name": [
      "AK",
      "A1"
    ],
    // Blacklist names. Higher priority than the sell list.
    "blacklist_words": [
      "blacklist_word_1",
      "blacklist_word_2"
    ],
    "use_price_adjustment": true, // Auto undercut by -0.01
    "price_adjustment_threshold": 1.0 // Only undercut above this price
  },
  // Steam auto-accept gift offers
  "steam_auto_accept_offer": {
    // Enable auto-accept Steam gift offers that require no items from your inventory
    "enable": false,
    // Polling interval seconds
    "interval": 300
  },
  // ECOSteam.cn plugin
  // Integrate with the open platform first. Put private key in config/rsakey.txt
  "ecosteam": {
    "enable": false,
    "partnerId": "", // Required for ECOsteam login
    "auto_accept_offer": {
      "interval": 30
    },
    "auto_sync_sell_shelf": { // Sync listed items across platforms to match main platform
      "enable": false,
      "main_platform": "eco", // Main platform. Its listings stay unchanged. Others follow ratio. "buff"/"uu"/"eco"
      "enabled_platforms": ["uu"], // Multiple allowed, e.g. ["buff","uu"]. ECO always enabled.
      "ratio": { // Price ratio per platform
        "eco": 1,
        "uu": 1,
        "buff": 1
      }
    },
    "auto_sync_lease_shelf": { // Sync lease items with UU Youpin
      "enable": false,
      "main_platform": "eco", // Main platform. Options: "uu"/"eco"
      "ratio": { // Lease price ratios
        "eco": 1,
        "uu": 1
      }
    },
    "sync_interval": 60, // Sync interval seconds. Do not set too long or accounts may be banned.
    "qps": 10 // Max requests per second. If you have VIP whitelist, 30 is suggested.
  },
  "c5_auto_accept_offer": { // C5 auto-delivery
    "enable": false, // Enable
    "interval": 30, // Polling interval seconds
    "app_key": "" // C5Game AppKey. Apply at https://www.c5game.com/user/user/open-api
  },
  // Master panel configuration for item tracking
  "master_panel": {
    // Master panel API base URL
    "baseurl": "",
    // Master panel API key
    "api_key": ""
  },
  // File log level: "debug"/"info"/"warning"/"error"
  "log_level": "debug",
  // Local log retention days
  "log_retention_days": 7,
  // If true, program stops immediately on error. Do not enable unless you know what you are doing.
  "no_pause": false,
  // Local plugin whitelist. When local plugin differs from bundled one, it will not be overwritten.
  "plugin_whitelist": [],
  // Auto-update when running from source
  "source_code_auto_update": false
}
"""

STEAM_ERROR_CODES = {
    1: "OK",
    2: "Fail",
    3: "No connection",
    4: "No connection, retry",
    5: "Invalid password",
    6: "Logged in elsewhere",
    7: "Invalid protocol version",
    8: "Invalid parameter",
    9: "File not found",
    10: "Busy",
    11: "Invalid state",
    12: "Invalid name",
    13: "Invalid email",
    14: "Duplicate name",
    15: "Access denied",
    16: "Timeout",
    17: "Banned",
    18: "Account not found",
    19: "Invalid Steam ID",
    20: "Service unavailable",
    21: "Not logged in",
    22: "Pending",
    23: "Encryption failure",
    24: "Insufficient privilege",
    25: "Limit exceeded",
    26: "Revoked",
    27: "Expired",
    28: "Already redeemed",
    29: "Duplicate request",
    30: "Already owned",
    31: "IP not found",
    32: "Persistence failed",
    33: "Locking failed",
    34: "Logon session replaced",
    35: "Connect failed",
    36: "Handshake failed",
    37: "IO failure",
    38: "Remote disconnect",
    39: "Shopping cart not found",
    40: "Blocked",
    41: "Ignored",
    42: "No match",
    43: "Account disabled",
    44: "Service read-only",
    45: "Account not featured",
    46: "Admin OK",
    47: "Content version error",
    48: "CM switch failed",
    49: "Password required to kick",
    50: "Logged in elsewhere",
    51: "Suspended",
    52: "Canceled",
    53: "Data corrupt",
    54: "Disk full",
    55: "Remote call failed",
    56: "Password unset",
    57: "External account unlinked",
    58: "Invalid PSN ticket",
    59: "External account linked",
    60: "Remote file conflict",
    61: "Illegal password",
    62: "Same as previous value",
    63: "Account logon denied",
    64: "Cannot reuse old password",
    65: "Invalid auth code",
    66: "Logon denied, no mail",
    67: "Hardware not capable of IPT",
    68: "IPT init error",
    69: "Parental control restricted",
    70: "Facebook query error",
    71: "Expired auth code",
    72: "IP logon restriction failed",
    73: "Account locked",
    74: "Email verification required",
    75: "No matching URL",
    76: "Bad response",
    77: "Password reentry required",
    78: "Value out of range",
    79: "Unexpected error",
    80: "Disabled",
    81: "Invalid CEG submission",
    82: "Restricted device",
    83: "Region restricted",
    84: "Rate limit exceeded",
    85: "2FA required",
    86: "Item deleted",
    87: "Logon rate limited",
    88: "2FA code mismatch. Check shared_secret.",
    89: "2FA activation code mismatch",
    90: "Multiple partner accounts linked",
    91: "Not modified",
    92: "No mobile device",
    93: "Time not synchronized",
    94: "SMS code failed",
    95: "Account limit exceeded",
    96: "Account activity limit exceeded",
    97: "Phone activity limit exceeded",
    98: "Refund to wallet",
    99: "Email send failed",
    100: "Unresolved",
    101: "Captcha required",
    102: "GSLT denied",
    103: "GSLT owner denied",
    104: "Invalid item type",
    105: "IP banned",
    106: "GSLT expired",
    107: "Insufficient funds",
    108: "Too many pending transactions",
    109: "Site license not found",
    110: "WG send rate exceeded",
    111: "Account not friended",
    112: "Limited user account",
    113: "Cannot remove item",
    114: "Account deleted",
    115: "Existing user canceled license",
    116: "Community cooldown",
    117: "Launcher unspecified",
    118: "Must accept EULA",
    119: "Launcher migrated",
    120: "Steam realm mismatch",
    121: "Invalid signature",
    122: "Parse failure",
    123: "No verified phone",
}
