# SWARM

Smart Wallet Automated Routine Manager (SWARM) helps you automate common EVM wallet routines such as:
- Batch ERC‑20 swaps (via KyberSwap)
- Batch token transfers
- Multi‑wallet balance checks
It’s designed to run safely across many wallets while keeping gas usage under control.

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen)
=======

<img width="1024" height="813" alt="image" src="https://github.com/user-attachments/assets/202f1c11-a6eb-4a5d-9592-634908e4f3be" />

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-brightgreen)

## Requirements

- **Python 3.11+** (64-bit recommended)
- `pip` for managing packages


## Highlights

- Automates repetitive EVM wallet tasks
- Batch execution across chains
- KyberSwap‑powered quotes + resilient RPC rotation
- Simple resource files to preload wallets, tokens, and receivers


## Setup

Follow the steps for your OS. If you’re brand new to Python, just copy/paste commands exactly as shown.


### Windows (PowerShell)
1) Install prerequisites

- Install Python 3.10 or newer from python.org. During install, check “Add Python to PATH”.
- Install Git from git-scm.com.

- Install Python 3.11 or newer from python.org. During install, check “Add Python to PATH”.
- ```
winget install -e --id Python.Python.3.11 --scope machine
```   
  
- Install Git from git-scm.com.
- ```
  winget install --id Git.Git -e --source winget   
  ```




2) Get the code and open the folder
- Clone this repository and open it in a terminal:
  - ```
  git clone https://github.com/120hdd/SWARM.git
  ```
 
  - Then change directory to the cloned folder (use the name you see after cloning), for example:``cd SWARM``

3) Create a virtual environment and install packages

- Create and activate a virtual environment:
  - ```
  python -m venv .venv
  ./.venv/Scripts/Activate
  ```
- Upgrade pip and install dependencies:
  - ```
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  ```

4) Create your `.env`
  - Copy the example file: ```
  copy env.example .env
  ```
- Open `.env` in a text editor and add your API keys (details below).

5) Run SWARM
- ```
python main_runner.py
```
- Choose a task from the menu (e.g., `kyberSwap`, `transfer_token`, `check_balance`).

Note (optional UI): CustomTkinter uses the built‑in Windows Tk runtime that comes with the standard Python installer. If overlays don’t appear, you can still use the CLI flows.

### macOS / Linux
**1) Install prerequisites**
- Python 3.10+ and Git. On Linux you may also need Tk bindings for optional overlays:
  - Ubuntu/Debian: ````sudo apt-get install -y python3-tk````

**2) Get the code and open the folder**
- ```
git clone https://github.com/120hdd/SWARM.git
```
- `cd SWARM` ('or the folder name you cloned into)

**3) Use the setup script (recommended)**
- Make it executable: ```
chmod +x setup.sh
```

- Run it: ```
./setup.sh
```

- Creates `.venv`, installs `requirements.txt`, and copies `env.example` to `.env` if missing.
  - use ````copy env.example .env```` 


- Python 3.11+ and Git. On Linux you may also need Tk bindings for optional overlays:
  - Ubuntu/Debian: ```
  sudo apt-get install -y python3-tk
  ```
  
4) Activate the virtual environment (if needed later)
- macOS/Linux: ```
source .venv/bin/activate
```

5) Run SWARM
-  ```
python main_runner.py
```
- Choose a task from the menu.

## Configure Your .env

set these so SWARM can talk to a blockchain node:
- `ALCHEMY_API_KEY` (RPC call)
- `INFURA_API_KEY` (used for gas price API)

You can also add advanced options:
- `ALCHEMY_API_KEYS` — comma‑separated list of extra Alchemy keys for RPC rotation.
- `EXTRA_RPC_URLS` — comma‑separated list of full RPC URLs.

Example `.env`:
```
ALCHEMY_API_KEY=your_alchemy_key_here
INFURA_API_KEY=your_infura_key_here
# Optional advanced settings
#ALCHEMY_API_KEYS=key1,key2
#EXTRA_RPC_URLS=https://my.custom.node:8545
```

### Getting API Keys
- Alchemy
  1. Go to https://alchemy.com and create a free account.
  2. Create an app for your target network (e.g., Ethereum Mainnet).
  3. Copy the HTTP API URL (looks like `.../v2/<YOUR_KEY>`). Use the part after `/v2/` as `ALCHEMY_API_KEY`.

- Infura
  1. Go to https://infura.io and create a project.
  2. Select your network under Endpoints. Copy the HTTPS URL (contains `/v3/<PROJECT_ID>`).
  3. Use the `<PROJECT_ID>` as `INFURA_API_KEY`.

## Prepare Your Resources

SWARM uses simple text files in `resources/` so you don’t re‑enter data each time.

- Wallet private keys: `resources/wallet.txt`
  - One private key per line. Supports `0x`‑prefixed or raw hex.
  - Example:
    ```
    0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    ```

- Receivers (for transfers): `resources/receiver_wallet.txt`
  - One address or ENS per line.
  - Example:
    ```
    vitalik.eth
    0x1234567890abcdef1234567890abcdef12345678
    ```

- Token lists per chain: `resources/<CHAIN>/tokens.txt`
  - One token address per line. The native token sentinel is `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE`.
  - Examples:
    - Polygon: `resources/POLYGON/tokens.txt`
    - Base: `resources/BASE/tokens.txt`
    - Ethereum: `resources/ETHER/tokens.txt`
    - Optimism: `resources/OP/tokens.txt`
    - Arbitrum: `resources/ARB/tokens.txt`
    - Linea: `resources/LINEA/tokens.txt`

You can also enter wallets/tokens interactively from the UI/CLI when prompted.

## Run Tasks

Run the launcher: `python main_runner.py` and pick a module. The common ones are:
- `kyberSwap` — Batch swaps. You’ll pick chain, tokens, slippage, and gas tier. Handles allowance automatically.
- `transfer_token` — Batch ERC‑20 transfers from multiple senders to receivers.
<<<<<<< HEAD
- `check_balance` — Check balances across many wallets and tokens; writes CSV output in the repo root.
=======
- `check_balance` — Check balances across many wallets and tokens; writes CSV output in the result directory.

When a task starts, you’ll be asked to:
- Select a chain (Polygon, OP, Base, ARB, Linea, Ethereum)
- Choose how to enter wallets/tokens (from files, CLI, or GUI depending on OS)
- Confirm/adjust gas settings (for swaps/transfers)

## Troubleshooting
- “No RPC URLs configured”
  - Ensure `.env` has `ALCHEMY_API_KEY` or set `EXTRA_RPC_URLS`.
- CustomTkinter/UI doesn’t show
  - Windows: use the standard Python installer from python.org (includes Tk). You can still use CLI without UI.
  - Linux: `sudo apt-get install -y python3-tk`.
- Pip/build issues
  - Upgrade pip: `python -m pip install --upgrade pip`.
  - Recreate venv: delete `.venv/` then repeat setup.
- Rate limits / flaky RPC
  - Add more keys via `ALCHEMY_API_KEYS` or set `EXTRA_RPC_URLS`.

## Notes
- KyberSwap API headers: the default `x-client-id` in `config.py` is a placeholder. If you have a custom client id, set it there.
- Files created on first run: helper utilities create placeholder files in `resources/` if missing.
> **Resource Placeholders**: on first run, helper utilities create starter files in `resources/` for wallets, receivers, and tokens so you can fill them in later.

## Contributing
Pull requests are welcome — open an issue first to discuss changes.

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.


