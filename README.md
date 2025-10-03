# SWARM

Smart Wallet Automated Routine Manager (SWARM) automates low-fee Ethereum Virtual Machine (EVM) wallet routines such as batching ERC-20 swaps, token transfers, and balance checks. The CLI helps you fan out work across many wallets while keeping gas usage under control.

## Highlights
- Multi-wallet orchestration with sequential signing to avoid nonce clashes
- Compatible with any EVM chain that exposes a JSON-RPC endpoint
- KyberSwap-powered quoting plus custom RPC rotation for resilience
- Optional desktop overlays (CustomTkinter) for richer feedback while long jobs run
- Configurable resource files so you can preload wallets, tokens, and routing preferences

## Prerequisites
- Python 3.10 or newer (3.11 recommended)
- Git
- Network access to your target chain (Alchemy, Infura, or self-hosted node)
- On Linux/macOS: system Tk bindings for the optional CustomTkinter UI (`sudo apt-get install python3-tk` or equivalent)

## Quick Start
```bash
# 1. Clone the repository
git clone https://github.com/120hdd/MWswap.git
cd SWARM

# 2. Make sure the setup script is executable (macOS/Linux)
chmod +x setup.sh

# 3. Provision the virtual environment and install dependencies
./setup.sh

# 4. Run the interactive task launcher
python main_runner.py
```

---

## Environment Variables

| Var | Description |
|-----|-------------|
| `ALCHEMY_API_KEY` | Your Alchemy HTTP key for the desired network |
| `INFURA_API_KEY`  | (Optional) Your Infura API key |

---

## Getting API Keys

### Alchemy

1. Go to <https://alchemy.com>, sign up (free tier is fine).  
2. Click **“Create App”**, choose the desired chain and network (e.g., `Ethereum Mainnet`).  
3. In the app dashboard, copy the **HTTP API URL**—the long URL ends with something like `.../v2/ALCHEMY_API_KEY`.  
4. Paste the value after the last slash (`ALCHEMY_API_KEY`) into your `.env`:

   ```env
   ALCHEMY_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Infura

1. Visit <https://infura.io>, create an account, and make a **New Project**.  
2. Select the network (e.g., Mainnet) under **Endpoints**.  
3. Copy the **HTTPS URL**—it contains your project ID at the end (after `/v3/`).  
4. Put that ID in your `.env`:

   ```env
   INFURA_API_KEY=yyyyyyyyyyyyyyyyyyyyyyyyyyyyy
   ```

---

## Contracts
* You can specify token addresses manually via CLI **or** list them in  
  `/resources/<chain_name>/tokens.txt` (one `address` per line).
  
---
## Wallets
You can supply wallets interactively via CLI, or maintain a default list in  
`/resources/wallet.txt`.

---

## Contributing

Pull requests are welcome—open an issue first to discuss changes.

MIT License
