import os, sys, json, time, logging, platform
from typing import List, Dict, Tuple, Optional, Set

import requests
import questionary
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn

from web3 import Web3
from web3.exceptions import TimeExhausted
from eth_account import Account

from customtkinter import CTk, CTkTextbox, CTkButton, CTkLabel, CTkFrame

from utils.helper import Web3Helper, FileHelper, NATIVE_SENTINEL

import config  # your config.py

console = Console()



GAS_ERROR_HINTS = (
    "base fee", "underpriced", "fee cap", "max fee", "intrinsic gas too low",
    "replacement transaction underpriced", "max priority", "fee too low", "EIP-1559"
)

class BatchTransferManager:
    def __init__(self, chain_config):
        self.console = console
        self.chain_config = chain_config

        # --- paths / chain config
        self.wallet_file = chain_config.WALLET_FILE
        self.receiver_file = chain_config.RECEIVERS_FILE
        self.contracts_file = chain_config.CONTRACTS_FILE
        self.native_correct_contract = chain_config.NATIVE_TOKEN  # the correct contract from config
        self.chain_id = int(chain_config.CHAIN_ID)
        self.chain_name = chain_config.CHAIN_NAME
        self.infura_gas_api = chain_config.INFURA_GAS_API_URL

        # --- logging
        logging.basicConfig(level=logging.INFO, handlers=[RichHandler(console=self.console)])
        self.logger = logging.getLogger(__name__)

        # --- helper-backed web3 wiring
        self.web3h = Web3Helper(chain_config, console=self.console)
        self.provider = self.web3h.provider
        self.w3 = self.web3h.w3
        self.multicall = self.web3h.multicall
        self.erc20_abi = self.web3h.erc20_abi

        self.is_linux = platform.system().lower() == "linux"

        # --- in-memory
        self.wallet_private_keys: List[str] = []
        # sender_addresses: addresses derived from loaded private keys (senders only)
        self.sender_addresses: List[str] = []
        # wallet_addresses: union of senders and receivers (for prefetch/ENS convenience)
        self.wallet_addresses: List[str] = []
        self.receivers_raw: List[str] = []
        self.receiver_addresses: List[str] = []
        self.receiver_ens_map: Dict[str, str] = {}
        self.receiver_unresolved_ens: List[str] = []
        self.tokens: Dict[str, str] = {}
        self.token_addresses: List[str] = []
        self.token_modes: Dict[str, str] = {}

        # Prefetch caches (filled via Web3Helper.multicall_fetch)
        self.prefetched_balances: Dict[Tuple[str, str], Optional[int]] = {}
        self.prefetched_decimals: Dict[str, int] = {}
        self.prefetched_names: Dict[str, str] = {}
        self.prefetched_symbols: Dict[str, str] = {}
        self.prefetched_ens_reverse: Dict[str, str] = {}
        self.prefetched_ens_forward: Dict[str, str] = {}
        self.prefetch_ready: bool = False

        # --- files
        for path_item, kind in ((self.wallet_file, 'wallets'), (self.receiver_file, 'receivers'), (self.contracts_file, 'contracts')):
            try:
                FileHelper.ensure_placeholder(path_item, kind)
            except Exception as e:
                self.console.log(f"[yellow]Could not ensure placeholder {kind} file {path_item}: {e}[/yellow]")

    # ---- PK loaders (like Kyber) ----

    def load_private_keys_from_file(self):
        keys, addrs = self.web3h.load_privatekeys_file(self.wallet_file)
        if not keys:
            raise RuntimeError("No valid private keys loaded from file.")
        self.wallet_private_keys = keys
        self.sender_addresses = list(addrs)
        self.wallet_addresses = list(addrs)

    def load_private_keys_from_cli(self):
        keys, addrs = self.web3h.load_privatekeys_cli()
        if not keys:
            raise RuntimeError("No private keys entered.")
        self.wallet_private_keys = keys
        self.sender_addresses = list(addrs)
        self.wallet_addresses = list(addrs)

    def load_private_keys_from_gui(self):
        keys, addrs = self.web3h.load_privatekeys_gui()
        if not keys:
            raise RuntimeError("No private keys entered.")
        self.wallet_private_keys = keys
        self.sender_addresses = list(addrs)
        self.wallet_addresses = list(addrs)

    def select_private_key_input_method(self):
        if self.is_linux:
            choice = questionary.select(
                "Choose private key input method:",
                choices=["Default Path (File)", "Manual Input (CLI)"]
            ).ask()
            loader = self.load_private_keys_from_file if choice == "Default Path (File)" else self.load_private_keys_from_cli
        else:
            choice = questionary.select(
                "Choose private key input method:",
                choices=["Default Path (File)", "Manual Input (GUI)"]
            ).ask()
            loader = self.load_private_keys_from_file if choice == "Default Path (File)" else self.load_private_keys_from_gui

        loader()
        if not self.wallet_private_keys:
            raise RuntimeError("No private keys loaded.")
        self.console.log(f"[green]Loaded {len(self.sender_addresses)} wallet(s).")

    def select_receiver_input_method(self):
        if self.is_linux:
            choice = questionary.select("Choose receiver input method:", choices=["Default Path (File)", "Manual Input (CLI)"]).ask()
            if choice == "Default Path (File)":
                addrs, ens_names = self.web3h.load_wallets_file(self.receiver_file)
            else:
                addrs, ens_names = self.web3h.load_wallets_cli()
        else:
            choice = questionary.select("Choose receiver wallet address:", choices=["Default Path (File)", "Manual Input (GUI)"]).ask()
            if choice == "Default Path (File)":
                addrs, ens_names = self.web3h.load_wallets_file(self.receiver_file)
            else:
                addrs, ens_names = self.web3h.load_wallets_gui()

        self.receivers_raw = list(addrs or [])
        if ens_names:
            self.receivers_raw.extend([n for n in ens_names if n])

        final_receivers: List[str] = []
        seen: Set[str] = set()
        self.receiver_ens_map = {}
        self.receiver_unresolved_ens = []

        def _push(address: Optional[str], label: Optional[str] = None) -> None:
            if not address:
                return
            checksum = self._coerce_address_key(address)
            if not checksum:
                return
            key = checksum.lower()
            if key in seen:
                if label and checksum not in self.receiver_ens_map:
                    self.receiver_ens_map[checksum] = label
                return
            seen.add(key)
            final_receivers.append(checksum)
            if label:
                self.receiver_ens_map[checksum] = label

        for addr in addrs or []:
            _push(addr)

        resolved_from_ens: Dict[str, Optional[str]] = {}
        if ens_names:
            try:
                resolved_from_ens = self.web3h.batch_ens_forward(ens_names, use_multicall=True) or {}
            except Exception as exc:
                self.console.log(f"[yellow]ENS forward resolution failed: {exc}[/yellow]")
                resolved_from_ens = {}

        for name in ens_names or []:
            resolved_addr = resolved_from_ens.get(name)
            if resolved_addr:
                _push(resolved_addr, label=name)
            else:
                self.receiver_unresolved_ens.append(name)

        if self.receiver_unresolved_ens:
            unresolved_joined = ', '.join(self.receiver_unresolved_ens)
            self.console.log(f"[yellow]Unresolved ENS receivers: {unresolved_joined}[/yellow]")

        if not final_receivers:
            raise RuntimeError("No receiver addresses resolved.")

        self.receiver_addresses = final_receivers

        # Keep wallet_addresses as the union of sender + receiver for prefetch/ENS use
        merged: List[str] = []
        seen: Set[str] = set()
        for lst in (self.sender_addresses, self.receiver_addresses):
            for a in lst:
                try:
                    cs = self.w3.to_checksum_address(a)
                except Exception:
                    cs = a
                k = (cs.lower() if isinstance(cs, str) else str(cs))
                if k in seen:
                    continue
                seen.add(k)
                merged.append(cs)
        self.wallet_addresses = merged

    # ---- tokens input
    def select_token_input_method(self):
        if self.is_linux:
            choice = questionary.select(
                "Choose token contract input method:",
                choices=["Default Path (File)", "Manual Input (CLI)"]
            ).ask()
            tokens_raw = (
                self.web3h.load_tokens_file(self.contracts_file)
                if choice == "Default Path (File)"
                else self.web3h.load_tokens_cli()
            )
        else:
            choice = questionary.select(
                "Choose token contract input method:",
                choices=["Default Path (File)", "Manual Input (GUI)"]
            ).ask()
            tokens_raw = (
                self.web3h.load_tokens_file(self.contracts_file)
                if choice == "Default Path (File)"
                else self.web3h.load_tokens_gui()
            )

        if not tokens_raw:
            raise RuntimeError("No token addresses provided.")

        self.token_addresses = []
        self.token_modes = {}
        for addr in tokens_raw:
            actual, mode = self._normalize_token_choice(addr)
            normalized = self._coerce_address_key(actual)
            self.token_addresses.append(normalized)
            self.token_modes[normalized] = mode
            if actual != normalized:
                self.token_modes[actual] = mode

        return self.token_addresses

    def _normalize_token_choice(self, token: str) -> Tuple[str, str]:
        token_str = (token or "").strip()
        if not token_str:
            raise RuntimeError("Empty token entry provided.")
        token_lower = token_str.lower()
        chain_lower = (self.chain_name or "").strip().lower()

        if token_lower == NATIVE_SENTINEL.lower():
            if chain_lower == "polygon":
                target = self.native_correct_contract
                mode = "erc20"  # polygon native exposed via ERC20 wrapper
            else:
                target = token_str
                mode = "true-native"
        else:
            target = token_str
            mode = "erc20"

        try:
            checksum = self.w3.to_checksum_address(target)
        except Exception:
            checksum = target
        return checksum, mode

    def _coerce_address_key(self, value) -> str:
        candidate = value
        if isinstance(candidate, (list, tuple)):
            for item in candidate:
                if isinstance(item, str) and item:
                    candidate = item
                    break
            else:
                candidate = candidate[0] if candidate else ""
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate:
                try:
                    return self.w3.to_checksum_address(candidate)
                except Exception:
                    return candidate
            return ""
        return str(candidate)


    def _fetch_token_decimals(self, token_address: str) -> int:
        try:
            contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(token_address),
                abi=self.erc20_abi,
            )
            return int(contract.functions.decimals().call())
        except Exception:
            return 18

    def prefetch_wallet_token_metadata(self) -> None:
        # Require senders and tokens to prefetch; wallet_addresses may include receivers as well
        if not self.sender_addresses or not self.token_addresses:
            self.prefetch_ready = False
            return

        try:
            result = self.web3h.multicall_fetch(
                wallets=self.wallet_addresses,
                tokens=self.token_addresses,
                spender=None,
                # Do not pass helper.ens_names here to avoid polluting sender wallets
                # with ENS-forward-resolved receiver entries
                ens_names=None,
                use_multicall=True,
                want_balance=True,
                want_decimals=True,
                want_allowance=False,
                want_ens=True,
                with_name=True,
                with_symbol=True,
            )
        except Exception as exc:
            self.console.log(f"[yellow]Multicall prefetch failed: {exc}[/yellow]")
            self.prefetch_ready = False
            return

        wallets_resolved = result.get("wallets_resolved", []) or []
        if wallets_resolved:
            merged_wallets: List[str] = []
            seen: Set[str] = set()

            def _merge_wallet(addr: Optional[str]) -> None:
                if not addr:
                    return
                try:
                    checksum = self.w3.to_checksum_address(addr)
                except Exception:
                    checksum = addr
                key = checksum.lower() if isinstance(checksum, str) else str(checksum)
                if key in seen:
                    return
                seen.add(key)
                merged_wallets.append(checksum)

            for addr in self.wallet_addresses:
                _merge_wallet(addr)
            for addr in wallets_resolved:
                _merge_wallet(addr)
            for wallet_addr, _ in (result.get("balances", {}) or {}).keys():
                _merge_wallet(wallet_addr)

            self.wallet_addresses = merged_wallets
            self.web3h.wallet_addresses = list(merged_wallets)

        balances_raw = result.get("balances", {}) or {}
        decimals_raw = result.get("decimals", {}) or {}
        names_raw = result.get("names", {}) or {}
        symbols_raw = result.get("symbols", {}) or {}
        ens_rev_raw = result.get("ens_reverse", {}) or {}
        ens_fwd_raw = result.get("ens_forward", {}) or {}

        self.prefetched_balances = {}
        for (wallet, token), value in balances_raw.items():
            w = self._coerce_address_key(wallet)
            t = self._coerce_address_key(token)
            self.prefetched_balances[(w, t)] = value

        self.prefetched_decimals = {}
        for token, value in decimals_raw.items():
            t = self._coerce_address_key(token)
            try:
                self.prefetched_decimals[t] = int(value)
            except Exception:
                pass

        self.prefetched_names = {}
        for token, value in names_raw.items():
            t = self._coerce_address_key(token)
            if value:
                self.prefetched_names[t] = value

        self.prefetched_symbols = {}
        for token, value in symbols_raw.items():
            t = self._coerce_address_key(token)
            if value:
                self.prefetched_symbols[t] = value

        self.prefetched_ens_reverse = {}
        for wallet, name in ens_rev_raw.items():
            w = self._coerce_address_key(wallet)
            if name:
                self.prefetched_ens_reverse[w] = name

        for addr, name in self.receiver_ens_map.items():
            key = self._coerce_address_key(addr)
            if key and name and key not in self.prefetched_ens_reverse:
                self.prefetched_ens_reverse[key] = name

        enriched_forward = dict(ens_fwd_raw)
        for addr, name in self.receiver_ens_map.items():
            if not name:
                continue
            if name not in enriched_forward:
                enriched_forward[name] = self._coerce_address_key(addr)
        self.prefetched_ens_forward = enriched_forward
        self.web3h.ens_names = list(enriched_forward.keys())
        self.prefetch_ready = True

        self._build_token_selection_map()

    def _build_token_selection_map(self) -> None:
        self.tokens = {}
        used_labels = set()
        for token in self.token_addresses:
            checksum = self._coerce_address_key(token)

            symbol = self.prefetched_symbols.get(checksum) or ""
            # Use only the symbol in selection labels; omit the long name like (Ether)
            name = self.prefetched_names.get(checksum) or ""
            label_base = (symbol or name).strip()
            if label_base:
                base = f"{label_base} ({checksum})"
            else:
                base = checksum

            label = base
            suffix = 2
            while label in used_labels:
                label = f"{base} [{suffix}]"
                suffix += 1
            used_labels.add(label)
            self.tokens[label] = checksum

    # ---------------- ENS reverse
    def reverse_ens(self, address: str) -> Optional[str]:
        if not address:
            return None
        try:
            result = self.web3h.batch_ens_reverse([address], use_multicall=True) or {}
        except Exception as exc:
            self.console.log(f"[yellow]ENS reverse lookup failed: {exc}[/yellow]")
            return None
        try:
            checksum = self.w3.to_checksum_address(address)
        except Exception:
            checksum = self._coerce_address_key(address)
        if not checksum:
            checksum = str(address)
        return result.get(checksum) or result.get(checksum.lower()) or result.get(str(address))

    # ---------------- Gas + receipts
    def fetch_suggested_fees(self, tier: str) -> Tuple[Optional[int], Optional[int]]:
        try:
            r = requests.get(self.infura_gas_api)
            r.raise_for_status()
            data = r.json()
            max_fee = Web3.to_wei(float(data[tier]["suggestedMaxFeePerGas"]), "gwei")
            max_prio = Web3.to_wei(float(data[tier]["suggestedMaxPriorityFeePerGas"]), "gwei")
            return max_fee, max_prio
        except Exception as e:
            self.console.log(f"[red]Gas API error: {e}[/red]")
            return None, None

    def wait_receipt_slow(self, tx_hash, timeout=300, start_delay=2, max_delay=8):
        delay = start_delay
        start = time.time()
        while True:
            try:
                return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=delay, poll_latency=delay)
            except TimeExhausted:
                if time.time() - start > timeout:
                    raise
                delay = min(max_delay, delay * 1.5)

    # ------------- Multicall: one call for decimals + all balances

    # ------------- ERC-20 / native sends (native sentinel -> use contract from config)
    def send_erc20(self, private_key: str, token_address: str, to: str, amount_wei: int, max_fee: int, max_prio: int) -> str:
        acct = Account.from_key(private_key)
        # self.erc20_abi is already a loaded JSON object
        erc20 = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.erc20_abi)
        tx = erc20.functions.transfer(self.w3.to_checksum_address(to), int(amount_wei)).build_transaction({
            "chainId": self.chain_id,
            "from": acct.address,
            "nonce": self.w3.eth.get_transaction_count(acct.address),
            "type": 2,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_prio
        })
        try:
            tx["gas"] = self.w3.eth.estimate_gas(tx)
        except Exception:
            tx["gas"] = 100000
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        return self.w3.eth.send_raw_transaction(signed.rawTransaction).hex()

    # ------------- Main flow
    def run(self):
        # Keys input (helper-backed)
        self.select_private_key_input_method()
        self.select_receiver_input_method()
        self.select_token_input_method()

        if not self.wallet_private_keys:
            self.console.log("[bold red]No private keys loaded. Exiting.[/bold red]")
            return
        if not self.receiver_addresses:
            self.console.log("[bold red]No receiver addresses loaded. Exiting.[/bold red]")
            return
        if not self.token_addresses:
            self.console.log("[bold red]No token addresses provided. Exiting.[/bold red]")
            return

        self.console.rule("[bold cyan]Prefetching wallet/token data[/bold cyan]")
        self.prefetch_wallet_token_metadata()
        if not self.tokens:
            # fallback labels if multicall failed
            fallback = {}
            used = set()
            for token in self.token_addresses:
                checksum = self._coerce_address_key(token)
                label = checksum
                suffix = 2
                while label in used:
                    label = f"{checksum} [{suffix}]"
                    suffix += 1
                used.add(label)
                fallback[label] = checksum
            self.tokens = fallback

        self.console.rule("[bold cyan]Wallets loaded[/bold cyan]")
        for idx, wallet in enumerate(self.sender_addresses, 1):
            wallet_cs = self._coerce_address_key(wallet)
            ens = self.prefetched_ens_reverse.get(wallet_cs)
            if ens:
                self.console.log(f"{idx:>2}. {wallet_cs} ({ens})")
            else:
                self.console.log(f"{idx:>2}. {wallet_cs}")

        self.console.rule("[bold cyan]Receivers loaded[/bold cyan]")
        for idx, rcv in enumerate(self.receiver_addresses, 1):
            rcv_cs = self._coerce_address_key(rcv)
            ens = self.prefetched_ens_reverse.get(rcv_cs) or self.receiver_ens_map.get(rcv_cs)
            if not ens and rcv_cs in self.receiver_ens_map:
                ens = self.receiver_ens_map.get(rcv_cs)
            if ens:
                self.console.log(f"{idx:>2}. {rcv_cs} (ENS: {ens})")
            else:
                self.console.log(f"{idx:>2}. {rcv_cs}")

        self.console.rule("[bold cyan]Tokens loaded[/bold cyan]")
        seen_tokens = set()
        for idx, token in enumerate(self.token_addresses, 1):
            token_cs = self._coerce_address_key(token)
            if token_cs in seen_tokens:
                continue
            seen_tokens.add(token_cs)
            symbol = self.prefetched_symbols.get(token_cs, "")
            name = self.prefetched_names.get(token_cs, "")
            decimals_cache = self.prefetched_decimals.get(token_cs)
            pieces = [p for p in (symbol, name) if p]
            descriptor = " / ".join(dict.fromkeys(pieces)) if pieces else ""
            extra = f" | decimals: {decimals_cache}" if decimals_cache is not None else ""
            if descriptor:
                self.console.log(f"{idx:>2}. {token_cs} ({descriptor}){extra}")
            else:
                self.console.log(f"{idx:>2}. {token_cs}{extra}")

        token_label = questionary.select(
            "Select token to transfer:",
            choices=list(self.tokens.keys())
        ).ask()
        token_sel_raw = self.tokens[token_label]
        token_sel = self._coerce_address_key(token_sel_raw)
        native_mode = self.token_modes.get(token_sel, self.token_modes.get(token_sel_raw, "erc20"))

        if native_mode == "true-native":
            decimals = 18
        else:
            decimals = self.prefetched_decimals.get(token_sel)
            if decimals is None:
                decimals = self._fetch_token_decimals(token_sel)

        token_key = self._coerce_address_key(token_sel)
        balances: Dict[str, Optional[int]] = {}
        for wallet in self.sender_addresses:
            wallet_key = self._coerce_address_key(wallet)
            bal = self.prefetched_balances.get((wallet_key, token_key))
            if bal is None:
                if native_mode == "true-native":
                    try:
                        bal = self.w3.eth.get_balance(wallet_key)
                    except Exception:
                        bal = None
                else:
                    try:
                        contract = self.w3.eth.contract(address=token_key, abi=self.erc20_abi)
                        bal = contract.functions.balanceOf(wallet_key).call()
                    except Exception:
                        bal = None
            balances[wallet_key] = bal

        self.console.rule("[bold]Sender Balances[/bold]")
        for wallet in self.sender_addresses:
            wallet_key = self._coerce_address_key(wallet)
            bal = balances.get(wallet_key)
            ens = self.prefetched_ens_reverse.get(wallet_key)
            label = f"{ens} -> {wallet_key}" if ens else wallet_key
            if bal is None:
                bal_hr = "N/A"
            else:
                try:
                    bal_hr = bal / (10 ** decimals)
                except Exception:
                    bal_hr = "N/A"
            self.console.print(f"[blue]{label}[/blue]: {bal_hr}")

        # Mode
        mode = questionary.select("Choose transfer mode:", choices=["Same number (1-to-1)", "Multi-number (multi-to-many)"]).ask()

        plan: List[Tuple[str, str, int]] = []  # (sender, receiver, amount_wei)

        # ----- Same number (1-to-1) -----
        if mode.startswith("Same number"):
            if len(self.sender_addresses) != len(self.receiver_addresses):
                self.console.log("[bold red]Sender and receiver counts must match for 1-to-1 mode.[/bold red]")
                return
            amt_mode = questionary.select("Amount input method:", choices=["Enter fixed amount", "Enter based %"]).ask()
            if amt_mode.startswith("Enter fixed"):
                amt = questionary.text("Enter fixed amount for EACH pair (token units):").ask()
                try:
                    amt_wei = int(float(amt) * (10 ** decimals))
                except Exception:
                    self.console.log("[red]Invalid amount[/red]"); return
                for s, r in zip(self.sender_addresses, self.receiver_addresses):
                    plan.append((s, r, amt_wei))
            else:
                pct = questionary.text("Enter percentage of EACH sender's balance to send (1-100):").ask()
                try:
                    pct_val = float(pct)
                    if not 0 < pct_val <= 100:
                        raise ValueError
                except Exception:
                    self.console.log("[red]Invalid percentage[/red]"); return
                for s, r in zip(self.sender_addresses, self.receiver_addresses):
                    sender_key = self._coerce_address_key(s)
                    bal = balances.get(sender_key, 0) or 0
                    amt_wei = int(bal * (pct_val / 100.0))
                    if amt_wei <= 0:
                        self.console.log(f"[yellow]{s}: zero amount; skipping[/yellow]")
                        continue
                    plan.append((s, r, amt_wei))

        # ----- Multi-number (multi-to-many) -----
        else:
            sub = questionary.select(
                "Multi-number options:",
                choices=[
                    "Same amount to every receiver",
                    "Calculate distribution (sum sender amounts ÷ receiver count)"
                ]
            ).ask()

            if sub.startswith("Same amount"):
                amt_mode = questionary.select("Amount source:", choices=["Enter fixed amount", "Enter based % of each sender's balance"]).ask()

                if amt_mode.startswith("Enter fixed"):
                    per_recv_units = questionary.text("Enter amount PER RECEIVER (token units):").ask()
                    try:
                        per_recv = int(float(per_recv_units) * (10 ** decimals))
                    except Exception:
                        self.console.log("[red]Invalid amount[/red]"); return

                    # sequentially allocate from senders to fund each receiver equally
                    targets = {r: per_recv for r in self.receiver_addresses}
                    sender_remaining = {s: balances.get(self._coerce_address_key(s), 0) or 0 for s in self.sender_addresses}
                    for r in self.receiver_addresses:
                        remaining = targets[r]
                        for s in self.sender_addresses:
                            if remaining == 0:
                                break
                            avail = sender_remaining[s]
                            if avail <= 0:
                                continue
                            send_amt = min(avail, remaining)
                            if send_amt > 0:
                                plan.append((s, r, send_amt))
                                sender_remaining[s] -= send_amt
                                remaining -= send_amt
                        if remaining > 0:
                            self.console.log("[red]Insufficient total balance to fund all receivers equally.[/red]"); return

                else:
                    # % of EACH sender's balance, aggregated then spread equally
                    pct = questionary.text("Enter % of EACH sender's balance to aggregate (1-100):").ask()
                    try:
                        pct_val = float(pct)
                        if not 0 < pct_val <= 100:
                            raise ValueError
                    except Exception:
                        self.console.log("[red]Invalid percentage[/red]"); return
                    per_sender_amt = {s: int((balances.get(self._coerce_address_key(s), 0) or 0) * (pct_val / 100.0)) for s in self.sender_addresses}
                    total_sum = sum(per_sender_amt.values())
                    if total_sum <= 0:
                        self.console.log("[red]Total sum is zero[/red]"); return
                    per_receiver = total_sum // len(self.receiver_addresses)
                    if per_receiver == 0:
                        self.console.log("[red]Per-receiver share is zero; increase %[/red]"); return
                    sender_remaining = per_sender_amt.copy()
                    for r in self.receiver_addresses:
                        remaining = per_receiver
                        for s in self.sender_addresses:
                            if remaining == 0:
                                break
                            avail = sender_remaining[s]
                            if avail <= 0:
                                continue
                            send_amt = min(avail, remaining)
                            if send_amt > 0:
                                plan.append((s, r, send_amt))
                                sender_remaining[s] -= send_amt
                                remaining -= send_amt
                    leftover = sum(sender_remaining.values())
                    if leftover > 0:
                        self.console.log("[yellow]Note: remainder not distributed due to integer division.[/yellow]")

            else:
                # Calculate distribution: a single amount per sender (applied to ALL), or % of each sender balance (same %)
                in_mode = questionary.select(
                    "Sender amounts input:",
                    choices=["Enter fixed amount per sender (one value)", "Enter based % of each sender's balance (single %)"]
                ).ask()
                if in_mode.startswith("Enter fixed"):
                    unit = questionary.text("Enter fixed AMOUNT per sender (token units):").ask()
                    try:
                        per_sender_amt = int(float(unit) * (10 ** decimals))
                    except Exception:
                        self.console.log("[red]Invalid amount[/red]"); return
                    total_sum = per_sender_amt * len(self.sender_addresses)
                else:
                    pct = questionary.text("Enter single % of EACH sender's balance (1-100):").ask()
                    try:
                        pct_val = float(pct)
                        if not 0 < pct_val <= 100:
                            raise ValueError
                    except Exception:
                        self.console.log("[red]Invalid percentage[/red]"); return
                    per_sender_map = {s: int((balances.get(self._coerce_address_key(s), 0) or 0) * (pct_val / 100.0)) for s in self.sender_addresses}
                    total_sum = sum(per_sender_map.values())

                if total_sum <= 0:
                    self.console.log("[red]Total sum must be > 0[/red]"); return
                per_receiver = total_sum // len(self.receiver_addresses)
                if per_receiver == 0:
                    self.console.log("[red]Per-receiver share is zero; increase amount/%[/red]"); return

                # Build from available balances
                if in_mode.startswith("Enter fixed"):
                    sender_remaining = {s: per_sender_amt for s in self.sender_addresses}
                else:
                    sender_remaining = per_sender_map

                for r in self.receiver_addresses:
                    remaining = per_receiver
                    for s in self.sender_addresses:
                        if remaining == 0:
                            break
                        avail = sender_remaining[s]
                        if avail <= 0:
                            continue
                        send_amt = min(avail, remaining)
                        if send_amt > 0:
                            plan.append((s, r, send_amt))
                            sender_remaining[s] -= send_amt
                            remaining -= send_amt
                leftover = sum(sender_remaining.values())
                if leftover > 0:
                    self.console.log("[yellow]Note: remainder not distributed due to integer division.[/yellow]")

        # ---- Confirmation preview ----
        self.console.rule("[bold]Transfer Plan Preview[/bold]")
        total_tx = len(plan)
        total_amount = sum(a for _, _, a in plan)
        self.console.print(f"[bold]Token Contract:[/bold] {token_sel}")
        self.console.print(f"[bold]Decimals:[/bold] {decimals}")
        self.console.print(f"[bold]Transfers:[/bold] {total_tx} txs")
        self.console.print(f"[bold]Total Amount:[/bold] {total_amount / (10**decimals)}")
        for i, (s, r, a) in enumerate(plan[:10], 1):
            try:
                s_cs = self.w3.to_checksum_address(s)
            except Exception:
                s_cs = s
            try:
                r_cs = self.w3.to_checksum_address(r)
            except Exception:
                r_cs = r
            se = self.prefetched_ens_reverse.get(s_cs) or self.reverse_ens(s)
            re = self.prefetched_ens_reverse.get(r_cs) or self.reverse_ens(r)
            sender_label = f"{se} -> {s}" if se else s
            receiver_label = f"{re} -> {r}" if re else r
            pretty_amount = a / (10 ** decimals)
            self.console.print(f"{i:>3}. {sender_label} -> {receiver_label} | {pretty_amount}")

        if total_tx > 10:
            self.console.print(f"... and {total_tx-10} more")
        if not questionary.confirm("Proceed with these transfers?").ask():
            self.console.log("[yellow]Cancelled by user[/yellow]")
            return

        # ---- Gas setup per your rules ----
        chosen_tier = questionary.select("Select gas tier to use:", choices=["low", "medium", "high"], default="medium").ask()
        use_same_gas = questionary.confirm("Use the SAME fetched gas for all transactions?", default=True).ask()
        fixed_fees = None
        if use_same_gas:
            mf, mp = self.fetch_suggested_fees(chosen_tier)
            if not mf:
                self.console.log("[red]Failed to get gas fees[/red]"); return
            fixed_fees = (mf, mp)

        # ---- Execute with live progress ----
        success = 0
        progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            "•",
            TimeElapsedColumn(),
            "•",
            TimeRemainingColumn(),
            console=self.console,
        )
        with progress:
            task = progress.add_task("[cyan]Transferring...", total=total_tx)
            for (sender, receiver, amt_wei) in plan:
                try:
                    pk = self.wallet_private_keys[self.sender_addresses.index(sender)]
                    if fixed_fees:
                        max_fee, max_prio = fixed_fees
                    else:
                        max_fee, max_prio = self.fetch_suggested_fees(chosen_tier)
                        if not max_fee:
                            raise RuntimeError("Failed to get gas fees")

                    txh = self.send_erc20(pk, token_sel, receiver, amt_wei, max_fee, max_prio)
                    self.console.log(f"[green]Sent tx: {txh}[/green]")
                    rcpt = self.wait_receipt_slow(txh)
                    if rcpt and rcpt.get("status", 0) == 1:
                        success += 1
                    else:
                        self.console.log(f"[red]Tx failed in block {rcpt.get('blockNumber')}[/red]")

                except Exception as e:
                    msg = (str(e) or "").lower()
                    self.console.log(f"[red]Error sending tx ({sender} → {receiver}): {e}[/red]")
                    # Gas-related immediate retry with fresh 'medium' fees + fresh estimate
                    if any(h in msg for h in GAS_ERROR_HINTS):
                        self.console.log("[yellow]Gas-related error → retrying with fresh 'medium' fees[/yellow]")
                        try:
                            max_fee, max_prio = self.fetch_suggested_fees("medium")
                            if not max_fee:
                                raise RuntimeError("Gas API failed on retry")
                            txh = self.send_erc20(pk, token_sel, receiver, amt_wei, max_fee, max_prio)
                            self.console.log(f"[green]Retry tx: {txh}[/green]")
                            rcpt = self.wait_receipt_slow(txh)
                            if rcpt and rcpt.get("status", 0) == 1:
                                success += 1
                            else:
                                self.console.log(f"[red]Retry failed in block {rcpt.get('blockNumber')}[/red]")
                        except Exception as e2:
                            self.console.log(f"[red]Retry error: {e2}[/red]")
                    # else non-gas error → continue
                finally:
                    progress.advance(task, 1)

        self.console.rule("[bold]Done[/bold]")
        self.console.print(f"[bold green]Success:[/bold green] {success}/{total_tx} txs")
        self.console.print(f"[bold red]Failed:[/bold red] {total_tx - success} txs")


def main():
    chain_choices = ["POLYGON", "OP", "Base", "ARB", "Linea", "ETHER"]
    chain_selection = questionary.select("Select chain:", choices=chain_choices).ask()

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

    app = BatchTransferManager(chain_config)
    app.run()


if __name__ == "__main__":
    main()
