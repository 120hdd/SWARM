# SWARM

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-brightgreen)

Smart Wallet Automated Routine Manager (SWARM) helps you automate common EVM wallet routines such as:
- Batch ERC-20 swaps (via KyberSwap)
- Batch token transfers
- Multi-wallet balance checks

It's designed to run safely across many wallets while keeping gas usage under control.

<img width="1024" height="813" alt="SWARM interface" src="https://github.com/user-attachments/assets/202f1c11-a6eb-4a5d-9592-634908e4f3be" />

## Requirements
- Python 3.11+ (64-bit recommended)
- pip for managing packages

## Highlights
- Automates repetitive EVM wallet tasks
- Batch execution across chains
- KyberSwap-powered quotes with resilient RPC rotation
- Simple resource files to preload wallets, tokens, and receivers

## Setup
Follow the steps for your OS. If you are new to Python, copy and paste the commands exactly as shown.

### Windows (PowerShell)
1. Install prerequisites
   - Install Python 3.11 or newer from python.org and check "Add Python to PATH".
   - Alternatively, install with winget:
     ```
     winget install -e --id Python.Python.3.11 --scope machine
     ```
   - Install Git from git-scm.com or via winget:
     ```
     winget install --id Git.Git -e --source winget
     ```
2. Get the code and open the folder
   - Clone the repository:
     ```
     git clone https://github.com/120hdd/SWARM.git
     ```
   - Then change into the project directory:
     ```
     cd SWARM
     ```
3. Create a virtual environment and install packages
   - Create and activate the virtual environment:
     ```
     python -m venv .venv
     .\.venv\Scripts\Activate
     ```
   - Upgrade pip and install dependencies:
     ```
     python -m pip install --upgrade pip
     pip install -r requirements.txt
     ```
4. Create your `.env`
   - Copy the example file:
     ```
     copy env.example .env
     ```
   - Open `.env` in a text editor and add your API keys.
5. Run SWARM
   - Launch the runner:
     ```
     python main_runner.py
     ```
   - Choose a task from the menu (for example `kyberSwap`, `transfer_token`, or `check_balance`).

Optional UI note: CustomTkinter uses the Windows Tk runtime that ships with the standard Python installer. If overlays do not appear, continue with the CLI prompts.

### macOS / Linux
1. Install prerequisites
   - Install Python 3.11+ and Git. On Linux you may also need Tk bindings for the optional UI overlays:
     ```
     sudo apt-get install -y python3-tk
     ```
2. Get the code and open the folder
   - Clone the repository:
     ```
     git clone https://github.com/120hdd/SWARM.git
     ```
   - Change into the project directory:
     ```
     cd SWARM
     ```
3. Use the setup script (recommended)
   - Make it executable:
     ```
     chmod +x setup.sh
     ```
   - Run the script:
     ```
     ./setup.sh
     ```
   - The script creates `.venv`, installs `requirements.txt`, and copies `env.example` to `.env` if missing. If you prefer to do it manually, run the same commands as shown in the Windows section using Unix-style paths (`source .venv/bin/activate` and `pip install -r requirements.txt`). To copy the env file manually:
     ```
     cp env.example .env
     ```
4. Activate the virtual environment later (if needed)
   - macOS/Linux:
     ```
     source .venv/bin/activate
     ```
5. Run SWARM
   - Launch the runner:
     ```
     python main_runner.py
     ```
   - Choose a task from the menu.

## Configure Your .env
Set these so SWARM can talk to a blockchain node:
- `ALCHEMY_API_KEY` (RPC calls)
- `INFURA_API_KEY` (used for the gas price API)

You can also add advanced options:
- `ALCHEMY_API_KEYS` - comma-separated list of extra Alchemy keys for RPC rotation.
- `EXTRA_RPC_URLS` - comma-separated list of additional RPC URLs.

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
  2. Create an app for your target network (for example Ethereum Mainnet).
  3. Copy the HTTP API URL (looks like `.../v2/<YOUR_KEY>`). Use the part after `/v2/` as `ALCHEMY_API_KEY`.

- Infura
  1. Go to https://infura.io and create a project.
  2. Select your network under Endpoints. Copy the HTTPS URL (contains `/v3/<PROJECT_ID>`).
  3. Use the `<PROJECT_ID>` as `INFURA_API_KEY`.

## Prepare Your Resources
SWARM uses simple text files in `resources/` so you do not re-enter data each time.

- Wallet private keys: `resources/wallet.txt`
  - One private key per line. Supports `0x`-prefixed or raw hex.
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

You can also enter wallets and tokens interactively from the UI or CLI when prompted.

## Run Tasks
Run the launcher with `python main_runner.py` and pick a module. Common options include:
- `kyberSwap`: Batch swaps with chain selection, slippage, and gas tier configuration. Handles approvals automatically.
- `transfer_token`: Batch ERC-20 transfers from multiple senders to receivers.
- `check_balance`: Check balances across many wallets and tokens; writes CSV output in the `result` directory.

When a task starts you will be asked to:
- Select a chain (Polygon, Optimism, Base, Arbitrum, Linea, Ethereum)
- Choose how to enter wallets and tokens (from files, CLI prompts, or GUI depending on OS)
- Confirm or adjust gas settings for swaps and transfers

## Troubleshooting
- `No RPC URLs configured`
  - Ensure `.env` has `ALCHEMY_API_KEY` or set `EXTRA_RPC_URLS`.
- CustomTkinter UI does not appear
  - Windows: use the standard Python installer from python.org (includes Tk). You can always use the CLI.
  - Linux: install Tk bindings with `sudo apt-get install -y python3-tk`.
- pip or build issues
  - Upgrade pip: `python -m pip install --upgrade pip`.
  - Recreate the virtual environment: delete `.venv/` then repeat setup.
- Rate limits or flaky RPC
  - Add more keys via `ALCHEMY_API_KEYS` or set `EXTRA_RPC_URLS`.

## Notes
- KyberSwap API headers: the default `x-client-id` in `config.py` is a placeholder. Replace it with your client id if you have one.
- Resource placeholders: on the first run, helper utilities create starter files in `resources/` for wallets, receivers, and tokens so you can fill them in later.

## Contributing
Pull requests are welcome - open an issue first to discuss changes.

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
