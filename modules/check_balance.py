
import os, sys, json, math, time, logging, platform, csv
from typing import List, Dict, Tuple, Optional

import questionary as q
from rich.console import Console
from rich.logging import RichHandler

from web3 import Web3
from eth_account import Account
from web3.exceptions import ContractLogicError
from decimal import Decimal, ROUND_HALF_UP, getcontext

from utils.helper import Web3Helper, FileHelper, NATIVE_SENTINEL
import config

console = Console()

class BalanceChecker:
    """
    Check balances of a token list for a set of wallets using Multicall3 when available.
    - Tokens are read from chain_config.CONTRACTS_FILE (each line: "0x... SYMBOL")
    - Wallets are read from chain_config.WALLET_FILE (private keys, one per line)
    - Uses utils.helper.Web3Helper for Web3/multicall wiring
    - Handles the native token sentinel "0xEeee...EEeE" (and chain_config.NATIVE_TOKEN) by reading eth.get_balance
    - Exports results to CSV in the repository root (check_balance_results.csv)
    """
    def __init__(self, chain_config):
        self.console = console
        self.chain_config = chain_config
        self.chainName = chain_config.CHAIN_NAME

        logging.basicConfig(level=logging.INFO, handlers=[RichHandler(console=self.console)])
        self.logger = logging.getLogger(__name__)

        # Web3 / helpers (reuse centralized helper for provider + multicall)
        self.web3h = Web3Helper(chain_config, console=self.console)
        self.w3 = self.web3h.w3
        self.multicall = self.web3h.multicall
        self.ens_names = self.web3h.ens_names

        # Files
        self.wallet_file = chain_config.RECEIVERS_FILE
        self.contracts_file = chain_config.CONTRACTS_FILE

        # In-memory
        self.wallet_private_keys: List[str] = []
        self.wallet_addresses: List[str] = []
        self.tokens: List[str] = [] # (address_lower, label)

        # Ensure placeholders exist
        try:
            FileHelper.ensure_placeholder(self.wallet_file, 'wallets')
            FileHelper.ensure_placeholder(self.contracts_file, 'contracts')
        except Exception as e:
            self.console.log(f"[yellow]Could not ensure placeholder files: {e}[/yellow]")

        self.is_linux = platform.system().lower() == "linux" 

    # ---------- Loaders ----------       

    def select_wallet_input_method(self):
        if self.is_linux : 
            choice = q.select(
            "Choose wallet address input method:",
            choices=["Defalut path(file)","Manual input(CLI)"]
            ).ask()
            if choice == "Defalut path(file)" :
                wallets, ens_name = self.web3h.load_wallets_file(self.wallet_file)
            if choice == "Manual input(CLI)" :
                wallets, ens_name = self.web3h.load_wallets_cli()
        else:
            choice = q.select(
            "Choose wallet address input method:",
            choices=["Defalut path(file)","Manual input(Gui)"]
            ).ask()

            if choice == "Defalut path(file)":
                wallets, ens_name = self.web3h.load_wallets_file(self.wallet_file)
            if choice == "Manual input(Gui)":
                wallets , ens_name = self.web3h.load_wallets_gui()
        return wallets, ens_name
              
    def select_token_input_method(self):
        if self.is_linux : 
            choice = q.select(
            "Choose token contract input method:",
            choices=["Defalut path(file)","Manual input(CLI)"]
            ).ask()
            if choice == "Defalut path(file)" :
                t = self.web3h.load_tokens_file(self.contracts_file)
            if choice == "Manual input(CLI)" :
                t = self.web3h.load_tokens_cli()
        else:
            choice = q.select(
            "Choose token contract input method:",
            choices=["Defalut path(file)","Manual input(Gui)"]
            ).ask()

            if choice == "Defalut path(file)":
                t = self.web3h.load_tokens_file(self.contracts_file)
            if choice == "Manual input(Gui)":
                t = self.web3h.load_tokens_gui()
        self.tokens = t
        return t

    # ---------- Multicall helpers ----------
    def _encode_balanceOf(self, token_address: str, account: str):
        erc20_abi = json.loads(self.chain_config.TOKEN_ABI)
        c = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=erc20_abi)
        return c.encode_abi(fn_name='balanceOf', args=[self.w3.to_checksum_address(account)])

    def _encode_symbol(self, token_address: str):
        erc20_abi = json.loads(self.chain_config.TOKEN_ABI)
        c = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=erc20_abi)
        try:
            return c.encode_abi(fn_name='symbol', args=[])
        except Exception:
            return None

    def _encode_decimals(self, token_address: str):
        erc20_abi = json.loads(self.chain_config.TOKEN_ABI)
        c = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=erc20_abi)
        try:
            return c.encode_abi(fn_name='decimals', args=[])
        except Exception:
            return None

    def _try_aggregate(self, calls: List[Tuple[str, bytes]], require_success: bool=False) -> List[Tuple[bool, bytes]]:
        """
        Execute multicall3.tryAggregate if available, else fallback to loop.
        """
        if self.multicall is not None:
            try:
                return self.web3h._tryAggregate3(require_success, calls).call()
            except Exception as e:
                self.console.log(f"[yellow]Multicall tryAggregate failed, fallback to single calls: {e}[/yellow]")
        # Fallback: execute each call directly
        out = []
        for target, data in calls:
            try:
                ret = self.w3.eth.call({'to': self.w3.to_checksum_address(target), 'data': data})
                out.append((True, ret))
            except Exception:
                out.append((False, b''))
        return out

    def collect_balances(self):
        """
        Calls helper.multicall_fetch for balances + token labels + ENS.
        Returns (result, rows) where:
            result: raw dict returned by multicall_fetch
            rows:   list of dict rows for CSV & pretty printing
        """
        getcontext().prec = 60  # high precision for big ints -> decimals

        wallets = self.wallet_addresses
        tokens  = list(self.tokens)
        wallet_positions = {}
        for idx, addr in enumerate(wallets, start=1):
            try:
                wallet_positions[self.w3.to_checksum_address(addr)] = idx
            except Exception:
                wallet_positions[addr] = idx

        # one call: balances + labels + ENS, no decimals / no allowance
        result = self.web3h.multicall_fetch(
            wallets=wallets,
            tokens=tokens,
            spender=None,
            ens_names=getattr(self, "ens_names", []),
            use_multicall=True,
            want_balance=True,
            want_decimals=False,
            want_allowance=False,
            want_ens=True,
            with_name=True,
            with_symbol=True,
        )

        balances    = result.get("balances", {})       # {(wallet, token)->int}
        names_map   = result.get("names", {})          # {token->name}
        symbols_map = result.get("symbols", {})        # {token->symbol}
        ens_rev     = result.get("ens_reverse", {})    # {wallet->ens|None}
        ens_fwd     = result.get("ens_forward", {})    # {ens_name->address|None}

        # Build rows for CSV / printing (format to 5 decimals; assume 18-dec for display only)
        rows = []
        for (wallet, token), raw in balances.items():
            ct = Web3.to_checksum_address(token)
            # Display-only formatting to 5 decimals (assumes 18 decimals)
            pretty = (Decimal(raw) / (Decimal(10) ** 18)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
            rows.append({
                "wallet_number": wallet_positions,
                "wallet": wallet,
                "wallet_ens": ens_rev.get(wallet, "") or "",
                "token": ct , 
                "token_name": names_map.get(ct, "") or "",
                "token_symbol": symbols_map.get(ct, "") or "",
                "raw": str(raw),
                "formatted": f"{pretty:.5f}",
            })

        return result, rows
    
    def export_csv(self, rows, out_path: str) -> str:
        headers = ["wallet_number","wallet", "wallet_ens", "token", "token_name", "token_symbol", "raw", "formatted"]
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in headers})
        return out_path

    def run(self):
        self.wallet_addresses, self.ens_names = self.select_wallet_input_method()
        self.tokens = self.select_token_input_method()
        # 2) call multicall_fetch (balances + labels + ENS)
        self.console.rule("[bold cyan]Fetching balances")
        result, rows = self.collect_balances()

        # 3) AFTER the fetch, print all logs / details
        names_map   = result.get("names", {})
        symbols_map = result.get("symbols", {})
        ens_rev     = result.get("ens_reverse", {})
        ens_fwd     = result.get("ens_forward", {})

        # Wallets loaded (with reverse ENS if present)
        self.console.rule("[bold cyan]Wallets loaded")
        for w in self.wallet_addresses:
            rev = ens_rev.get(w)
            if rev:
                self.console.log(f"[green]{w}[/green]  (ENS: [magenta]{rev}[/magenta])")
            else:
                self.console.log(f"[green]{w}[/green]")

        # ENS names provided (show forward resolution)
        if getattr(self, "ens_names", []):
            for n in self.ens_names:
                addr = ens_fwd.get(n)
                if addr:
                    self.console.log(f"[magenta]{n}[/magenta] → [cyan]{addr}[/cyan]")
                else:
                    self.console.log(f"[magenta]{n}[/magenta] → [red]unresolved[/red]")

        for t in self.tokens:
            ct = Web3.to_checksum_address(t)
            sym = symbols_map.get(ct, "") or symbols_map.get(t, "") or ""
            nm  = names_map.get(ct, "") or names_map.get(t, "") or ""
            if sym or nm:
                self.console.log(f"[cyan]{ct}[/cyan] — {sym} {f'({nm})' if nm else ''}")
            else:
                self.console.log(f"[cyan]{ct}[/cyan]")
        for r in rows:
            self.console.log(f"{r['wallet'][:8]}… | {r['token_symbol'] or 'TOKEN'} | {r['formatted']}")

        # 4) Export CSV (requested name)
        out_file = os.path.join(os.path.dirname(__file__), "..", "logs", f"checkBalance_{self.chainName}_result.csv")
        out_path = os.path.abspath(os.path.normpath(out_file))
        saved = self.export_csv(rows, out_path)
        self.console.log(f"[bold green]Exported CSV:[/bold green] {saved}")

def main():
    chain_choices = ["POLYGON", "OP", "Base", "ARB", "Linea", "ETHER"]
    chain_selection = q.select("Select chain:", choices=chain_choices).ask()

    if chain_selection == "POLYGON":
        chain_config = config.POLYGON
    elif chain_selection == "OP":
        chain_config = config.OP
    elif chain_selection == "Base":
        chain_config = config.Base
    elif chain_selection == "ARB":
        chain_config = config.ARB
    elif chain_selection == "Linea":
        chain_config = config.Linea
    elif chain_selection == "ETHER":
        chain_config = config.ETHER
    else:
        chain_config = config.POLYGON

    app = BalanceChecker(chain_config)
    app.run()

if __name__ == "__main__":
    main()
