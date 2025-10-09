import sys
import os
import json
import math, time
import logging
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Set

import requests
import questionary
from web3 import Web3
from rich.console import Console
from rich.logging import RichHandler
from eth_account import Account
from web3.exceptions import ABIFunctionNotFound, ContractLogicError , TimeExhausted
from eth_account.messages import encode_structured_data
import platform

import config  # Make sure your config.py is in the same directory or PYTHONPATH
from utils.rpc_provider import RotatingHTTPProvider
from utils.helper import Web3Helper, FileHelper, NATIVE_SENTINEL

console = Console()

class SwapManager:
    def __init__(self, chain_config, KYBERSWAP_API_HEADERS=config.KYBERSWAP_API_HEADERS):
        """
        Initialize the SwapManager with a specific chain configuration object.
        """
        self.console = Console()
        self.KYBERSWAP_API_HEADERS = KYBERSWAP_API_HEADERS

        # Store the chain config (Polygon, OP, Base, etc.)
        self.chain_config = chain_config

        # Extract commonly used fields
        # Build a list of RPC URLs (supports multiple keys via env)
        self.rpc_urls = self._build_rpc_urls(chain_config)
        self.wallet_file = chain_config.WALLET_FILE
        self.contracts_file = chain_config.CONTRACTS_FILE
        self.INFURA_GAS_API_URL = chain_config.INFURA_GAS_API_URL
        self.native_token = chain_config.NATIVE_TOKEN

        # Some chain configs have CHAIN_ID as string, ensure integer
        self.chain_id = int(chain_config.CHAIN_ID)
        self.chain_name = chain_config.CHAIN_NAME
        self._token_meta = {}
        self._permit_support_cache = {}

        self.rpc_urls = self._build_rpc_urls(chain_config)
        self.provider = RotatingHTTPProvider(self.rpc_urls)
        self.provider.console = self.console
        self.w3 = Web3(self.provider)

        # Unified Web3 helper (centralizes RPC/ENS/Multicall/gas/tx/erc20)
        self.web3h = Web3Helper(chain_config, console=self.console)
        # Prefer helper-managed instances
        self.provider = self.web3h.provider
        self.w3 = self.web3h.w3

        # Logging
        logging.basicConfig(level=logging.INFO, handlers=[RichHandler(console=self.console)])
        self.logger = logging.getLogger(__name__)

        # Determine if running on a Linux system (commonly Ubuntu)
        self.is_linux = platform.system().lower() == "linux"

        # Lists to store loaded wallets
        self.wallet_addresses = []
        self.wallet_private_keys = []
        self.wallet_ens_names = {}
        self.wallet_unresolved_ens: List[str] = []

        self.create_placeholder_file(self.wallet_file, 'wallets')
        self.create_placeholder_file(self.contracts_file, 'contracts')
        # Load token contracts
        self.tokens: OrderedDict[str, str] = OrderedDict()

        # Prefetched data caches (multicall)
        self.prefetched_balances: Dict[Tuple[str, str], Optional[int]] = {}
        self.prefetched_decimals: Dict[str, int] = {}
        self.prefetched_names: Dict[str, str] = {}
        self.prefetched_symbols: Dict[str, str] = {}
        self.prefetched_tokens: set[str] = set()
        self.prefetched_allowances: Dict[Tuple[str, str], Optional[int]] = {}
        self.prefetched_ens_reverse: Dict[str, str] = {}
        self.prefetch_ready: bool = False

        # Use the chain-specific KyberSwap endpoints from config
        self.kyberswap_api_route = chain_config.KYBERSWAP_API_ROUTE
        self.kyberswap_api_build = chain_config.KYBERSWAP_API_BUILD
        self.kyberswap_api_encode = chain_config.KYBERSWAP_API_ENCODE


        # Web3 provider (already initialized above)
        # self.w3 remains the rotating provider instance
        self.multicall = self.web3h.multicall



    def multicall_available(self):
        return self.multicall is not None

    def _build_rpc_urls(self, chain_config):
        """
        Build a list of RPC URLs from the chain_config and environment.
        - Supports multiple Alchemy keys in env var `ALCHEMY_API_KEYS` (comma-separated).
        - Always includes the single `ALCHEMY_RPC_URL` as fallback if present.
        - Includes any `EXTRA_RPC_URLS` (comma-separated full URLs).
        """
        urls = []
        # Base single URL from config
        base = getattr(chain_config, 'ALCHEMY_RPC_URL', None)
        if base:
            urls.append(str(base))

        # Expand Alchemy via multiple keys
        keys_raw = os.getenv('ALCHEMY_API_KEYS', '')
        keys = [k.strip() for k in keys_raw.split(',') if k.strip()]
        if keys:
            # If base looks like .../v2/{key}, expand using same host
            if base and '/v2/' in base:
                prefix = base.split('/v2/')[0] + '/v2/'
                urls.extend([prefix + k for k in keys])
            else:
                # No recognizable template; skip expansion
                pass

        # Add any explicit extra RPC URLs
        extras_raw = os.getenv('EXTRA_RPC_URLS', '')
        extras = [u.strip() for u in extras_raw.split(',') if u.strip()]
        urls.extend(extras)

        # De-duplicate while preserving order
        dedup = []
        seen = set()
        for u in urls:
            if u not in seen:
                dedup.append(u)
                seen.add(u)
        if not dedup:
            raise RuntimeError('No RPC URLs configured. Set ALCHEMY_API_KEY or EXTRA_RPC_URLS in .env')
        return dedup

    def _checksum(self, value: str) -> Optional[str]:
        if not value:
            return None
        try:
            return self.w3.to_checksum_address(value)
        except Exception:
            return None

    def _token_key(self, token: str) -> Optional[str]:
        checksum = self._checksum(token)
        if checksum:
            return checksum.lower()
        return str(token).lower() if token else None

    def _pair_key(self, wallet: str, token: str) -> Optional[Tuple[str, str]]:
        wallet_checksum = self._checksum(wallet)
        token_checksum = self._checksum(token)
        if not wallet_checksum or not token_checksum:
            return None
        return (wallet_checksum.lower(), token_checksum.lower())

    def _update_cached_balance(self, wallet: str, token: str, value: int) -> None:
        key = self._pair_key(wallet, token)
        if key:
            self.prefetched_balances[key] = int(value)

    def _update_cached_allowance(self, wallet: str, token: str, value: int) -> None:
        key = self._pair_key(wallet, token)
        if key:
            self.prefetched_allowances[key] = int(value) if value is not None else None

    def _get_prefetched_balance(self, wallet_address: str, token_address: str) -> Optional[int]:
        key = self._pair_key(wallet_address, token_address)
        if not key:
            return None
        value = self.prefetched_balances.get(key)
        return int(value) if value is not None else None

    def _get_prefetched_allowance(self, wallet_address: str, token_address: str) -> Optional[int]:
        key = self._pair_key(wallet_address, token_address)
        if not key:
            return None
        value = self.prefetched_allowances.get(key)
        return int(value) if value is not None else None

    def _get_prefetched_decimal(self, token_address: str) -> Optional[int]:
        token_key = self._token_key(token_address)
        if not token_key:
            return None
        val = self.prefetched_decimals.get(token_key)
        return int(val) if val is not None else None

    def prefetch_wallet_token_data(
        self,
        extra_tokens: Optional[List[str]] = None,
        *,
        include_allowance: bool = False,
        spender: Optional[str] = None,
        include_ens: bool = True,
    ) -> None:
        token_set = {str(addr).strip() for addr in self.tokens.values() if str(addr).strip()}
        if extra_tokens:
            for token in extra_tokens:
                if token:
                    token_set.add(str(token).strip())

        token_set.add(NATIVE_SENTINEL)
        native_cfg = getattr(self.chain_config, "NATIVE_TOKEN", None)
        if native_cfg:
            token_set.add(str(native_cfg).strip())

        wallets: List[str] = []
        for addr in self.wallet_addresses:
            checksum = self._checksum(addr)
            if checksum:
                wallets.append(checksum)

        normalized_tokens: List[str] = []
        token_keys: set[str] = set()
        for token in token_set:
            checksum = self._checksum(token)
            if not checksum:
                continue
            key = checksum.lower()
            if key not in token_keys:
                token_keys.add(key)
                normalized_tokens.append(checksum)

        if not wallets or not normalized_tokens:
            self.prefetch_ready = False
            return

        spender_checksum = self._checksum(spender) if include_allowance and spender else None

        ens_names_forward = [name for name in self.wallet_ens_names.values() if name] if include_ens else []
        try:
            result = self.web3h.multicall_fetch(
                wallets=wallets,
                tokens=normalized_tokens,
                spender=spender_checksum,
                ens_names=ens_names_forward or None,
                use_multicall=bool(self.web3h.multicall),
                want_balance=True,
                want_decimals=True,
                with_name=True,
                with_symbol=True,
                want_ens=include_ens,
                want_allowance=include_allowance,
            )
        except Exception as e:
            self.console.log(f"[yellow]Token prefetch via multicall_fetch failed: {e}[/yellow]")
            return

        wallets_resolved = result.get("wallets_resolved", []) or []
        if wallets_resolved:
            merged_wallets: List[str] = []
            seen_wallets: Set[str] = set()

            def _merge(addr: Optional[str]) -> None:
                if not addr:
                    return
                checksum = self._checksum(addr) or (addr if isinstance(addr, str) else str(addr))
                key = checksum.lower() if isinstance(checksum, str) else str(checksum)
                if key in seen_wallets:
                    return
                seen_wallets.add(key)
                merged_wallets.append(checksum)

            for addr in self.wallet_addresses:
                _merge(addr)
            for addr in wallets_resolved:
                _merge(addr)
            for wallet_addr, _ in (result.get("balances", {}) or {}).keys():
                _merge(wallet_addr)

            self.wallet_addresses = merged_wallets
            wallets = merged_wallets
            self.web3h.wallet_addresses = list(merged_wallets)

        balances_raw = result.get("balances", {})
        decimals_raw = result.get("decimals", {})
        names_raw = result.get("names", {})
        symbols_raw = result.get("symbols", {})
        allowances_raw = result.get("allowance", {}) if include_allowance else {}
        ens_reverse_raw = result.get("ens_reverse", {}) if include_ens else {}

        new_balances: Dict[Tuple[str, str], Optional[int]] = {}
        for (wallet, token), value in balances_raw.items():
            key = self._pair_key(wallet, token)
            if key:
                new_balances[key] = int(value) if value is not None else None

        new_allowances: Dict[Tuple[str, str], Optional[int]] = {}
        for (wallet, token), value in allowances_raw.items():
            key = self._pair_key(wallet, token)
            if key:
                new_allowances[key] = int(value) if value is not None else None

        new_decimals: Dict[str, int] = {}
        for token, value in decimals_raw.items():
            token_key = self._token_key(token)
            if token_key and value is not None:
                new_decimals[token_key] = int(value)

        new_names: Dict[str, str] = {}
        for token, value in names_raw.items():
            token_key = self._token_key(token)
            if token_key and value:
                new_names[token_key] = value

        new_symbols: Dict[str, str] = {}
        for token, value in symbols_raw.items():
            token_key = self._token_key(token)
            if token_key and value:
                new_symbols[token_key] = value

        self.prefetched_balances = new_balances
        if include_allowance:
            self.prefetched_allowances = new_allowances
        self.prefetched_decimals = new_decimals
        self.prefetched_names = new_names
        self.prefetched_symbols = new_symbols
        token_keys_result = {self._token_key(t) for t in normalized_tokens if self._token_key(t)}
        for token in token_set:
            key = self._token_key(token)
            if key:
                token_keys_result.add(key)
        self.prefetched_tokens = token_keys_result
        self._refresh_token_labels()
        self.prefetch_ready = bool(self.prefetched_balances or self.prefetched_decimals or self.prefetched_allowances)

        if include_ens:
            ens_map: Dict[str, str] = {}
            for wallet, name in ens_reverse_raw.items():
                checksum = self._checksum(wallet)
                if checksum and name:
                    ens_map[checksum] = name
            if ens_map:
                self.prefetched_ens_reverse = ens_map
                for addr, name in ens_map.items():
                    self.wallet_ens_names[addr] = name
                self.web3h.ens_names = [name for name in self.wallet_ens_names.values() if name]

        if self.prefetch_ready:
            self.console.log(f"[green]Prefetched {len(normalized_tokens)} tokens via multicall[/green]")

    def _ensure_prefetched_token(self, token_address: str) -> None:
        token_key = self._token_key(token_address)
        if not token_key:
            return
        if token_key not in self.prefetched_tokens:
            self.prefetch_wallet_token_data(
                extra_tokens=[token_address],
                include_allowance=True,
                spender=config.KYBER_ROUTER,
                include_ens=False,
            )

    def _log_loaded_wallets(self) -> None:
        if not self.wallet_addresses:
            return
        self.console.rule("[bold cyan]Loaded wallets[/bold cyan]")
        for idx, addr in enumerate(self.wallet_addresses, start=1):
            checksum = self._checksum(addr) or addr
            name = self.prefetched_ens_reverse.get(checksum)
            if not name:
                name = self.wallet_ens_names.get(checksum)
                if not name and checksum in self.wallet_ens_names:
                    name = self.wallet_ens_names[checksum]
            if name:
                self.console.log(f"[green]#{idx}[/green] {name} ({checksum})")
            else:
                self.console.log(f"[green]#{idx}[/green] {checksum}")

    def _log_loaded_tokens(self) -> None:
        if not self.tokens:
            return
        self.console.rule("[bold cyan]Loaded tokens[/bold cyan]")
        for label, address in self.tokens.items():
            symbol_from_label = label.split(' (')[0]
            token_key = self._token_key(address)
            symbol = self.prefetched_symbols.get(token_key, symbol_from_label) if token_key else symbol_from_label
            name = self.prefetched_names.get(token_key) if token_key else None
            checksum = self._checksum(address) or address
            extra = f" ({name})" if name else ""
            self.console.log(f"[magenta]{symbol}[/magenta] -> {checksum}{extra}")

    def select_token_input_method(self) -> OrderedDict[str, str]:
        if self.is_linux:
            choices = ["Default path (file)", "Manual input (CLI)"]
        else:
            choices = ["Default path (file)", "Manual input (GUI)"]

        choice = questionary.select(
            "Choose token contract input method:",
            choices=choices
        ).ask()

        if not choice:
            self.console.log("[yellow]No selection made, falling back to file input.[/yellow]")
            choice = "Default path (file)"

        loaders = {
            "Default path (file)": (self.web3h.load_tokens_file, (self.contracts_file,)),
            "Manual input (CLI)": (self.web3h.load_tokens_cli, ()),
            "Manual input (GUI)": (self.web3h.load_tokens_gui, ()),
        }

        loader, args = loaders.get(choice, loaders["Default path (file)"])

        try:
            tokens = loader(*args)
        except Exception as exc:
            self.console.log(f"[red]Failed to load tokens via {choice}: {exc}[/red]")
            tokens = []

        if tokens is None:
            tokens = []

        self._ingest_token_addresses(tokens)

        if not self.tokens:
            self.console.log("[yellow]No token contracts loaded.[/yellow]")

        return self.tokens
    
    def create_placeholder_file(self, file_path, content_type):
        """Create a placeholder file if it doesn't exist (delegates to FileHelper)."""
        try:
            FileHelper.ensure_placeholder(file_path, content_type)
        except Exception as e:
            self.console.log(f"[yellow]Could not ensure placeholder {file_path}: {e}[/yellow]")

    def _ingest_token_addresses(self, addresses: List[str]) -> None:
        token_map: OrderedDict[str, str] = OrderedDict()
        seen: set[str] = set()

        for raw in addresses or []:
            checksum = self._checksum(raw)
            if not checksum:
                continue
            lower = checksum.lower()
            if lower in seen:
                continue
            seen.add(lower)

            label = self._format_token_label(checksum)
            base_label = label
            suffix = 2
            while label in token_map:
                label = f"{base_label} #{suffix}"
                suffix += 1
            token_map[label] = checksum

        self.tokens = token_map
        self._refresh_token_labels()

    def _format_token_label(self, address: str) -> str:
        checksum = self._checksum(address) or str(address)
        token_key = self._token_key(checksum)
        symbol = self.prefetched_symbols.get(token_key) if token_key else None
        name = self.prefetched_names.get(token_key) if token_key else None

        native_aliases = {NATIVE_SENTINEL.lower()}
        native_checksum = self._checksum(self.native_token)
        if native_checksum:
            native_aliases.add(native_checksum.lower())

        if checksum.lower() in native_aliases:
            symbol = symbol or self.chain_name.upper() if hasattr(self, "chain_name") else "NATIVE"
            name = name or "Native Token"

        if symbol:
            return f"{symbol} ({checksum})"
        if name:
            return f"{name} ({checksum})"
        return checksum

    def _refresh_token_labels(self) -> None:
        if not self.tokens:
            return

        updated: OrderedDict[str, str] = OrderedDict()
        seen_labels: set[str] = set()

        for label, address in self.tokens.items():
            new_label = label
            if not label.lower().startswith("custom"):
                new_label = self._format_token_label(address)

            base_label = new_label
            suffix = 2
            while new_label in seen_labels:
                new_label = f"{base_label} #{suffix}"
                suffix += 1

            seen_labels.add(new_label)
            updated[new_label] = address

        self.tokens = updated

    def _store_wallet_addresses(self, addresses: List[str], ens_names: Optional[List[str]] = None) -> None:
        self.wallet_addresses = []
        self.wallet_ens_names = {}
        self.wallet_unresolved_ens = []

        ens_list = list(ens_names or [])
        seen: Set[str] = set()

        def _push(addr: Optional[str], label: Optional[str] = None) -> None:
            if not addr:
                return
            checksum = self._checksum(addr) or (addr if isinstance(addr, str) else str(addr))
            if not checksum:
                return
            key = checksum.lower() if isinstance(checksum, str) else str(checksum)
            if key in seen:
                if label and checksum not in self.wallet_ens_names:
                    self.wallet_ens_names[checksum] = label
                return
            seen.add(key)
            self.wallet_addresses.append(checksum)
            if label:
                self.wallet_ens_names[checksum] = label
            else:
                self.wallet_ens_names.setdefault(checksum, None)

        candidate_ens: List[str] = []
        for addr in addresses or []:
            checksum = self._checksum(addr)
            if checksum:
                _push(checksum)
            elif isinstance(addr, str) and addr.strip():
                candidate_ens.append(addr.strip())

        all_names: List[str] = []
        for name in ens_list:
            if name:
                all_names.append(name)
        for name in candidate_ens:
            if name:
                all_names.append(name)

        resolved_map: Dict[str, Optional[str]] = {}
        if all_names:
            unique_names = []
            seen_names = set()
            for name in all_names:
                if name not in seen_names:
                    seen_names.add(name)
                    unique_names.append(name)
            try:
                resolved_map = self.web3h.batch_ens_forward(unique_names, use_multicall=True) or {}
            except Exception as exc:
                self.console.log(f"[yellow]ENS forward resolution failed: {exc}[/yellow]")
                resolved_map = {}
            for name in unique_names:
                resolved = resolved_map.get(name)
                if resolved:
                    _push(resolved, label=name)
                else:
                    self.wallet_unresolved_ens.append(name)

        if self.wallet_addresses:
            try:
                reverse_map = self.web3h.batch_ens_reverse(self.wallet_addresses, use_multicall=True) or {}
            except Exception as exc:
                self.console.log(f"[yellow]ENS reverse lookup failed: {exc}[/yellow]")
                reverse_map = {}
            for addr, name in reverse_map.items():
                checksum = self._checksum(addr) or addr
                if checksum and name:
                    self.wallet_ens_names[checksum] = name

        for addr, name in self.wallet_ens_names.items():
            if not name:
                continue
            checksum = self._checksum(addr) or addr
            if checksum:
                self.prefetched_ens_reverse[checksum] = name

        if self.wallet_unresolved_ens:
            unresolved = ', '.join(self.wallet_unresolved_ens)
            self.console.log(f"[yellow]Unresolved wallet ENS entries: {unresolved}[/yellow]")

        ens_values = [name for name in self.wallet_ens_names.values() if name]
        self.web3h.wallet_addresses = list(self.wallet_addresses)
        self.web3h.ens_names = list(dict.fromkeys(ens_values))

    

    def select_wallet_input_method(self) -> Tuple[List[str], List[str]]:
        if self.is_linux:
            choices = ["Default path (file)", "Manual input (CLI)"]
        else:
            choices = ["Default path (file)", "Manual input (GUI)"]

        choice = questionary.select(
            "Choose private key input method:",
            choices=choices
        ).ask()

        if not choice:
            self.console.log("[yellow]No selection made, falling back to file input.[/yellow]")
            choice = "Default path (file)"

        loaders = {
            "Default path (file)": (self.web3h.load_privatekeys_file, (self.wallet_file,)),
            "Manual input (CLI)": (self.web3h.load_privatekeys_cli, ()),
            "Manual input (GUI)": (self.web3h.load_privatekeys_gui, ()),
        }

        loader, args = loaders.get(choice, loaders["Default path (file)"])

        try:
            keys, addresses = loader(*args)
        except Exception as exc:
            self.console.log(f"[red]Failed to load private keys via {choice}: {exc}[/red]")
            keys, addresses = [], []

        if keys is None:
            keys = []
        if addresses is None:
            addresses = []

        self._store_private_keys(keys, addresses)

        if not self.wallet_private_keys:
            self.console.log("[yellow]No private keys loaded.[/yellow]")

        return self.wallet_private_keys, addresses

    def _store_private_keys(self, keys: List[str], addresses: List[str]) -> None:
        if not keys:
            self.console.log("[bold red]No private keys provided. Exiting.[/bold red]")
            sys.exit(1)
        self.wallet_private_keys = list(keys)
        self._store_wallet_addresses(addresses)
        self.console.log(f"[bold blue]Total private keys loaded: {len(self.wallet_private_keys)}[/bold blue]")

    def check_token_balance(self, token_address, account_address):
        """Check the balance of a specific token for a given account (helper-backed)."""
        try:
            self._ensure_prefetched_token(token_address)

            raw_pref = self._get_prefetched_balance(account_address, token_address)

            decimals = self._get_prefetched_decimal(token_address)

            raw_value = raw_pref
            if raw_value is None:
                raw_value = self.web3h.check_token_balance(token_address, account_address)

            if raw_value is None:
                return None, None, decimals

            raw_int = int(raw_value)

            self._update_cached_balance(account_address, token_address, raw_int)
            token_key = self._token_key(token_address)
            if token_key:
                self.prefetched_tokens.add(token_key)

            if decimals is None:
                native_aliases = {NATIVE_SENTINEL.lower()}
                native_checksum = self._checksum(self.native_token)
                if native_checksum:
                    native_aliases.add(native_checksum.lower())
                checksum_token = self._checksum(token_address)
                token_lower = checksum_token.lower() if checksum_token else str(token_address).lower()
                if token_lower in native_aliases:
                    decimals = 18
                else:
                    try:
                        erc20 = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.web3h.erc20_abi)
                        decimals = int(erc20.functions.decimals().call())
                    except Exception:
                        decimals = 18

            if token_key and decimals is not None:
                self.prefetched_decimals.setdefault(token_key, decimals)

            human = (raw_int / (10 ** decimals)) if decimals is not None else None
            return raw_int, human, decimals
        except Exception as e:
            self.console.log(f"[bold red]Error in check_token_balance: {str(e)}[/bold red]")
            raise

    def fetch_suggested_fees(self):
        """Fetch suggested gas fees with helper and a tier selector."""
        tier_choice = questionary.select(
            "Select gas tier to use:",
            choices=["low", "medium", "high"]
        ).ask()

        if not tier_choice:
            self.console.log("[yellow]No tier selected. Falling back to 'medium'.[/yellow]")
            tier_choice = "medium"

        max_fee, max_prio = self.web3h.fetch_suggested_fees(self.INFURA_GAS_API_URL, tier=tier_choice.lower())
        if (max_fee is None) or (max_prio is None):
            self.console.log("[yellow]Gas fee suggestion unavailable; consider manual input[/yellow]")
        return max_fee, max_prio

    def wait_receipt_slow(self, tx_hash, timeout=300, start_delay=2, max_delay=8):
        return self.web3h.wait_for_receipt(
            tx_hash,
            timeout=timeout,
            start_delay=start_delay,
            max_delay=max_delay,
        )

    def send_approval_transaction(self, private_key, token_address, spender, amount, max_fee_per_gas, max_priority_fee_per_gas, approval_mode=None):
        """Approve the KyberSwap router (spender) to spend the specified token.

        approval_mode:
          - None: interactively ask user (current behavior)
          - 'exact': approve exactly the amount
          - 'unlimited': approve MAX_UINT256
        """
        try:
            account = Account.from_key(private_key)
            abi = json.loads(self.chain_config.TOKEN_ABI)
            token_contract = self.w3.eth.contract(address=token_address, abi=abi)

            if approval_mode is None:
                approval_choice = questionary.select(
                    "Do you want to approve the exact amount or unlimited amount?",
                    choices=["Exact amount", "Unlimited amount"]
                ).ask()
                if approval_choice == "Exact amount":
                    approval_amount = int(amount + 1)
                else:
                    approval_amount = 2**256 - 1
            else:
                if approval_mode == 'exact':
                    approval_amount = int(amount + 1)
                else:
                    approval_amount = 2**256 - 1

            tx_hash = self.web3h.send_approval(
                private_key=private_key,
                token_address=token_address,
                spender=spender,
                amount=approval_amount,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
            )
            self.console.log(f"[green]Approval transaction sent: {tx_hash.hex()}[/green]")

            receipt = self.wait_receipt_slow(tx_hash)
            
            if receipt['status'] == 1:
                self.console.log("[bold green]Approval transaction confirmed successfully![/bold green]")
            else:
                self.console.log("[bold red]Approval transaction failed![/bold red]")

        except Exception as e:
            self.console.log(f"[bold red]Error in send_approval_transaction: {e}[/bold red]")
            raise

    def _get_token_meta(self, token_address):
        if token_address not in self._token_meta:
            if token_address.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" :
                self._token_meta[token_address] = {"decimals": 18 , "name" : "Native Token", "version" : "1"}
            else :
                abi = json.loads(self.chain_config.TOKEN_ABI)
                c = self.w3.eth.contract(address=token_address , abi=abi)
                try:
                    name = c.functions.name().call()
                except Exception:
                    name = "Unknown"
                try:
                    version = c.functions.version().call()
                except Exception:
                    version= "1"

                decimals = c.functions.decimals().call()
                self._token_meta[token_address] = {"decimals" : decimals, "name" : name, "version": version}
        return self._token_meta[token_address]

    def check_eip2612_support(self, token_address, owner_address):
        """True if token's permit path is usable (EIP-2612-style)."""
        self.console.log("[yellow]Checking EIP-2612 support.[/yellow]")
        try:
            if token_address in self._permit_support_cache:
                return self._permit_support_cache[token_address]

            token_checksum = self._checksum(token_address)
            native_aliases = {NATIVE_SENTINEL.lower()}
            native_checksum = self._checksum(self.native_token)
            if native_checksum:
                native_aliases.add(native_checksum.lower())
            if token_checksum and token_checksum.lower() in native_aliases:
                self.console.log("[yellow]Native assets do not require permit approvals.[/yellow]")
                self._permit_support_cache[token_address] = False
                return False

            if not hasattr(self.chain_config, 'MINIMAL_ABI_PERMIT'):
                self.console.log("[red]No MINIMAL_ABI_PERMIT in chain config[/red]")
                self._permit_support_cache[token_address] = False
                return False

            minimal_abi = json.loads(self.chain_config.MINIMAL_ABI_PERMIT)
            contract_address = self.w3.to_checksum_address(token_checksum or token_address)
            c = self.w3.eth.contract(address=contract_address, abi=minimal_abi)

            # Ensure the function exists in ABI (no network call)
            try:
                c.get_function_by_name('permit')
                self.console.log("[green]Found permit function in ABI[/green]")
            except ABIFunctionNotFound:
                self.console.log("[red]No permit() in ABI[/red]")
                self._permit_support_cache[token_address] = False
                return False

            # One on-chain probe only: try nonces(owner) then nonces()
            try:
                _ = c.functions.nonces(owner_address).call()
                self.console.log("[green]nonces(owner) works[/green]")
            except Exception:
                try:
                    _ = c.functions.nonces().call()
                    self.console.log("[green]nonces() works[/green]")
                except Exception:
                    self.console.log("[red]No working nonces; treating as non-permit[/red]")
                    self._permit_support_cache[token_address] = False
                    return False

            self._permit_support_cache[token_address] = True
            self.console.log("[bold green]Token usable with EIP-2612[/bold green]")
            return True

        except Exception as e:
            self.console.log(f"[bold red]Error checking EIP-2612 support: {e}[/bold red]")
            self._permit_support_cache[token_address] = False
            return False


    def get_permit_data(self, token_address, owner, spender, value, deadline, private_key):
        """Generate permit data for EIP-2612 approval."""
        try:
            # Some chains might not define ERC20_PERMIT_ABI
            if not hasattr(self.chain_config, 'ERC20_PERMIT_ABI'):
                self.console.log("[red]This chain config does not have ERC20_PERMIT_ABI defined[/red]")
                return None

            erc20_abi = json.loads(self.chain_config.ERC20_PERMIT_ABI)
            token = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=erc20_abi)
            

            meta = self._get_token_meta(token_address)
            name, version = meta["name"] , meta["version"]

            # nonce
            try:
                nonce = token.functions.nonces(owner).call()
                self.console.log(f"[green]Got nonce using nonces(address): {nonce}[/green]")
            except Exception :
                nonce = token.functions.nonces().call()

            domain = {
                "name": name,
                "version" : version,
                "chainId" : self.chain_id,
                "verifyingContract" : self.w3.to_checksum_address(token_address) 
            }
            typed_data = {
                "types" : {
                    "EIP712Domain" : [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"}
                    ],
                    "Permit" : [
                        {"name": "owner", "type": "address"},
                        {"name": "spender", "type": "address"},
                        {"name": "value", "type": "uint256"},
                        {"name": "nonce", "type": "uint256"},
                        {"name": "deadline", "type": "uint256"}
                    ]
                },
                "primaryType" : "Permit",
                "domain" : domain, 
                "message" : {
                    "owner" : owner,                      
                    "spender" : spender,
                    "value" : int(value),
                    "nonce" : int(nonce),
                    "deadline" : int(deadline)
                    },
            }    

            #--- sign EIP-712
            encoded_msg = encode_structured_data(typed_data)
            signed = Account.sign_message(encoded_msg, private_key=private_key)
            v, r, s = signed.v, signed.r, signed.s

            # r & s must be bytes32 for the ABI
            r_bytes = int(r).to_bytes(32, byteorder='big')
            s_bytes = int(s).to_bytes(32, byteorder='big')

            return token.encodeABI(
                "permit",
                args=[owner , spender, int(value), int(deadline), int(v), r_bytes, s_bytes],    
            )
             
        except json.JSONDecodeError as e:
            self.console.log(f"[bold red]Error parsing ERC20_PERMIT_ABI: {str(e)}[/bold red]")
            return None
        except Exception as e:
            self.console.log(f"[bold red]Token does not support EIP-2612 permits: {e}[/bold red]")
            return None

    def check_allowance(self, token_address, owner_address, spender_address):
        """Check the current allowance of the spender for the owner's token (helper-backed)."""
        try:
            if token_address.lower() == '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee':
                return float('inf')  # Native token doesn't need allowance
            return self.web3h.check_allowance(token_address, owner_address, spender_address)
        except Exception as e:
            self.console.log(f"[bold red]Error checking allowance: {e}[/bold red]")
            raise

    def get_swap_route(self, chain, token_in, token_out, amount_in):
        """
        Fetch the best swap route from KyberSwap Aggregator API for the selected chain.
        We'll also uncomment the fee logic so you can optionally define fee_amount > 0 if you want to charge fees.
        """
        url = self.kyberswap_api_route
        headers = self.KYBERSWAP_API_HEADERS.copy()
        headers["source"] = headers.get("x-client-id", "")

        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amountIn": amount_in,
            "deadline": int(time.time()) + 1200,  # 20 min
            "slippageTolerance": 50  # 0.5%
        }

        # Un-commented fee logic:
        fee_amount = 0  # <-- set this if you want to charge fees
        if fee_amount > 0:
            params["feeAmount"] = fee_amount
            params["chargeFeeBy"] = "currency_in"  # or "currency_out"

        # Remove empty keys
        params = {k: v for k, v in params.items() if v not in [None, "", []]}

        try:
            # Log the fully prepared URL for easier debugging of query formatting
            try:
                prepared = requests.Request('GET', url, params=params, headers=headers).prepare()
                self.console.log(f"[yellow]Requesting route: {prepared.url}[/yellow]")
            except Exception:
                self.console.log(f"[yellow]Requesting route: {url} with params={params}[/yellow]")

            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            route = response.json()
            if route.get("code") == 0:
                self.console.log("[bold green]Fetched swap route successfully.[/bold green]")
                return route
            else:
                self.console.log(f"[bold red]Failed to fetch swap route: {route.get('message')}[/bold red]")
                self.console.log(f"[yellow]Error code: {route.get('code')}[/yellow]")
                self.console.log(f"[yellow]Response data: {response.text}[/yellow]")
                return None
        except requests.exceptions.RequestException as e:
            self.console.log(f"[bold red]Error fetching swap route: {str(e)}[/bold red]")
            if hasattr(e, 'response') and e.response is not None:
                self.console.log(f"[yellow]Response status code: {e.response.status_code}[/yellow]")
                self.console.log(f"[yellow]Response text: {e.response.text}[/yellow]")
            return None

    def get_encoded_swap_data(self, chain , route_summary, tx_params):
        """
        Retrieve the calldata needed to execute the swap from the aggregator's /route/build endpoint.
        """
        url = self.kyberswap_api_build
        headers = self.KYBERSWAP_API_HEADERS.copy()
        headers["source"] = headers.get("x-client-id", "")

        payload = {
            "routeSummary": route_summary,
            **tx_params
        }

        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            encoded_data = response.json()
            if encoded_data.get("code") == 0:
                self.console.log("[bold green]Fetched encoded swap data successfully.[/bold green]")
                #self.console.log(f"[yellow]Encoded Data: {json.dumps(encoded_data, indent=2)}[/yellow]")
                return encoded_data
            else:
                self.console.log(f"[bold red]Failed to fetch encoded swap data: {encoded_data.get('message')}[/bold red]")
                self.console.log(f"[yellow]Error code: {encoded_data.get('code')}[/yellow]")
                self.console.log(f"[yellow]Response data: {response.text}[/yellow]")
                return None
        except requests.exceptions.RequestException as e:
            self.console.log(f"[bold red]Error fetching encoded swap data: {e}[/bold red]")
            return None

    def get_swap_info_with_encoded_data(self, encoded_data):
        """(Optional) Retrieve more info from encoded data if there's a separate /route/encode endpoint."""
        url = self.kyberswap_api_encode
        headers = self.KYBERSWAP_API_HEADERS.copy()
        headers["source"] = headers.get("x-client-id", "")
        payload = {"encodedData": encoded_data}

        try:
            self.console.log(f"[yellow]Request URL: {url}[/yellow]")
            self.console.log(f"[yellow]Request Payload: {json.dumps(payload, indent=2)}[/yellow]")

            response = requests.post(url, json=payload, headers=headers)
            self.console.log(f"[yellow]Response Status Code: {response.status_code}[/yellow]")
            self.console.log(f"[yellow]Response Text: {response.text}[/yellow]")
            response.raise_for_status()

            swap_info = response.json()
            if swap_info.get("code") == 0:
                self.console.log("[bold green]Fetched swap info successfully.[/bold green]")
                return swap_info
            else:
                self.console.log(f"[bold red]Failed to fetch swap info: {swap_info.get('message')}[/bold red]")
                self.console.log(f"[yellow]Error code: {swap_info.get('code')}[/yellow]")
                self.console.log(f"[yellow]Response data: {response.text}[/yellow]")
                return None
        except requests.exceptions.RequestException as e:
            self.console.log(f"[bold red]Error fetching swap info: {e}[/bold red]")
            if hasattr(e, 'response') and e.response is not None:
                self.console.log(f"[yellow]Response status code: {e.response.status_code}[/yellow]")
                self.console.log(f"[yellow]Response text: {e.response.text}[/yellow]")
            return None

    def execute_swap(self, private_key, encoded_data, router_address , from_token , amount_in_wei , max_fee_per_gas , max_priority_fee_per_gas):
        """Send the swap transaction to the KyberSwap router contract."""
        if not max_fee_per_gas or not max_priority_fee_per_gas:
            self.console.log("[bold red]Could not fetch valid gas fees. Aborting swap.[/bold red]")
            return

        try:
            account = Account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)

            self.console.log(f"[debug]Executing swap for router_address: {router_address}[/debug]")
            calldata = encoded_data.get("data", {}).get("data")
            gas_detail = encoded_data.get("data", {}).get("gas")

            if not calldata:
                self.console.log("[bold red]Calldata is missing in encoded swap data. Aborting swap.[/bold red]")
                return

            # Clean up
            calldata = calldata.replace('\n', '').replace(' ', '')
            if not calldata.startswith('0x'):
                self.console.log("[bold red]Invalid calldata format. Aborting swap.[/bold red]")
                return
            
            # If from_token is 0xEeeeeEeee... => native coin => tx["value"] = amount_in_wei
            if from_token.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                tx_value = amount_in_wei
            else:
                tx_value = 0  # Standard ERC-20 swap => no value in the transaction

            tx = {
                'chainId': self.chain_id,
                'from': account.address,
                'to': router_address,
                'nonce': nonce,
                'gas': int(gas_detail) if gas_detail else 21000,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': max_priority_fee_per_gas,
                'data': calldata,
                'value': tx_value
            }

            self.console.log(f"[debug]Transaction Params: {json.dumps(tx, indent=2)}[/debug]")

            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            self.console.log(f"[green]Swap transaction sent: {tx_hash.hex()}[/green]")

            receipt = self.wait_receipt_slow(tx_hash, timeout=300)
            if receipt['status'] == 1:
                self.console.log("[bold green]Swap successful![/bold green]")
            else:
                self.console.log("[bold red]Swap failed![/bold red]")

        except Exception as e:
            self.console.log(f"[bold red]Error executing swap: {e}[/bold red]")

    def swap_tokens_kyberswap(self, private_key):
        """Main function to handle token swapping via KyberSwap."""
        account = Account.from_key(private_key)
        sender = account.address
        recipient = account.address  # can be changed if needed    

        # 1. Select tokens (with manual contract address option)
        from_token_choices = list(self.tokens.keys()) + ["[Enter contract address manually]"]
        from_token_full = questionary.select(
            "Select the token you want to swap from:",
            choices=from_token_choices
        ).ask()
        if from_token_full == "[Enter contract address manually]":
            manual_from_token = questionary.text(
                "Enter the contract address of the token you want to swap from:"
            ).ask()
            # Validate address format
            try:
                manual_from_token = self.w3.to_checksum_address(manual_from_token.strip())
            except Exception:
                self.console.log("[bold red]Invalid contract address entered. Aborting.[/bold red]")
                return
            from_token_full = f"Custom ({manual_from_token})"
            self.tokens[from_token_full] = manual_from_token

        to_token_choices = [symbol for symbol in self.tokens.keys() if symbol != from_token_full] + ["[Enter contract address manually]"]
        to_token_full = questionary.select(
            "Select the token you want to swap to:",
            choices=to_token_choices
        ).ask()
        if to_token_full == "[Enter contract address manually]":
            manual_to_token = questionary.text(
                "Enter the contract address of the token you want to swap to:"
            ).ask()
            # Validate address format
            try:
                manual_to_token = self.w3.to_checksum_address(manual_to_token.strip())
            except Exception:
                self.console.log("[bold red]Invalid contract address entered. Aborting.[/bold red]")
                return
            to_token_full = f"Custom ({manual_to_token})"
            self.tokens[to_token_full] = manual_to_token

        from_token = self.tokens[from_token_full]
        to_token = self.tokens[to_token_full]
        from_token_symbol = from_token_full.split(' (')[0]
        to_token_symbol = to_token_full.split(' (')[0]

        self._ensure_prefetched_token(from_token)
        self._ensure_prefetched_token(to_token)

        # 2. Check balance
        try:
            balance_raw, human_readable_balance, decimals = self.check_token_balance(from_token, sender)
            self.console.log(f"[bold blue]Your balance of {from_token_symbol}: {human_readable_balance}[/bold blue]")
            if balance_raw <= 0:
                self.console.log("[bold red]Error: Zero balance for the input token[/bold red]")
                return
        except Exception as e:
            self.console.log(f"[bold red]Error fetching token balance: {e}[/bold red]")
            return

        # 3. Get Amount to Swap
        amount_choice = questionary.select(
            "Choose amount input method:",
            choices=["Enter fixed amount", "Enter based %"]
        ).ask()

        if amount_choice == "Enter fixed amount":
            amount = questionary.text(
                f"Enter the amount of {from_token_symbol} to swap (max {human_readable_balance}):"
            ).ask()
            try:
                amount_float = float(amount)
                if amount_float > human_readable_balance:
                    self.console.log(f"[bold red]Error: Insufficient balance[/bold red]")
                    return
                amount_in_wei = int(amount_float * (10 ** decimals))
            except ValueError:
                self.console.log(f"[bold red]Error: Invalid amount entered[/bold red]")
                return
        else:
            percentage = questionary.text(
                f"Enter how much (%) of {from_token_symbol} balance you want to swap (1-100):"
            ).ask()
            try:
                percentage_float = float(percentage)
                if not 0 < percentage_float <= 100:
                    self.console.log(f"[bold red]Error: Percentage must be between 1 and 100[/bold red]")
                    return
                amount_float = (percentage_float / 100) * human_readable_balance
                self.console.log(f"[bold blue]Amount to swap: {amount_float} {from_token_symbol}[/bold blue]")
                amount_in_wei = int(amount_float * (10 ** decimals))
            except ValueError:
                self.console.log(f"[bold red]Error: Invalid percentage entered[/bold red]")
                return

        # 4. Slippage
        slippage_choice = questionary.select(
            "Choose slippage setting:",
            choices=["Default (0.5%)", "Custom"]
        ).ask()
        if slippage_choice == "Custom":
            slippage = questionary.text("Enter slippage tolerance % (e.g., 0.5 for 0.5%):").ask()
            try:
                slippage_float = float(slippage) / 100
            except ValueError:
                self.console.log("[bold red]Invalid slippage value. Using default 0.5%[/bold red]")
                slippage_float = 0.005
        else:
            slippage_float = 0.005

        # 5. Fetch gas fees
        max_fee_per_gas, max_priority_fee_per_gas = self.fetch_suggested_fees()
        if not max_fee_per_gas or not max_priority_fee_per_gas:
            self.console.log("[bold red]Could not fetch valid gas fees. Aborting swap.[/bold red]")
            return

        # 6. Fetch swap route
        route = self.get_swap_route(
            chain=self.chain_config.CHAIN_NAME,
            token_in=from_token,
            token_out=to_token,
            amount_in=amount_in_wei
        )
        if not route:
            self.console.log("[bold red]Failed to fetch swap route. Aborting swap.[/bold red]")
            return

        data = route.get("data")
        if not data:
            self.console.log("[bold red]No data found in route response. Aborting swap.[/bold red]")
            return

        route_summary = data.get("routeSummary")
        router_address = data.get("routerAddress")


        if not router_address:
            self.console.log("[bold red]Router address not found. Aborting swap.[/bold red]")
            return
        if not route_summary:
            self.console.log("[bold red]Incomplete route data received. Aborting swap.[/bold red]")
            return

        self.console.log(f"[bold green]KyberSwap Router Address: {router_address}[/bold green]")

        # 7. Check allowance

        try:
            permit_data = None
            if str(from_token).lower() == NATIVE_SENTINEL:
                self.console.log("[green]Native token - no allowance needed[/green]")
            else:
                allowance = self._get_prefetched_allowance(sender, from_token)
                if allowance is None:
                    allowance = self.check_allowance(from_token, sender, config.KYBER_ROUTER)
                    if allowance is not None:
                        self._update_cached_allowance(sender, from_token, allowance)
                if allowance is None:
                    allowance = 0
                    self._update_cached_allowance(sender, from_token, allowance)

                decimals_safe = decimals or 18
                allowance_human = allowance / (10 ** decimals_safe)
                required_allowance_human = amount_in_wei / (10 ** decimals_safe)

                self.console.log(f"[bold blue]Current Allowance: {allowance_human} {from_token_symbol}[/bold blue]")
                self.console.log(f"[bold blue]Required Allowance: {required_allowance_human} {from_token_symbol}[/bold blue]")

                if allowance < amount_in_wei:
                    self.console.log("[yellow]Insufficient allowance. Approving tokens...[/yellow]")
                    supports_permit = self.check_eip2612_support(from_token, sender)
                    if supports_permit:
                        self.console.log("[green]Token supports EIP-2612. Using permit for approval.[/green]")
                        deadline = int(time.time()) + 1200
                        permit_data = self.get_permit_data(
                            token_address=from_token,
                            owner=sender,
                            spender=config.KYBER_ROUTER,
                            value=amount_in_wei,
                            deadline=deadline,
                            private_key=private_key
                        )
                        if permit_data:
                            self._update_cached_allowance(sender, from_token, amount_in_wei)
                            self.console.log("[bold green]Permit data generated successfully.[/bold green]")
                        else:
                            self.console.log("[bold red]Failed to generate permit data. Aborting swap.[/bold red]")
                            return
                    else:
                        self.console.log("[bold yellow]Token does not support EIP-2612. "
                                        "Proceeding with traditional approval.[/bold yellow]")
                        if not questionary.confirm("Do you want to proceed with the approval transaction?").ask():
                            self.console.log("[yellow]Approval cancelled by user[/yellow]")
                            return

                        self.send_approval_transaction(
                            private_key=private_key,
                            token_address=from_token,
                            spender=config.KYBER_ROUTER,
                            amount=amount_in_wei,
                            max_fee_per_gas=max_fee_per_gas,
                            max_priority_fee_per_gas=max_priority_fee_per_gas
                        )
                        self._update_cached_allowance(sender, from_token, 2 ** 256 - 1)
                        refreshed = self.check_allowance(from_token, sender, config.KYBER_ROUTER)
                        if refreshed is not None:
                            self._update_cached_allowance(sender, from_token, refreshed)
                            allowance_human = refreshed / (10 ** decimals_safe)
                            self.console.log(f"[bold green]New Allowance: {allowance_human} {from_token_symbol}[/bold green]")
                else:
                    self.console.log(f"[green]Sufficient allowance exists: {allowance_human} {from_token_symbol}[/green]")
        except Exception as e:
            self.console.log(f"[bold red]Error during allowance check/approval: {e}[/bold red]")
            return

        # 8. Prepare TX params
        tx_params = {
            "sender": sender,
            "recipient": recipient,
            "deadline": int(time.time()) + 1200,
            "slippageTolerance": int(slippage_float * 10000),  # bps
            "chargeFeeBy": "",
            "feeAmount": 0,
            "isInBps": True,
            "feeReceiver": "",
            "sources": "",
            "referral": "",
            "enableGasEstimation": True,
            "permit": permit_data or "",  # include EIP-2612 permit if available
            "ignoreCappedSlippage": False
        }

        # Clean out empty
        tx_params = {k: v for k, v in tx_params.items() if v not in [None, "", []]}

        # 9. Get encoded swap data
        encoded_data = self.get_encoded_swap_data(
            chain=self.chain_config.CHAIN_NAME,
            route_summary=route_summary,
            tx_params=tx_params
        )
            
        
        if not encoded_data:
            self.console.log("[bold red]Failed to get encoded swap data. Aborting swap.[/bold red]")
            return

        # 10. Extract some swap details
        swap_details = encoded_data.get("data", {})
        amount_in = swap_details.get("amountIn")
        amount_out = swap_details.get("amountOut")
        gas = swap_details.get("gas")
        gas_usd = swap_details.get("gasUsd")
        amount_in_usd = swap_details.get("amountInUsd")
        amount_out_usd = swap_details.get("amountOutUsd")

        amount_in_eth = Web3.from_wei(int(amount_in), 'ether') if amount_in else 0
        amount_out_eth = Web3.from_wei(int(amount_out), 'ether') if amount_out else 0

        self.console.log(f"[bold blue]Swap Details:[/bold blue]")
        self.console.log(f"  - Amount In: {amount_in_eth} {from_token_symbol} (${amount_in_usd})")
        self.console.log(f"  - Expected Amount Out: {amount_out_eth} {to_token_symbol} (${amount_out_usd})")
        self.console.log(f"  - Gas: {gas} units (${gas_usd})")

        # 11. Confirm
        if not questionary.confirm("Do you want to proceed with the swap based on the above details?").ask():
            self.console.log("[yellow]Swap cancelled by user[/yellow]")
            return

        # 12. Execute
        self.execute_swap(
            private_key=private_key,
            encoded_data=encoded_data,
            router_address=router_address,
            from_token=from_token,
            amount_in_wei=amount_in_wei,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas  # pass in the from_token address here
        )

    def start_swaps(self):
        """Initiate the swapping process for all loaded private keys."""
        for private_key in self.wallet_private_keys:
            try:
                self.swap_tokens_kyberswap(private_key)
            except Exception as e:
                self.console.log(f"[bold red]Error in swap for wallet: {e}[/bold red]")

    def start_swaps_batch(self):
        """Batch mode: collect setup once and apply to all wallets.

        - Prompts once for tokens, amount method, and slippage.
        - If a wallet needs approval, approve unlimited by default.
        - If a token supports EIP-2612, uses permit per wallet when possible.
        """
        if not self.wallet_private_keys:
            self.console.log("[bold red]No private keys loaded. Exiting.[/bold red]")
            return

        # 1) Select tokens (support manual input like single-threaded flow)
        from_token_choices = list(self.tokens.keys()) + ["[Enter contract address manually]"]
        from_token_full = questionary.select(
            "Select the token you want to swap from:",
            choices=from_token_choices
        ).ask()
        if from_token_full == "[Enter contract address manually]":
            manual_from_token = questionary.text(
                "Enter the contract address of the token you want to swap from:"
            ).ask()
            try:
                manual_from_token = self.w3.to_checksum_address(manual_from_token.strip())
            except Exception:
                self.console.log("[bold red]Invalid contract address entered. Aborting.[/bold red]")
                return
            from_token_full = f"Custom ({manual_from_token})"
            self.tokens[from_token_full] = manual_from_token

        to_token_choices = [symbol for symbol in self.tokens.keys() if symbol != from_token_full] + ["[Enter contract address manually]"]
        to_token_full = questionary.select(
            "Select the token you want to swap to:",
            choices=to_token_choices
        ).ask()
        if to_token_full == "[Enter contract address manually]":
            manual_to_token = questionary.text(
                "Enter the contract address of the token you want to swap to:"
            ).ask()
            try:
                manual_to_token = self.w3.to_checksum_address(manual_to_token.strip())
            except Exception:
                self.console.log("[bold red]Invalid contract address entered. Aborting.[/bold red]")
                return
            to_token_full = f"Custom ({manual_to_token})"
            self.tokens[to_token_full] = manual_to_token

        from_token = self.tokens[from_token_full]
        to_token = self.tokens[to_token_full]
        from_token_symbol = from_token_full.split(' (')[0]
        to_token_symbol = to_token_full.split(' (')[0]

        self._ensure_prefetched_token(from_token)
        self._ensure_prefetched_token(to_token)

        # 2) Check token balance (use first wallet) and log; reuse decimals from here
        try:
            first_addr = Account.from_key(self.wallet_private_keys[0]).address
            balance_raw, human_readable_balance, decimals = self.check_token_balance(from_token, first_addr)
            self.console.log(f"[bold blue]First wallet balance of {from_token_symbol}: {human_readable_balance}[/bold blue]")
            if balance_raw <= 0:
                self.console.log("[yellow]Warning: First wallet has zero balance for input token[/yellow]")
        except Exception as e:
            self.console.log(f"[bold red]Error fetching token balance: {e}[/bold red]")
            return

        # 3) Amount selection (fixed or %)
        amount_choice = questionary.select(
            "Choose amount input method:",
            choices=["Enter fixed amount", "Enter based %"]
        ).ask()

        fixed_amount_in_wei = None
        percentage_float = None
        if amount_choice == "Enter fixed amount":
            amount = questionary.text(
                f"Enter the fixed amount of {from_token_symbol} to swap:"
            ).ask()
            try:
                amount_float = float(amount)
                if amount_float > human_readable_balance:
                    self.console.log(f"[bold red]Error: Insufficient balance[/bold red]")
                    return
                fixed_amount_in_wei = int(amount_float * (10 ** decimals))
            except ValueError:
                self.console.log("[bold red]Invalid amount. Aborting.[/bold red]")
                return
        else:
            percentage = questionary.text(
                f"Enter how much (%) of {from_token_symbol} balance to swap (1-100):"
            ).ask()
            try:
                percentage_float = float(percentage)
                if not 0 < percentage_float <= 100:
                    self.console.log("[bold red]Percentage must be between 1 and 100[/bold red]")
                    return
            except ValueError:
                self.console.log("[bold red]Invalid percentage. Aborting.[/bold red]")
                return

        # 4) Slippage
        slippage_choice = questionary.select(
            "Choose slippage setting:",
            choices=["Default (0.5%)", "Custom"]
        ).ask()
        if slippage_choice == "Custom":
            slippage = questionary.text("Enter slippage tolerance % (e.g., 0.5 for 0.5%):").ask()
            try:
                slippage_float = float(slippage) / 100
            except ValueError:
                self.console.log("[bold red]Invalid slippage value. Using default 0.5%[/bold red]")
                slippage_float = 0.005
        else:
            slippage_float = 0.005

        # Confirm once
        if not questionary.confirm(
            f"Proceed with batch swap from {from_token_symbol} to {to_token_symbol} for {len(self.wallet_private_keys)} wallets?"
        ).ask():
            self.console.log("[yellow]Batch swap cancelled by user[/yellow]")
            return

        # 5) Fetch gas fees once
        max_fee_per_gas, max_priority_fee_per_gas = self.fetch_suggested_fees()
        if not max_fee_per_gas or not max_priority_fee_per_gas:
            self.console.log("[bold red]Could not fetch valid gas fees. Aborting batch.[/bold red]")
            return
        
        # Build a sensible probe amount to fetch router address.
        # Very tiny values (e.g., 1 wei) often cause Kyber to return "route not found".
        # Use at least ~0.001 token (or the whole balance if it's smaller) to probe.
        min_probe_amount_wei = 1
        try:
            # decimals comes from the earlier balance check in this flow
            # Aim for 0.001 token as probe size
            min_probe_amount_wei = max(1, int((10 ** int(decimals)) / 1000))
        except Exception:
            # Fallback in case decimals is unavailable; 1e15 (~0.001 ETH with 18 decimals)
            min_probe_amount_wei = 10 ** 15

        probe_amount_in_wei = fixed_amount_in_wei
        if probe_amount_in_wei is None:  # percentage mode
            computed = int(balance_raw * (percentage_float / 100)) if balance_raw > 0 else 0
            if computed <= 0:
                # If computed is zero, try minimum probe or whatever balance we have
                probe_amount_in_wei = min(balance_raw, min_probe_amount_wei) if balance_raw > 0 else min_probe_amount_wei
            else:
                # Ensure we meet minimal probe threshold but not exceed wallet balance
                probe_amount_in_wei = min(balance_raw, max(computed, min_probe_amount_wei))
        else:
            # Fixed mode: ensure a reasonable minimum for probing, without exceeding balance if we know it
            probe_amount_in_wei = max(probe_amount_in_wei, min_probe_amount_wei)
            if balance_raw > 0:
                probe_amount_in_wei = min(probe_amount_in_wei, balance_raw)

        probe_route = self.get_swap_route(
            chain=self.chain_config.CHAIN_NAME,
            token_in=from_token,
            token_out=to_token,
            amount_in=probe_amount_in_wei
        )
        router_address_probe = None
        if probe_route and probe_route.get("data"):
            router_address_probe = probe_route["data"].get("routerAddress")
        if router_address_probe:
            self.console.log(f"[green]Probe router address: {router_address_probe}[/green]")
        else:
            self.console.log("[yellow]Failed to probe router address. Proceeding with cached data.[/yellow]")

        # 6) Process each wallet
        for private_key in self.wallet_private_keys:
            try:
                account = Account.from_key(private_key)
                sender = account.address
                recipient = account.address

                # Compute amount for this wallet
                if percentage_float is not None:
                    balance_raw = self._get_prefetched_balance(sender, from_token)
                    if balance_raw is None:
                        balance_raw, _, _ = self.check_token_balance(from_token, sender)
                    amount_in_wei = int(balance_raw * (percentage_float / 100))
                    if amount_in_wei <= 0:
                        self.console.log(f"[yellow]{sender}: Skipping, zero amount based on %.")
                        continue
                else:
                    amount_in_wei = fixed_amount_in_wei

                # Route per wallet (amount can differ)
                route = self.get_swap_route(
                    chain=self.chain_config.CHAIN_NAME,
                    token_in=from_token,
                    token_out=to_token,
                    amount_in=amount_in_wei
                )
                if not route:
                    self.console.log(f"[bold red]{sender}: Failed to fetch route. Skipping.[/bold red]")
                    continue
                data = route.get("data")
                if not data:
                    self.console.log("[bold red]No data found in route response. Skipping wallet.[/bold red]")
                    continue
                route_summary = data.get("routeSummary")
                router_address = data.get("routerAddress")

                if not router_address:
                    self.console.log("[bold red]Router address not found. Skipping wallet.[/bold red]")
                    continue
                if not route_summary:
                    self.console.log("[bold red]Incomplete route data received. Skipping wallet.[/bold red]")
                    continue
                
                self.console.log(f"[bold green]KyberSwap Router Address: {router_address}[/bold green]")

                # Allowance (auto-approve unlimited if needed)
                permit_data = None
                if str(from_token).lower() != NATIVE_SENTINEL:
                    allowance = self._get_prefetched_allowance(sender, from_token)
                    if allowance is None:
                        allowance = self.check_allowance(from_token, sender, config.KYBER_ROUTER)
                        if allowance is not None:
                            self._update_cached_allowance(sender, from_token, allowance)
                    if allowance is None:
                        allowance = 0
                        self._update_cached_allowance(sender, from_token, allowance)
                    decimals_safe = decimals or 18
                    allowance_human = allowance / (10 ** decimals_safe)
                    required_allowance_human = amount_in_wei / (10 ** decimals_safe)
                    
                    self.console.log(f"[bold blue]Current Allowance: {allowance_human} {from_token_symbol}[/bold blue]")
                    self.console.log(f"[bold blue]Required Allowance: {required_allowance_human} {from_token_symbol}[/bold blue]")                    
                    if allowance < amount_in_wei:
                        self.console.log(f"[{sender}] [yellow]Insufficient allowance. Approving tokens...[/yellow]")
                        supports_permit = self.check_eip2612_support(from_token, sender)
                        if supports_permit:
                            self.console.log(f"[{sender}] [green]Token supports EIP-2612. Using permit for approval.[/green]")
                            deadline = int(time.time()) + 1200
                            permit_data = self.get_permit_data(
                                token_address=from_token,
                                owner=sender,
                                spender=config.KYBER_ROUTER,
                                value=amount_in_wei,
                                deadline=deadline,
                                private_key=private_key
                            )
                            if permit_data:
                                self.console.log(f"[bold green]{sender}:Permit data generated successfully.[/bold green]")
                                self._update_cached_allowance(sender, from_token, amount_in_wei)
                            else:
                                self.console.log(f"[bold red]{sender}: Failed to generate permit data. Skipping swap.[/bold red]")
                                continue

                        else:
                            self.send_approval_transaction(
                                private_key=private_key,
                                token_address=from_token,
                                spender=config.KYBER_ROUTER,
                                amount=amount_in_wei,
                                max_fee_per_gas=max_fee_per_gas,
                                max_priority_fee_per_gas=max_priority_fee_per_gas,
                                approval_mode='unlimited'
                            )
                        self._update_cached_allowance(sender, from_token, 2 ** 256 - 1)
                    else:
                        self.console.log(f"[green]Sufficient allowance exists: {allowance_human} {from_token_symbol}[/green]")
                else:
                    self.console.log("[green]Native token - no allowance needed[/green]")
            except Exception as e:
                self.console.log(f"[bold red]Error during allowance check/approval: {e}[/bold red]")
                continue

                # Build TX params and execute
            tx_params = {
                "sender": sender,
                "recipient": recipient,
                "deadline": int(time.time()) + 1200,
                "slippageTolerance": int(slippage_float * 10000), #bps
                "chargeFeeBy": "",
                "feeAmount": 0,
                "isInBps": True,
                "feeReceiver": "",
                "sources": "",
                "referral": "",
                "enableGasEstimation": True,
                "permit": permit_data or route_summary.get('permit', ""),
                "ignoreCappedSlippage": False
            }
            #clean out empty
            tx_params = {k: v for k, v in tx_params.items() if v not in [None, "", []]}

            try:
                encoded_data = self.get_encoded_swap_data(
                    chain=self.chain_config.CHAIN_NAME,
                    route_summary=route_summary,
                    tx_params=tx_params
                )
                if not encoded_data:
                    self.console.log(f"[bold red]{sender}: Failed to get encoded swap data. Skipping.[/bold red]")
                    continue

            # 10. Extract some swap details
                swap_details = encoded_data.get("data", {})
                amount_in = swap_details.get("amountIn")
                amount_out = swap_details.get("amountOut")
                gas = swap_details.get("gas")
                gas_usd = swap_details.get("gasUsd")
                amount_in_usd = swap_details.get("amountInUsd")
                amount_out_usd = swap_details.get("amountOutUsd")

                amount_in_eth = Web3.from_wei(int(amount_in), 'ether') if amount_in else 0
                amount_out_eth = Web3.from_wei(int(amount_out), 'ether') if amount_out else 0

                self.console.log(f"[bold blue]Swap Details:[/bold blue]")
                self.console.log(f"  - Amount In: {amount_in_eth} {from_token_symbol} (${amount_in_usd})")
                self.console.log(f"  - Expected Amount Out: {amount_out_eth} {to_token_symbol} (${amount_out_usd})")
                self.console.log(f"  - Gas: (${gas_usd})")

                self.execute_swap(
                    private_key=private_key,
                    encoded_data=encoded_data,
                    router_address=router_address,
                    from_token=from_token,
                    amount_in_wei=amount_in_wei,
                    max_fee_per_gas=max_fee_per_gas,
                    max_priority_fee_per_gas=max_priority_fee_per_gas  # pass in the from
                    )
            except Exception as e:
                self.console.log(f"[bold red]{sender}: Error during batch processing: {e}[/bold red]")
                continue

    def run(self):
        """Run the SwapManager."""
        # Let user pick how to load private keys
        self.select_wallet_input_method()

        if not self.wallet_private_keys:
            self.console.log("[bold red]No private keys loaded. Exiting.[/bold red]")
            sys.exit(1)

        self.select_token_input_method()

        if not self.tokens:
            self.console.log("[bold red]No tokens configured. Exiting.[/bold red]")
            sys.exit(1)

        self.prefetch_wallet_token_data(include_allowance=True, spender=config.KYBER_ROUTER, include_ens=True)
        self._log_loaded_wallets()
        self._log_loaded_tokens()

        # Ask swap mode
        mode = questionary.select(
            "Choose swap mode:",
            choices=[
                "Batch swap (one setup for all wallets)",
                "Single-threaded swap (configure each wallet)"
            ]
        ).ask()

        if mode.startswith("Batch swap"):
            self.start_swaps_batch()
        else:
            self.start_swaps()


def main():
    """
    Main entry point. Prompt the user for which chain to use, then run the SwapManager with that chain config.
    """
    chain_choices = ["POLYGON", "OP", "Base", "ARB", "Linea", "ETHER"]
    chain_selection = questionary.select("Select chain:", choices=chain_choices).ask()

    # Dynamically pick the chain config
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
        # fallback
        chain_config = config.POLYGON

    # Initialize and run
    swap_manager = SwapManager(chain_config=chain_config)
    swap_manager.run()


if __name__ == "__main__":
    main()

