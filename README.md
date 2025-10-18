# Steamauto

![Steamauto](https://socialify.git.ci/Steamauto/Steamauto/image?description=1&language=1&name=1&owner=1&theme=Light)

<div align="center">
      <a href="https://github.com/Steamauto/Steamauto/stargazers" alt="GitHub Repo stars">
        <img src="https://img.shields.io/github/stars/Steamauto/Steamauto?logo=github" /></a>
      <a href="https://github.com/Steamauto/Steamauto/forks" alt="GitHub forks">
        <img src="https://img.shields.io/github/forks/Steamauto/Steamauto?logo=github" /></a>
</div>

> Open source Steam auto-shipment solution
> No fees, secure and stable

**Please read this documentation carefully before use!**
**Contributors are welcome to submit PRs to improve this program.**
**Please do not violate the open source license, including but not limited to closed-source resale of this program or modifications without open sourcing.**

## What can it do?

#### On [Buff Item Trading Platform](https://buff.163.com):

- Auto-shipment
- Auto-accept buy orders (requires enabling auto-accept gift offers)
- Supply buy order confirmations
- List all inventory at lowest price
  - Supports auto-listing descriptions
  - Supports auto-listing time period blacklist/whitelist
  - **Supports choosing to supply buy orders for maximum profit**

#### On [UU Youpin Item Trading Platform](https://www.youpin898.com/):

- Auto-shipment for sale items
- Auto-listing for rental/sale
  - Rental supports:
    - [x] Auto-set rental prices
    - [x] Set rental prices by fixed ratio of current sale price
  - Sale supports:
    - [ ] Price by wear range
    - [X] Price by profit rate (requires setting purchase price)

#### On [ECOSteam Trading Platform](https://www.ecosteam.cn/):

- Auto-shipment
- Sync listed items with BUFF and UU Youpin (supports ratios)

#### On [C5Game](https://www.c5game.com/):
- Auto-shipment

#### On Steam:

- Built-in Steam accelerator
- Auto-accept gift offers (offers that don't require spending any items from Steam inventory)

## How to use?

0. ~~Give this repository a star~~  
1. Go to [Github Releases](https://github.com/Steamauto/Steamauto/releases/latest) to download Steamauto suitable for your system
2. Run the program once, it will release configuration files
3. Edit `config.json5` in the `config` folder (file contains configuration assistance), enable the features you need
4. Modify all parameters in `steam_account_info.json5` in the `config` folder (related tutorials in appendix)
5. Configure relevant information according to the table below based on the platforms you need the program to automate

| Platform | Configuration Details |
| --------------------------------|--------------------------------------------------------------------|
| NetEase BUFF/UU Youpin | No manual login configuration needed, enable in `config.json5` and follow program prompts to login |
| ECOSteam | Need to configure partnerId in `config.json5` and create rsakey.txt in config folder with private key (tutorial below) |
| C5Game | Need to apply for API Key and configure in `config.json5` |

## `notification` related configuration items (only supports BUFF related plugins)

| Configuration Item | Description |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| sell_notification | Sale notification (can be deleted if not needed) |
| protection_notification | Sale protection notification (can be deleted if not needed) |
| item_mismatch_notification | Offer mismatch with BUFF sale item notification (can be deleted if not needed) |
| buff_cookie_expired_notification | BUFF Cookies expiration notification (can be deleted if not needed) |
| --- | --- |
| title | Notification title |
| body | Notification content |
| servers | Apprise format server list - see [Apprise](https://github.com/caronc/apprise) for details<br>- Additional support for [pushplus](https://www.pushplus.plus/) format as `pushplus://<token>` |

## FAQ

##### Account security issues?

All Steamauto source code is open on GitHub for everyone to review code security
Under the condition that the user's computer is not invaded by malicious software, account information cannot be leaked

##### SDA error `Object reference not set to an instance of an object`?

![Error as shown](https://github.com/Steamauto/Steamauto/assets/51043917/b1282372-11f6-4649-be5f-7bc52faf4c16)
Please remove mobile authenticator first before using SDA

##### Why does my editor show syntax errors when I open the configuration file?

This program uses json5 configuration files, so unsupported editors will show syntax errors, but this doesn't actually affect program operation

##### Can it handle seller-initiated offers?

Not supported, but there are the following solutions.
On BUFF, you can open [BUFF web version personal settings page](https://buff.163.com/user-center/profile) and check `Sell limited to buyer-initiated offers` in preference settings
On UU Youpin, there's no solution yet, you need to handle manually

##### How to get UU Youpin token?

In the latest version, just run the program directly. If the token is invalid, the program will automatically guide you to get a valid token

##### Does it support multiple instances?

Yes. But you need to copy multiple program instances and run them in different folders

##### Can I disable Buff auto-shipment?

Set `buff_auto_accept_offer.enable` to false in `config.json`

##### Proxy error when running source code with `proxies` configuration but local proxy works fine

This error occurs with specific `urllib` versions, installing a specific version can solve it

```
pip install urllib3==1.25.11
```

If errors occur after uncommenting lines 44-48 in `steampy/client.py`, it indicates this issue

## Appendix

### Getting Steam Account Information

Tutorials for obtaining `steam_account_info.json` related parameters are below, please refer to them
Personally recommend using [SteamDesktopAuthenticator (SDA)](https://github.com/Jessecar96/SteamDesktopAuthenticator) to get Steam token parameters - simple operation (do not use version 1.0.13, has issues with obtaining)
[Official video tutorial](https://www.bilibili.com/video/BV1ph4y1y7mz/)
[Rooted Android phone getting new Steam mobile authenticator tutorial](https://github.com/BeyondDimension/SteamTools/issues/2598)

### How to register ECOSteam Open Platform - Excerpt from [ECOSteam Official Documentation](https://docs.qq.com/aio/DRnR2U05aeG5MT0RS?p=tOOCPKrP8CUmptM7fhIq7p)

1. Application process
   1. Register and login to ECO App:
   2. Go to [My], click settings in top right;
   3. Click [Account & Security] to enter;
   4. Click [Open Capability Application] to enter introduction page;
   5. Click apply to join;
   6. Fill in application materials and submit, callback address and callback switch configuration can be modified after approval;  // Note: If ID card front/back photos need to be uploaded here, you can upload any images, they won't be reviewed
   7. Wait for approval;  // Note: Actually auto-approved, available immediately after application
2. Post-approval process
   1. Approved users can return to the page and click [View Identity ID];
   2. Enter RSA public key to get identity ID;  // Note: RSA private key needs to be filled into rsakey.txt in config directory after plugin runs, please generate RSA key pair yourself, recommend using 2048 or 4096 bit keys, if you don't know how to generate and don't want to learn, you can use online generation tools, for example [https://rsagen.pages.dev/) (if using this site, set strength: 2048 or 4096, please ensure browser security yourself)
      ~~Only use the **non-newline format** key content part.~~ ECOSteam now supports complete format key content parts
   3. If callback notifications are enabled, configure callback address and get ECO's callback public key;

## Acknowledgments

Thanks to [**@lupohan44**](https://github.com/lupohan44) for submitting a large amount of code to this project!

Thanks to devgod, 14m0k (QQ group users) for their tremendous help in developing the buy order supply functionality!

Thanks to [1Password](https://1password.com/) for providing free [1Password](https://1password.com/) team account authorization for open source projects