import os
import json
import time
import logging , threading
from typing import List, Optional, Tuple, Dict, Set
from customtkinter import CTk, CTkTextbox, CTkButton, CTkLabel, CTkFrame
import tkinter as tk
from tkinter import messagebox,StringVar
from web3 import Web3
from eth_account import Account
from web3.types import HexBytes

import sys , re

from .rpc_provider import RotatingHTTPProvider
import config


NATIVE_SENTINEL = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE".lower()


class Web3Helper:
    """
    Consolidated Web3 utilities for RPC rotation, gas, tx lifecycle,
    multicall wiring, ERC20 helpers, and ENS.

    This class owns a rotating provider and a Web3 instance.
    """

    def __init__(self, chain_config, console=None):
        self.console = console
        self.cfg = chain_config

        # Build RPC URLs and provider
        self.rpc_urls: List[str] = self._build_rpc_urls(chain_config)
        self.provider = RotatingHTTPProvider(self.rpc_urls)
        # Attach console if caller wants logs inside provider
        setattr(self.provider, "console", console)
        self.w3 = Web3(self.provider)
        self.ens = getattr(self.w3, "ens", None)

        self.wallet_addresses: list[str] = getattr(self, "wallet_addresses", [])
        self.tokens: list[str] = getattr(self, "tokens", [])
        self.ens_names: list[str] = getattr(self, "ens_names", [])
        self.private_keys: list[str] = getattr(self, "private_keys", [])
        self.pk_addresses: list[str] = getattr(self, "pk_addresses", [])

        # Multicall3 (optional)
        self.multicall = self.w3.eth.contract(
            address= self.w3.to_checksum_address(self.cfg.MULTICALL3_ADDRESS),
            abi= json.loads(self.cfg.MULTICALL3_ABI))
        self.erc20_abi = json.loads(self.cfg.TOKEN_ABI)
        self.ens_resolver_abi = json.loads(getattr(self.cfg, "ENS_PUBLIC_RESOLVER_ABI")) 
        self._init_ens_clients()

    # ---------- RPC ----------
    def _build_rpc_urls(self, chain_config) -> List[str]:
        urls: List[str] = []
        base = getattr(chain_config, 'ALCHEMY_RPC_URL', None)
        if base:
            urls.append(str(base))

        keys_raw = os.getenv('ALCHEMY_API_KEYS', '')
        keys = [k.strip() for k in keys_raw.split(',') if k.strip()]
        if keys and base and '/v2/' in base:
            prefix = base.split('/v2/')[0] + '/v2/'
            urls.extend([prefix + k for k in keys])

        extras_raw = os.getenv('EXTRA_RPC_URLS', '')
        extras = [u.strip() for u in extras_raw.split(',') if u.strip()]
        urls.extend(extras)

        # de-duplicate
        dedup: List[str] = []
        seen = set()
        for u in urls:
            if u not in seen:
                dedup.append(u)
                seen.add(u)
        if not dedup:
            raise RuntimeError('No RPC URLs configured. Set ALCHEMY_API_KEY or EXTRA_RPC_URLS in .env')
        return dedup

    def _build_ens_rpc_urls(self) -> List[str]:
        urls: List[str] = []
        primary = os.getenv('ENS_MAINNET_RPC_URL', '').strip()
        if primary:
            urls.append(primary)
        extras = os.getenv('ENS_MAINNET_RPC_URLS', '').strip()
        if extras:
            urls.extend([u.strip() for u in extras.split(',') if u.strip()])
        generic = os.getenv('ETH_MAINNET_RPC_URL', '').strip()
        if generic:
            urls.append(generic)
        fallback = getattr(getattr(config, 'ETHER', None), 'ALCHEMY_RPC_URL', None)
        if fallback and 'None' not in str(fallback):
            urls.append(str(fallback))
        dedup: List[str] = []
        seen = set()
        for u in urls:
            if u and u not in seen:
                dedup.append(u)
                seen.add(u)
        return dedup

    def _init_ens_clients(self) -> None:
        self.ens_provider = None
        self.ens_w3 = self.w3
        self.ens_multicall = self.multicall

        chain_name = str(getattr(self.cfg, 'CHAIN_NAME', '') or '').lower()
        ens_rpc_urls = self._build_ens_rpc_urls()

        if chain_name not in ('ethereum', 'eth', 'mainnet') and ens_rpc_urls:
            try:
                self.ens_provider = RotatingHTTPProvider(ens_rpc_urls)
                setattr(self.ens_provider, 'console', self.console)
                self.ens_w3 = Web3(self.ens_provider)
            except Exception as exc:
                if self.console:
                    self.console.log(f"[yellow]Failed to initialize ENS provider: {exc}[/yellow]")
                self.ens_provider = None
                self.ens_w3 = self.w3

        if self.ens_w3 is not self.w3:
            try:
                self.ens_multicall = self.ens_w3.eth.contract(
                    address=self.ens_w3.to_checksum_address(getattr(self.cfg, 'MULTICALL3_ADDRESS', config.MULTICALL3_ADDRESS)),
                    abi=json.loads(getattr(self.cfg, 'MULTICALL3_ABI', config.MULTICALL3_ABI))
                )
            except Exception:
                self.ens_multicall = None
        else:
            self.ens_multicall = self.multicall

        registry_address = getattr(self.cfg, 'ENS_REGISTRY_ADDRESS', '0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e')
        registry_abi = json.loads(getattr(self.cfg, 'ENS_REGISTRY_ABI'))
        self.ens_registry = self.ens_w3.eth.contract(
            address=self.ens_w3.to_checksum_address(registry_address),
            abi=registry_abi
        )

    # ---------- ENS ----------
    def _namehash(self, name: str) -> bytes:
        node = b"\x00" * 32
        if name:
            for label in name.split(".")[::-1]:
                node = Web3.keccak(node + Web3.keccak(text=label))
        return node
    
    def _reverse_node(self, address: str) -> bytes:
        w3 = getattr(self, 'ens_w3', None) or self.w3
        addr_hex = w3.to_checksum_address(address)[2:].lower()
        return self._namehash(f"{addr_hex}.addr.reverse")
    
    def _normalize_addr(self, s: str) -> str | None:
        if not s:
            return None
        s = s.strip()
        try:
            if self.w3.is_address(s):
                return self.w3.to_checksum_address(s)
        except Exception:
            pass
        return None
    
    def _derive_addresses_from_private_keys(self, keys: list[str]) -> tuple[list[str], list[str]]:
        """
        From a list of normalized private keys, derive checksum addresses.
        Skips invalid keys (rare but possible) and returns
        (filtered_keys, derived_addresses) in the same order.
        Uses local operations only - no RPC calls needed since address derivation 
        is purely cryptographic.
        """
        from eth_account import Account

        filtered_keys: list[str] = []
        derived: list[str] = []
        
        # Single pass: derive addresses locally
        for k in keys:
            try:
                # Account.from_key does the private key -> public key -> address derivation
                addr = Account.from_key(k).address
                # to_checksum_address is a local operation that implements EIP-55
                caddr = self.w3.to_checksum_address(addr)
                filtered_keys.append(k)
                derived.append(caddr)
            except Exception as e:
                if self.console:
                    masked = f"{k[:6]}...{k[-4:]}" if len(k) > 12 else "****"
                    self.console.log(f"[yellow]Skipping invalid private key: {masked} ({e})[/yellow]")
        
        return filtered_keys, derived

    def _is_ens_like(self, s: str) -> bool:
        _ENS_RE = re.compile(r"(?i)^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
        if not s or s.startswith("0x"):
            return False
        s = s.strip().lower()
        # allow common ENS-style names; you can restrict to .eth if you prefer
        return bool(_ENS_RE.match(s))
    
    def _parse_wallets_blob(self, blob: str) -> tuple[list[str], list[str]]:
        """
        Accept addresses and ENS names mixed. Delimiters: newline or comma or whitespace.
        Ignores '#' comments and empties. Returns (addresses, ens_names), both de-duped, order-preserved.
        """
        if not blob:
            return ([], [])
        text = blob.replace(",", "\n")
        addrs, ens, seen_a, seen_e = [], [], set(), set()

        def add_addr(a: str):
            if a not in seen_a:
                seen_a.add(a); addrs.append(a)

        def add_ens(n: str):
            n = n.lower()
            if n not in seen_e:
                seen_e.add(n); ens.append(n)

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in re.split(r"[\s,;]+", line):
                if not tok:
                    continue
                a = self._normalize_addr(tok)
                if a:
                    add_addr(a)
                    continue
                if self._is_ens_like(tok):
                    add_ens(tok)
                    continue
                # silently ignore junk; optionally log
                if self.console:
                    self.console.log(f"[yellow]wallets: ignoring invalid entry: {tok}[/yellow]")

        return (addrs, ens)
        
    def _parse_addresses_blob(self, blob: str, label: str) -> list[str]:
        """
        Addresses only (for tokens). Accepts line or comma separated, ignores comments.
        """
        if not blob:
            return []
        text = blob.replace(",", "\n")
        out, seen = [], set()
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in re.split(r"[\s,;]+", line):
                if not tok:
                    continue
                a = self._normalize_addr(tok)
                if a and a not in seen:
                    seen.add(a); out.append(a)
                elif not a and self.console:
                    self.console.log(f"[yellow]{label}: ignoring invalid address: {tok}[/yellow]")
        return out

    

    def _parse_privatekeys_blob(self, blob: str) -> list[str]:
        """
        Private keys: hex with/without 0x, 64 hex chars. Returns normalized '0x' + lowercase, unique.
        """
        _PRIV_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{64})$")
        if not blob:
            return []
        text = blob.replace(",", "\n")
        out, seen = [], set()
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in re.split(r"[\s,;]+", line):
                if not tok:
                    continue
                m = _PRIV_RE.match(tok.strip())
                if not m:
                    if self.console:
                        # mask for safety
                        masked = f"{tok[:6]}...{tok[-4:]}" if len(tok) > 12 else "****"
                        self.console.log(f"[yellow]private key: invalid, skipped: {masked}[/yellow]")
                    continue
                key = "0x" + m.group(1).lower()
                if key not in seen:
                    seen.add(key); out.append(key)
        return out
    
    def _aggregate3(self, calls: List[Tuple[str, bytes]], allow_failure: bool=True, *, w3: Optional[Web3]=None, contract=None) -> List[Tuple[bool, bytes]]:
        """
        Execute Multicall3.aggregate3 using the provided Web3/contract.
        Each call is (target, calldata). Returns [(success, returnData), ...].
        Fallback to single eth_call if needed.
        """
        w3 = w3 or self.w3
        contract = contract or self.multicall
        if contract:
            try:
                call3 = [{"target": w3.to_checksum_address(t), "allowFailure": allow_failure, "callData": d} for t, d in calls]
                results = contract.functions.aggregate3(call3).call()
                return [(bool(r[0] if isinstance(r, (list, tuple)) else r['success']),
                         bytes(r[1] if isinstance(r, (list, tuple)) else r['returnData'])) for r in results]
            except Exception as e:
                if self.console:
                    self.console.log(f"[yellow]aggregate3 failed, falling back: {e}[/yellow]")
        out: List[Tuple[bool, bytes]] = []
        for target, data in calls:
            try:
                ret = w3.eth.call({'to': w3.to_checksum_address(target), 'data': data})
                out.append((True, ret))
            except Exception:
                out.append((False, b''))
        return out
        
    def _enc(self, addr: str, fn: str, args=None):
        c = self.w3.eth.contract(address=self.w3.to_checksum_address(addr), abi=self.erc20_abi)
        return HexBytes(c.encodeABI(fn_name=fn, args=args or []))
    
    def _decode_string_like(self, data: bytes) -> Optional[str]:
        """Decode string or bytes32 -> str (handles non-standard ERC-20s)."""
        if not data:
            return None
        try:
            s = self.w3.codec.decode(['string'], data)[0]
            return s if isinstance(s, str) else s.decode('utf-8', errors='ignore')
        except Exception:
            pass
        try:
            b32 = self.w3.codec.decode(['bytes32'], data)[0]
            if isinstance(b32, (bytes, bytearray)):
                return bytes(b32).rstrip(b'\x00').decode('utf-8', errors='ignore') or None
        except Exception:
            pass
        return None
    
    def batch_ens_reverse(self, wallets: List[str], use_multicall: bool=True) -> Dict[str, Optional[str]]:
        w3 = getattr(self, 'ens_w3', None) or self.w3
        registry = self.ens_registry
        nodes = {w3.to_checksum_address(w): self._reverse_node(w) for w in wallets}

        if use_multicall and self.ens_multicall is not None:
            calls = [(registry.address, registry.encodeABI(fn_name='resolver', args=[node])) for node in nodes.values()]
            res = self._aggregate3(calls, allow_failure=True, w3=w3, contract=self.ens_multicall)
        else:
            res = []
            for node in nodes.values():
                try:
                    r = registry.functions.resolver(node).call()
                    res.append((True, w3.codec.encode(['address'], [r])))
                except Exception:
                    res.append((False, b''))

        resolvers: Dict[str, str] = {}
        for (wallet, node), (ok, data) in zip(nodes.items(), res):
            if ok and data and len(data) >= 32:
                try:
                    raddr = w3.codec.decode(['address'], data)[0]
                    resolvers[wallet] = raddr
                except Exception:
                    resolvers[wallet] = '0x0000000000000000000000000000000000000000'
            else:
                resolvers[wallet] = '0x0000000000000000000000000000000000000000'

        out: Dict[str, Optional[str]] = {w: None for w in wallets}
        by_resolver: Dict[str, List[Tuple[str, bytes]]] = {}
        for w, node in nodes.items():
            r = resolvers.get(w)
            if r and int(r, 16) != 0:
                by_resolver.setdefault(r, []).append((w, node))

        for raddr, pairs in by_resolver.items():
            resolver = w3.eth.contract(address=w3.to_checksum_address(raddr), abi=self.ens_resolver_abi)
            if use_multicall and self.ens_multicall is not None:
                calls = [(raddr, resolver.encodeABI(fn_name='name', args=[node])) for _, node in pairs]
                res2 = self._aggregate3(calls, allow_failure=True, w3=w3, contract=self.ens_multicall)
            else:
                res2 = []
                for _, node in pairs:
                    try:
                        nm = resolver.functions.name(node).call()
                        res2.append((True, w3.codec.encode(['string'], [nm])))
                    except Exception:
                        res2.append((False, b''))
            for (wallet, _node), (ok, data) in zip(pairs, res2):
                if ok and data:
                    try:
                        nm = resolver.decode_function_output('name', data)[0]
                        out[wallet] = nm or None
                    except Exception:
                        out[wallet] = None
        return out



    def batch_ens_forward(self, ens_names: List[str], use_multicall: bool=True) -> Dict[str, Optional[str]]:
        w3 = getattr(self, 'ens_w3', None) or self.w3
        registry = self.ens_registry
        name_nodes = {n: self._namehash(n) for n in ens_names}

        if use_multicall and self.ens_multicall is not None:
            calls = [(registry.address, registry.encodeABI(fn_name='resolver', args=[node])) for node in name_nodes.values()]
            res = self._aggregate3(calls, allow_failure=True, w3=w3, contract=self.ens_multicall)
        else:
            res = []
            for node in name_nodes.values():
                try:
                    r = registry.functions.resolver(node).call()
                    res.append((True, w3.codec.encode(['address'], [r])))
                except Exception:
                    res.append((False, b''))

        resolvers: Dict[str, str] = {}
        for (nm, node), (ok, data) in zip(name_nodes.items(), res):
            if ok and data and len(data) >= 32:
                try:
                    raddr = w3.codec.decode(['address'], data)[0]
                    resolvers[nm] = raddr
                except Exception:
                    resolvers[nm] = '0x0000000000000000000000000000000000000000'
            else:
                resolvers[nm] = '0x0000000000000000000000000000000000000000'

        out: Dict[str, Optional[str]] = {n: None for n in ens_names}
        by_resolver: Dict[str, List[Tuple[str, bytes]]] = {}
        for nm, node in name_nodes.items():
            r = resolvers.get(nm)
            if r and int(r, 16) != 0:
                by_resolver.setdefault(r, []).append((nm, node))

        for raddr, pairs in by_resolver.items():
            resolver = w3.eth.contract(address=w3.to_checksum_address(raddr), abi=self.ens_resolver_abi)
            if use_multicall and self.ens_multicall is not None:
                calls = [(raddr, resolver.encodeABI(fn_name='addr', args=[node])) for _, node in pairs]
                res2 = self._aggregate3(calls, allow_failure=True, w3=w3, contract=self.ens_multicall)
            else:
                res2 = []
                for _, node in pairs:
                    try:
                        a = resolver.functions.addr(node).call()
                        res2.append((True, w3.codec.encode(['address'], [a])))
                    except Exception:
                        res2.append((False, b''))
            for (nm, _node), (ok, data) in zip(pairs, res2):
                if ok and data and len(data) >= 32:
                    try:
                        a = w3.codec.decode(['address'], data)[0]
                        out[nm] = w3.to_checksum_address(a) if int(a, 16) != 0 else None
                    except Exception:
                        out[nm] = None
        return out

    # ---------- Gas ----------
    def fetch_suggested_fees(self, api_url: Optional[str], tier: str = 'medium') -> Tuple[Optional[int], Optional[int]]:
        import requests
        try:
            if not api_url:
                raise ValueError("No gas API URL provided")

            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            gas_data = response.json()  # Fixed: Called json() as a method

            # Check if gas_data has the expected structure
            if tier not in gas_data:
                raise KeyError(f"Gas tier '{tier}' not found in response")
            if 'suggestedMaxFeePerGas' not in gas_data[tier] or 'suggestedMaxPriorityFeePerGas' not in gas_data[tier]:
                raise KeyError("Missing required gas fee fields in response")

            max_fee_per_gas = float(gas_data[tier]['suggestedMaxFeePerGas'])
            max_priority_fee_per_gas = float(gas_data[tier]['suggestedMaxPriorityFeePerGas'])
            
            # Convert from Gwei to Wei
            max_fee_per_gas_wei = Web3.to_wei(max_fee_per_gas, 'gwei')
            max_priority_fee_per_gas_wei = Web3.to_wei(max_priority_fee_per_gas, 'gwei')

            self.console.log(f"[bold yellow]Fetched gas fees - Max Fee Per Gas:[/bold yellow] {max_fee_per_gas} Gwei, [bold yellow]Max Priority Fee Per Gas:[/bold yellow] {max_priority_fee_per_gas} Gwei")

            return max_fee_per_gas_wei, max_priority_fee_per_gas_wei
        
        except requests.exceptions.HTTPError as http_err:
            if self.console:
                self.console.log(f"[red]HTTP error occurred while fetching gas fees: {http_err}[/red]")
        except requests.exceptions.Timeout:
            if self.console:
                self.console.log("[red]Timeout while fetching gas fees from API[/red]")
        except (KeyError, ValueError) as err:
            if self.console:
                self.console.log(f"[red]Invalid gas fee data format: {err}[/red]")
        except Exception as err:
            if self.console:
                self.console.log(f"[red]An error occurred while fetching gas fees: {err}[/red]")
        
        try:
            base_fee = self.w3.eth.get_block('latest').baseFeePerGas
            tip = self.w3.eth.max_priority_fee
            return int(base_fee + tip), int(tip)
        except Exception:
            try:
                gp = self.w3.eth.gas_price
                return int(gp), None
            except Exception:
                return None, None

    # ---------- Tx lifecycle ----------
    def wait_for_receipt(self, tx_hash: HexBytes, timeout: int = 300, start_delay: float = 2, max_delay: float = 8):
        start = time.time()
        delay = start_delay
        while True:
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt:
                    return receipt
            except Exception:
                pass
            if time.time() - start > timeout:
                raise TimeoutError("Timed out waiting for transaction receipt")
            time.sleep(delay)
            delay = min(max_delay, delay * 1.5)

    # ---------- ERC20 ----------
    def _erc20(self, token_address: str):
        return self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.erc20_abi)

    def check_token_balance(self, token_address: str, account_address: str) -> Optional[int]:
        if not token_address:
            return None
        if token_address.lower() in {NATIVE_SENTINEL, getattr(self.chain_config, 'NATIVE_TOKEN', '').lower()}:
            try:
                return int(self.w3.eth.get_balance(self.w3.to_checksum_address(account_address)))
            except Exception:
                return None
        try:
            c = self._erc20(token_address)
            return int(c.functions.balanceOf(self.w3.to_checksum_address(account_address)).call())
        except Exception:
            return None

    def check_allowance(self, token_address: str, owner_address: str, spender_address: str) -> Optional[int]:
        try:
            c = self._erc20(token_address)
            return int(c.functions.allowance(
                self.w3.to_checksum_address(owner_address),
                self.w3.to_checksum_address(spender_address)
            ).call())
        except Exception:
            return None

    def send_approval(self, private_key: str, token_address: str, spender: str, amount: int,
                      max_fee_per_gas: Optional[int] = None, max_priority_fee_per_gas: Optional[int] = None) -> HexBytes:
        acct = Account.from_key(private_key)
        c = self._erc20(token_address)
        tx = c.functions.approve(self.w3.to_checksum_address(spender), int(amount)).build_transaction({
            'from': acct.address,
            'chainId': int(getattr(self.chain_config, 'CHAIN_ID', 0)),
            'nonce': self.w3.eth.get_transaction_count(acct.address),
            'type': 2,
            'maxFeePerGas': max_fee_per_gas or self.w3.eth.gas_price,
            'maxPriorityFeePerGas': max_priority_fee_per_gas or self.w3.eth.max_priority_fee,
        })
        signed = self.w3.eth.account.sign_transaction(tx, private_key=private_key)
        return self.w3.eth.send_raw_transaction(signed.rawTransaction)

    def send_erc20(self, private_key: str, token_address: str, to: str, amount_wei: int,
                   max_fee_per_gas: Optional[int] = None, max_priority_fee_per_gas: Optional[int] = None) -> HexBytes:
        acct = Account.from_key(private_key)
        if token_address.lower() in {NATIVE_SENTINEL, getattr(self.chain_config, 'NATIVE_TOKEN', '').lower()}:
            tx = {
                'from': acct.address,
                'to': self.w3.to_checksum_address(to),
                'value': int(amount_wei),
                'chainId': int(getattr(self.chain_config, 'CHAIN_ID', 0)),
                'nonce': self.w3.eth.get_transaction_count(acct.address),
                'type': 2,
                'maxFeePerGas': max_fee_per_gas or self.w3.eth.gas_price,
                'maxPriorityFeePerGas': max_priority_fee_per_gas or self.w3.eth.max_priority_fee,
            }
        else:
            c = self._erc20(token_address)
            tx = c.functions.transfer(self.w3.to_checksum_address(to), int(amount_wei)).build_transaction({
                'from': acct.address,
                'chainId': int(getattr(self.chain_config, 'CHAIN_ID', 0)),
                'nonce': self.w3.eth.get_transaction_count(acct.address),
                'type': 2,
                'maxFeePerGas': max_fee_per_gas or self.w3.eth.gas_price,
                'maxPriorityFeePerGas': max_priority_fee_per_gas or self.w3.eth.max_priority_fee,
            })
        signed = self.w3.eth.account.sign_transaction(tx, private_key=private_key)
        return self.w3.eth.send_raw_transaction(signed.rawTransaction)
    
    def load_wallets_file(self, wallet_file: str) -> tuple[list[str], list[str]]:
        """
        Reads a file that may contain addresses and ENS names mixed.
        Returns (addresses, ens_names) and also stores them on the helper.
        """
        try:
            with open(wallet_file, "r", encoding="utf-8-sig") as f:
                blob = f.read()
        except Exception as e:
            if self.console:
                self.console.log(f"[red]Failed to read wallets file {wallet_file}: {e}[/red]")
            self.wallet_addresses, self.ens_names = [], []
            return ([], [])
        addrs, ens = self._parse_wallets_blob(blob)
        self.wallet_addresses, self.ens_names = addrs, ens
        return (addrs, ens)

    def load_wallets_gui(self) -> tuple[list[str], list[str]]:
        try:
            import customtkinter as ctk

            class WalletInputDialog:
                def __init__(self):
                    # Configure appearance
                    ctk.set_appearance_mode("dark")
                    ctk.set_default_color_theme("blue")
                    self.root = ctk.CTk()
                    self.root.title("Wallet Input")
                    self.root.geometry("600x500")
                    self.root.resizable(True, True)
                    self.root.eval('tk::PlaceWindow . center')
                    self.root.protocol("WM_DELETE_WINDOW", self.on_cancel)
                    self.result = None
                    self.destroyed = False
                    self.setup_ui()
                    # Ensure window appears centered and focused/selected after launch
                    try:
                        self.root.after(100, self.center_and_focus)
                    except Exception:
                        pass
                
                def setup_ui(self):
                    # Main container with modern styling
                    main_frame = ctk.CTkFrame(self.root, corner_radius=15)
                    main_frame.pack(fill="both", expand=True, padx=20, pady=20)
                    # Header section
                    header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    header_frame.pack(fill="x", padx=20, pady=(20, 10))
                    # Icon and title
                    icon_label = ctk.CTkLabel(
                        header_frame, 
                        text="👛",
                        font=("Arial", 24),
                        text_color="#4CC9F0"
                    )
                    icon_label.pack(side="left", padx=(0, 10))
                    
                    title_label = ctk.CTkLabel(
                        header_frame,
                        text="Add Wallet Addresses",
                        font=("Arial", 20, "bold"),
                        text_color="white"
                    )
                    title_label.pack(side="left")
                    
                    # Instructions
                    instructions = ctk.CTkLabel(
                        main_frame,
                        text="Enter wallet addresses and/or ENS names\nYou can separate them with commas, spaces, or newlines",
                        font=("Arial", 14),
                        text_color="#B0B0B0",
                        justify="left"
                    )
                    instructions.pack(pady=(0, 20))
                    
                    # Text input area with modern styling
                    input_frame = ctk.CTkFrame(main_frame, corner_radius=10)
                    input_frame.pack(fill="both", expand=True, padx=20, pady=10)
                    
                    self.text_widget = ctk.CTkTextbox(
                        input_frame,
                        height=200,
                        border_width=0,
                        corner_radius=8,
                        fg_color="#2B2B2B",
                        text_color="white",
                        font=("Consolas", 12),
                        wrap="word"
                    )
                    self.text_widget.pack(fill="both", expand=True, padx=10, pady=10)
                    self.text_widget.focus_set()
                    
                    # Character counter
                    self.counter_var = StringVar(value="0 characters")
                    counter_label = ctk.CTkLabel(
                        main_frame,
                        textvariable=self.counter_var,
                        font=("Arial", 11),
                        text_color="#808080"
                    )
                    counter_label.pack(pady=(5, 0))

                    self.setup_counter_updater()
                    
                    # Button frame
                    button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    button_frame.pack(fill="x", padx=20, pady=20)
                    
                    # Cancel button
                    cancel_btn = ctk.CTkButton(
                        button_frame,
                        text="Cancel",
                        command=self.on_cancel,
                        fg_color="transparent",
                        border_width=2,
                        text_color=("black", "white"),
                        hover_color="#3A3A3A",
                        font=("Arial", 14, "bold")
                    )
                    cancel_btn.pack(side="left", padx=(0, 10))
                    
                    # Submit button
                    submit_btn = ctk.CTkButton(
                        button_frame,
                        text="Import Wallets",
                        command=self.on_submit,
                        fg_color="#4CC9F0",
                        hover_color="#3AA8CC",
                        font=("Arial", 14, "bold"),
                        text_color="white"
                    )
                    submit_btn.pack(side="right")
                    
                    # Bind Enter key to submit
                    self.root.bind('<Return>', lambda e: self.on_submit())
                    self.root.bind('<Escape>', lambda e: self.on_cancel())
                    
                    # Update counter initially
                    self.update_counter()

                def center_and_focus(self):
                    try:
                        # Compute geometry and center
                        self.root.update_idletasks()
                        w = self.root.winfo_width() or 600
                        h = self.root.winfo_height() or 500
                        # If not yet realized, parse from current geometry string
                        if w <= 1 or h <= 1:
                            try:
                                g = self.root.geometry()
                                size = g.split('+')[0]
                                w, h = [int(x) for x in size.split('x')]
                            except Exception:
                                w, h = 600, 500
                        sw = self.root.winfo_screenwidth()
                        sh = self.root.winfo_screenheight()
                        x = max(0, (sw - w) // 2)
                        y = max(0, (sh - h) // 2)
                        self.root.geometry(f"+{x}+{y}")

                        # Bring to front and focus so users don't need to re-select
                        self.root.deiconify()
                        self.root.lift()
                        # temporary topmost to steal focus on Windows, then drop it
                        self.root.attributes('-topmost', True)
                        self.root.after(300, lambda: self.root.attributes('-topmost', False))
                        # focus window and primary input
                        self.root.focus_force()
                        try:
                            self.text_widget.focus_set()
                        except Exception:
                            pass
                    except Exception:
                        pass
                
                def setup_counter_updater(self):
                    def poll_counter():
                        if not self.destroyed:
                            self.update_counter()
                            self.root.after(500, poll_counter)
                    self.root.after(500, poll_counter)

                def update_counter(self, event=None):
                    try: 
                        if self.destroyed:
                            return
                        text = self.text_widget.get("1.0", "end-1c")
                        char_count = len(text)
                        items = [x for x in text.replace(',', ' ').replace('\n', ' ').split() if x.strip()]
                        item_count = len(items)
                        self.counter_var.set(f"{char_count} characters, {item_count} items")
                    except Exception:
                        # Ignore errors during update
                        pass
                
                def on_submit(self):
                    try:
                        text = self.text_widget.get("1.0", "end-1c").strip()
                        self.result = text
                        self.cleanup()
                    except Exception:
                        self.result = ""
                        self.cleanup()
                    
                def on_cancel(self):
                    self.result = ""
                    self.cleanup()
                
                def cleanup(self):
                    """Proper cleanup to avoid after script errors"""
                    self.destroyed = True
                    try:
                        self.root.quit()
                    except:
                        pass
                    try:
                        self.root.destroy()
                    except:
                        pass

                def get_input(self):
                    self.root.mainloop()
                    return self.result
            
            # Create and run the dialog
            dialog = WalletInputDialog()
            blob = dialog.get_input()
            
        except Exception as e:
            # Fallback to traditional input if GUI fails
            print(f"GUI failed: {e}")
            blob = input("Paste wallet addresses and/or ENS names: ").strip()
        
        # Parse the input
        addrs, ens = self._parse_wallets_blob(blob or "")
        self.wallet_addresses, self.ens_names = addrs, ens
        
        # Show success message if we have results
        if addrs or ens:
            success_msg = f"Successfully imported {len(addrs)} addresses and {len(ens)} ENS names"
            try:
                # Show a brief success notification
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("Success", success_msg)
                root.destroy()
            except:
                print(success_msg)
        
        return (addrs, ens)
    
    
    def load_wallets_cli(self) -> tuple[list[str], list[str]]:
        """
        CLI prompt (newline/comma/space separated). Accepts addresses and ENS names.
        """
        try:
            import questionary as q
            blob = q.text("Paste wallet addresses and/or ENS names:").ask()
        except Exception:
            blob = input("Paste wallet addresses and/or ENS names: ").strip()
        addrs, ens = self._parse_wallets_blob(blob or "")
        self.wallet_addresses, self.ens_names = addrs, ens
        return (addrs, ens)
    
    def load_tokens_file(self, token_file: str) -> list[str]:
        try:
            with open(token_file, "r", encoding="utf-8-sig") as f:
                blob = f.read()
        except Exception as e:
            if self.console:
                self.console.log(f"[red]Failed to read tokens file {token_file}: {e}[/red]")
            self.erc20_tokens = []
            return []
        toks = self._parse_addresses_blob(blob, "tokens")
        self.erc20_tokens = toks
        return toks

    def load_tokens_cli(self) -> list[str]:
        try:
            import questionary as q
            blob = q.text("Paste token addresses (comma/newline/space separated):").ask()
        except Exception:
            blob = input("Paste token addresses: ").strip()
        toks = self._parse_addresses_blob(blob or "", "tokens")
        self.erc20_tokens = toks
        return toks

    def load_tokens_gui(self) -> list[str]:
        try:
            import customtkinter as ctk
        
            class TokenInputDialog:
                def __init__(self):
                    # Configure appearance
                    ctk.set_appearance_mode("dark")
                    ctk.set_default_color_theme("blue")
                    self.root = ctk.CTk()
                    self.root.title("Token Input")
                    self.root.geometry("600x500")
                    self.root.resizable(True, True)
                    self.root.eval('tk::PlaceWindow . center')
                    self.root.protocol("WM_DELETE_WINDOW", self.on_cancel)
                    self.result = None
                    self.destroyed = False
                    self.setup_ui()
                    # Ensure window appears centered and focused/selected after launch
                    try:
                        self.root.after(100, self.center_and_focus)
                    except Exception:
                        pass
                
                def setup_ui(self):
                    # Main container with modern styling
                    main_frame = ctk.CTkFrame(self.root, corner_radius=15)
                    main_frame.pack(fill="both", expand=True, padx=20, pady=20)
                    # Header section
                    header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    header_frame.pack(fill="x", padx=20, pady=(20, 10))
                    # Icon and title
                    icon_label = ctk.CTkLabel(
                        header_frame, 
                        text="👛",
                        font=("Arial", 24),
                        text_color="#4CC9F0"
                    )
                    icon_label.pack(side="left", padx=(0, 10))
                    
                    title_label = ctk.CTkLabel(
                        header_frame,
                        text="Add Token Addresses",
                        font=("Arial", 20, "bold"),
                        text_color="white"
                    )
                    title_label.pack(side="left")
                    
                    # Instructions
                    instructions = ctk.CTkLabel(
                        main_frame,
                        text="Enter token addresses\nYou can separate them with commas, spaces, or newlines",
                        font=("Arial", 14),
                        text_color="#B0B0B0",
                        justify="left"
                    )
                    instructions.pack(pady=(0, 20))
                    
                    # Text input area with modern styling
                    input_frame = ctk.CTkFrame(main_frame, corner_radius=10)
                    input_frame.pack(fill="both", expand=True, padx=20, pady=10)
                    
                    self.text_widget = ctk.CTkTextbox(
                        input_frame,
                        height=200,
                        border_width=0,
                        corner_radius=8,
                        fg_color="#2B2B2B",
                        text_color="white",
                        font=("Consolas", 12),
                        wrap="word"
                    )
                    self.text_widget.pack(fill="both", expand=True, padx=10, pady=10)
                    self.text_widget.focus_set()
                    
                    # Character counter
                    self.counter_var = StringVar(value="0 characters")
                    counter_label = ctk.CTkLabel(
                        main_frame,
                        textvariable=self.counter_var,
                        font=("Arial", 11),
                        text_color="#808080"
                    )
                    counter_label.pack(pady=(5, 0))

                    self.setup_counter_updater()
                    
                    # Button frame
                    button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    button_frame.pack(fill="x", padx=20, pady=20)
                    
                    # Cancel button
                    cancel_btn = ctk.CTkButton(
                        button_frame,
                        text="Cancel",
                        command=self.on_cancel,
                        fg_color="transparent",
                        border_width=2,
                        text_color=("black", "white"),
                        hover_color="#3A3A3A",
                        font=("Arial", 14, "bold")
                    )
                    cancel_btn.pack(side="left", padx=(0, 10))
                    
                    # Submit button
                    submit_btn = ctk.CTkButton(
                        button_frame,
                        text="Import Tokens",
                        command=self.on_submit,
                        fg_color="#4CC9F0",
                        hover_color="#3AA8CC",
                        font=("Arial", 14, "bold"),
                        text_color="white"
                    )
                    submit_btn.pack(side="right")
                    
                    # Bind Enter key to submit
                    self.root.bind('<Return>', lambda e: self.on_submit())
                    self.root.bind('<Escape>', lambda e: self.on_cancel())
                    
                    # Update counter initially
                    self.update_counter()

                def center_and_focus(self):
                    try:
                        self.root.update_idletasks()
                        w = self.root.winfo_width() or 600
                        h = self.root.winfo_height() or 500
                        if w <= 1 or h <= 1:
                            try:
                                g = self.root.geometry(); size = g.split('+')[0]
                                w, h = [int(x) for x in size.split('x')]
                            except Exception:
                                w, h = 600, 500
                        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
                        x = max(0, (sw - w) // 2); y = max(0, (sh - h) // 2)
                        self.root.geometry(f"+{x}+{y}")
                        self.root.deiconify(); self.root.lift()
                        self.root.attributes('-topmost', True)
                        self.root.after(300, lambda: self.root.attributes('-topmost', False))
                        self.root.focus_force()
                        try:
                            self.text_widget.focus_set()
                        except Exception:
                            pass
                    except Exception:
                        pass
                
                def setup_counter_updater(self):
                    def poll_counter():
                        if not self.destroyed:
                            self.update_counter()
                            self.root.after(500, poll_counter)
                    self.root.after(500, poll_counter)

                def update_counter(self, event=None):
                    try: 
                        if self.destroyed:
                            return
                        text = self.text_widget.get("1.0", "end-1c")
                        char_count = len(text)
                        items = [x for x in text.replace(',', ' ').replace('\n', ' ').split() if x.strip()]
                        item_count = len(items)
                        self.counter_var.set(f"{char_count} characters, {item_count} items")
                    except Exception:
                        # Ignore errors during update
                        pass
                
                def on_submit(self):
                    try:
                        text = self.text_widget.get("1.0", "end-1c").strip()
                        self.result = text
                        self.cleanup()
                    except Exception:
                        self.result = ""
                        self.cleanup()
                    
                def on_cancel(self):
                    self.result = ""
                    self.cleanup()
                
                def cleanup(self):
                    """Proper cleanup to avoid after script errors"""
                    self.destroyed = True
                    try:
                        self.root.quit()
                    except:
                        pass
                    try:
                        self.root.destroy()
                    except:
                        pass

                def get_input(self):
                    self.root.mainloop()
                    return self.result
                
            # Create and run the dialog
            dialog = TokenInputDialog()
            blob = dialog.get_input()
            
        except Exception as e:
            # Fallback to traditional input if GUI fails
            print(f"GUI failed: {e}")
            blob = input("Paste token addresses: ").strip()
        
        # Parse the input
        toks = self._parse_addresses_blob(blob or "", "tokens")
        self.erc20_tokens = toks
        
        # Show success message if we have results
        if toks:
            success_msg = f"Successfully imported {len(toks)} token addresses"
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("Success", success_msg)
                root.destroy()
            except:
                print(success_msg)
        
        return toks

    def load_privatekeys_file(self, key_file: str) -> tuple[list[str], list[str]]:
        try:
            with open(key_file, "r", encoding="utf-8-sig") as f:
                blob = f.read()
        except Exception as e:
            if self.console:
                self.console.log(f"[red]Failed to read private keys file {key_file}: {e}[/red]")
            self.private_keys = []
            self.pk_addresses = []
            return ([], [])

        keys_in = self._parse_privatekeys_blob(blob)          # normalized, deduped
        keys, addrs = self._derive_addresses_from_private_keys(keys_in)

        self.private_keys = keys
        self.pk_addresses = addrs
        return (keys, addrs)


    def load_privatekeys_cli(self) -> tuple[list[str], list[str]]:
        """
        CLI prompt. NOTE: not masked. For masking, collect per-line via getpass in a loop.
        """
        try:
            import questionary as q
            blob = q.text("Paste private keys (hex; with or without 0x; comma/newline separated):").ask()
        except Exception:
            blob = input("Paste private keys (hex; with or without 0x): ").strip()

        keys_in = self._parse_privatekeys_blob(blob or "")
        keys, addrs = self._derive_addresses_from_private_keys(keys_in)

        self.private_keys = keys
        self.pk_addresses = addrs
        return (keys, addrs)


    def load_privatekeys_gui(self) -> tuple[list[str], list[str]]:
        try:
            import customtkinter as ctk
            class TokenInputDialog:
                def __init__(self):
                    # Configure appearance
                    ctk.set_appearance_mode("dark")
                    ctk.set_default_color_theme("blue")
                    self.root = ctk.CTk()
                    self.root.title("Private key Input")
                    self.root.geometry("600x500")
                    self.root.resizable(True, True)
                    self.root.eval('tk::PlaceWindow . center')
                    self.root.protocol("WM_DELETE_WINDOW", self.on_cancel)
                    self.result = None
                    self.destroyed = False
                    self.setup_ui()
                    # Ensure window appears centered and focused/selected after launch
                    try:
                        self.root.after(100, self.center_and_focus)
                    except Exception:
                        pass
                
                def setup_ui(self):
                    # Main container with modern styling
                    main_frame = ctk.CTkFrame(self.root, corner_radius=15)
                    main_frame.pack(fill="both", expand=True, padx=20, pady=20)
                    # Header section
                    header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    header_frame.pack(fill="x", padx=20, pady=(20, 10))
                    # Icon and title
                    icon_label = ctk.CTkLabel(
                        header_frame, 
                        text="🔑",
                        font=("Arial", 24),
                        text_color="#4CC9F0"
                    )
                    icon_label.pack(side="left", padx=(0, 10))
                    
                    title_label = ctk.CTkLabel(
                        header_frame,
                        text="Add Private key",
                        font=("Arial", 20, "bold"),
                        text_color="white"
                    )
                    title_label.pack(side="left")
                    
                    # Instructions
                    instructions = ctk.CTkLabel(
                        main_frame,
                        text="Enter private keys\nYou can separate them with commas, spaces, or newlines",
                        font=("Arial", 14),
                        text_color="#B0B0B0",
                        justify="left"
                    )
                    instructions.pack(pady=(0, 20))
                    
                    # Text input area with modern styling
                    input_frame = ctk.CTkFrame(main_frame, corner_radius=10)
                    input_frame.pack(fill="both", expand=True, padx=20, pady=10)
                    
                    self.text_widget = ctk.CTkTextbox(
                        input_frame,
                        height=200,
                        border_width=0,
                        corner_radius=8,
                        fg_color="#2B2B2B",
                        text_color="white",
                        font=("Consolas", 12),
                        wrap="word"
                    )
                    self.text_widget.pack(fill="both", expand=True, padx=10, pady=10)
                    self.text_widget.focus_set()
                    
                    # Character counter
                    self.counter_var = StringVar(value="0 characters")
                    counter_label = ctk.CTkLabel(
                        main_frame,
                        textvariable=self.counter_var,
                        font=("Arial", 11),
                        text_color="#808080"
                    )
                    counter_label.pack(pady=(5, 0))

                    self.setup_counter_updater()
                    
                    # Button frame
                    button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
                    button_frame.pack(fill="x", padx=20, pady=20)
                    
                    # Cancel button
                    cancel_btn = ctk.CTkButton(
                        button_frame,
                        text="Cancel",
                        command=self.on_cancel,
                        fg_color="transparent",
                        border_width=2,
                        text_color=("black", "white"),
                        hover_color="#3A3A3A",
                        font=("Arial", 14, "bold")
                    )
                    cancel_btn.pack(side="left", padx=(0, 10))
                    
                    # Submit button
                    submit_btn = ctk.CTkButton(
                        button_frame,
                        text="Import Private keys",
                        command=self.on_submit,
                        fg_color="#4CC9F0",
                        hover_color="#3AA8CC",
                        font=("Arial", 14, "bold"),
                        text_color="white"
                    )
                    submit_btn.pack(side="right")
                    
                    # Bind Enter key to submit
                    self.root.bind('<Return>', lambda e: self.on_submit())
                    self.root.bind('<Escape>', lambda e: self.on_cancel())
                    
                    # Update counter initially
                    self.update_counter()

                def center_and_focus(self):
                    try:
                        self.root.update_idletasks()
                        w = self.root.winfo_width() or 600
                        h = self.root.winfo_height() or 500
                        if w <= 1 or h <= 1:
                            try:
                                g = self.root.geometry(); size = g.split('+')[0]
                                w, h = [int(x) for x in size.split('x')]
                            except Exception:
                                w, h = 600, 500
                        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
                        x = max(0, (sw - w) // 2); y = max(0, (sh - h) // 2)
                        self.root.geometry(f"+{x}+{y}")
                        self.root.deiconify(); self.root.lift()
                        self.root.attributes('-topmost', True)
                        self.root.after(300, lambda: self.root.attributes('-topmost', False))
                        self.root.focus_force()
                        try:
                            self.text_widget.focus_set()
                        except Exception:
                            pass
                    except Exception:
                        pass
                
                def setup_counter_updater(self):
                    def poll_counter():
                        if not self.destroyed:
                            self.update_counter()
                            self.root.after(500, poll_counter)
                    self.root.after(500, poll_counter)

                def update_counter(self, event=None):
                    try: 
                        if self.destroyed:
                            return
                        text = self.text_widget.get("1.0", "end-1c")
                        char_count = len(text)
                        items = [x for x in text.replace(',', ' ').replace('\n', ' ').split() if x.strip()]
                        item_count = len(items)
                        self.counter_var.set(f"{char_count} characters, {item_count} items")
                    except Exception:
                        # Ignore errors during update
                        pass
                
                def on_submit(self):
                    try:
                        text = self.text_widget.get("1.0", "end-1c").strip()
                        self.result = text
                        self.cleanup()
                    except Exception:
                        self.result = ""
                        self.cleanup()
                    
                def on_cancel(self):
                    self.result = ""
                    self.cleanup()
                
                def cleanup(self):
                    """Proper cleanup to avoid after script errors"""
                    self.destroyed = True
                    try:
                        self.root.quit()
                    except:
                        pass
                    try:
                        self.root.destroy()
                    except:
                        pass

                def get_input(self):
                    self.root.mainloop()
                    return self.result
                
            # Create and run the dialog
            dialog = TokenInputDialog()
            blob = dialog.get_input()
            
        except Exception as e:
            # Fallback to traditional input if GUI fails
            print(f"GUI failed: {e}")
            blob = input("Paste Private keys: ").strip()
        
        # Parse the input
        pks = self._parse_privatekeys_blob(blob or "")
        keys, addrs = self._derive_addresses_from_private_keys(pks)
        
        # Show success message if we have results
        if keys and addrs:
            success_msg = f"Successfully imported {len(keys)} pk addresses"
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo("Success", success_msg)
                root.destroy()
            except:
                print(success_msg)
        self.private_keys = keys
        self.pk_addresses = addrs
        return (keys,addrs)

    def _multicall_fetch_labels(self, addrs, do_name: bool, do_symbol: bool):
        """
        Internal: fetch token name/symbol via Multicall3 (aggregate3) with safe decode.
        Returns: {addr: {'name': str|None, 'symbol': str|None}}
        """
        out = {self.w3.to_checksum_address(a): {} for a in addrs}
        calls, order = [], []

        sel_name   = HexBytes('0x06fdde03')  # name()
        sel_symbol = HexBytes('0x95d89b41')  # symbol()

        for a in addrs:
            T = self.w3.to_checksum_address(a)
            if do_name:
                try:
                    calls.append((T, sel_name))
                    order.append((T, "name"))
                except Exception:
                    pass
            if do_symbol:
                try:
                    calls.append((T, sel_symbol))
                    order.append((T, "symbol"))
                except Exception:
                    pass

        if not calls:
            return out

        # go through the same known-good pipeline as balanceOf
        res = self._aggregate3(calls, allow_failure=True)

        for (addr, field), (ok, data) in zip(order, res):
            out.setdefault(addr, {})
            out[addr][field] = self._decode_string_like(data) if (ok and data) else None

        return out

    def multicall_fetch(
        self,
        wallets: List[str],
        tokens: List[str],
        *,
        spender: Optional[str] = None,
        ens_names: Optional[List[str]] = None,
        use_multicall: bool = True,
        want_balance: bool = True,
        want_decimals: bool = True,
        with_name: bool = True,
        with_symbol : bool = True,
        want_ens: bool = False,          # reverse + forward (if ens_names provided)
        want_allowance: bool = False
    ) -> Dict[str, Dict]:
        """
        Returns a dict with keys:
        {
          "balances": {(wallet, token) -> int},
          "decimals": {token -> int},
          "allowance": {(owner, token) -> int},   # spender fixed
          "ens_reverse": {wallet -> name|None},
          "ens_forward": {ens_name -> address|None}
        }
        """
        out = {
            "balances": {},
            "decimals": {},
            "allowance": {},
            "ens_reverse": {},
            "ens_forward": {},
            "wallets_resolved": []
        }

        ens_forward_map: Dict[str, Optional[str]] = {}
        wallets = list(wallets or [])

        if ens_names:
            try:
                ens_forward_map = self.batch_ens_forward(ens_names, use_multicall=use_multicall) or {}
            except Exception as exc:
                ens_forward_map = {}
                if self.console:
                    self.console.log(f"[yellow]ENS forward lookup failed: {exc}[/yellow]")

        final_wallets: List[str] = []
        seen_wallets: Set[str] = set()

        def _push_wallet(addr: Optional[str]) -> None:
            if not addr:
                return
            try:
                checksum = self.w3.to_checksum_address(addr)
            except Exception:
                checksum = addr
            key = checksum.lower() if isinstance(checksum, str) else str(checksum)
            if key in seen_wallets:
                return
            seen_wallets.add(key)
            final_wallets.append(checksum)

        for addr in wallets:
            _push_wallet(addr)

        if ens_forward_map:
            if ens_names:
                for name in ens_names:
                    _push_wallet(ens_forward_map.get(name))
            for addr in ens_forward_map.values():
                _push_wallet(addr)

        wallets = final_wallets

        if want_ens:
            out["ens_reverse"] = self.batch_ens_reverse(wallets, use_multicall=use_multicall) if wallets else {}
        out["ens_forward"] = ens_forward_map

        # --- DECIMALS ---
        # --- DECIMALS ---
        if want_decimals and tokens:
            if use_multicall:
                calls = []
                for t in tokens:
                    try:
                        calls.append( (t, self._enc(t, "decimals")) )
                    except Exception:
                        pass
                res = self._aggregate3(calls, allow_failure=True)
                for (t, _), (ok, data) in zip(calls, res):
                    if ok and data:
                        try:
                            val = self.w3.codec.decode(['uint8'], data)[0]
                            out["decimals"][self.w3.to_checksum_address(t)] = int(val)
                        except Exception:
                            pass
            else:
                for t in tokens:
                    try:
                        c = self.w3.eth.contract(address=self.w3.to_checksum_address(t), abi=self.erc20_abi)
                        d = c.functions.decimals().call()
                        out["decimals"][self.w3.to_checksum_address(t)] = int(d)
                    except Exception:
                        pass
        
        if (with_name or with_symbol) and tokens:
            out.setdefault("names", {})
            out.setdefault("symbols", {})
            labels = self._multicall_fetch_labels(tokens, with_name, with_symbol)
            for addr, kv in labels.items():
                if with_name and kv.get('name') is not None:
                    out["names"][addr] = kv['name']
                if with_symbol and kv.get('symbol') is not None:
                    out["symbols"][addr] = kv['symbol']

        # --- BALANCES ---
        if want_balance and tokens and wallets:
            native_aliases = {NATIVE_SENTINEL}
            cfg_native = getattr(self.cfg, "NATIVE_TOKEN", None)
            if cfg_native:
                native_aliases.add(str(cfg_native).lower())

            chain_name_raw = getattr(self.cfg, "CHAIN_NAME", "")
            chain_name = str(chain_name_raw).strip().lower() if chain_name_raw else ""

            symbol_from_cfg = (
                getattr(self.cfg, "NATIVE_SYMBOL", None)
                or getattr(self.cfg, "NATIVE_TOKEN_SYMBOL", None)
            )
            if symbol_from_cfg:
                native_symbol = str(symbol_from_cfg).strip() or None
            else:
                default_symbols = {
                    "polygon": "MATIC",
                    "optimism": "ETH",
                    "op": "ETH",
                    "ethereum": "ETH",
                    "eth": "ETH",
                    "base": "ETH",
                    "arbitrum": "ETH",
                    "linea": "ETH",
                }
                native_symbol = default_symbols.get(chain_name)
            if not native_symbol:
                native_symbol = (chain_name.upper() if chain_name else "NATIVE")

            name_from_cfg = (
                getattr(self.cfg, "NATIVE_NAME", None)
                or getattr(self.cfg, "NATIVE_TOKEN_NAME", None)
            )
            if name_from_cfg:
                native_name = str(name_from_cfg).strip() or None
            else:
                default_names = {
                    "polygon": "Matic",
                    "optimism": "Ether",
                    "op": "Ether",
                    "ethereum": "Ether",
                    "eth": "Ether",
                    "base": "Ether",
                    "arbitrum": "Ether",
                    "linea": "Ether",
                }
                native_name = default_names.get(chain_name)
            if not native_name:
                native_name = f"{native_symbol} (native)"

            native_checksums = {}

            if use_multicall:
                calls = []
                index = []
                for w in wallets:
                    try:
                        W = self.w3.to_checksum_address(w)
                    except Exception:
                        continue
                    for t in tokens:
                        token_str = str(t).strip()
                        if not token_str:
                            continue
                        token_lower = token_str.lower()
                        try:
                            T = self.w3.to_checksum_address(token_str)
                        except Exception:
                            continue
                        if token_lower in native_aliases:
                            try:
                                val = int(self.w3.eth.get_balance(W))
                            except Exception:
                                val = 0
                            out["balances"][(W, T)] = val
                            native_checksums[T] = (native_name, native_symbol)
                            continue
                        try:
                            calls.append((T, self._enc(T, "balanceOf", [W])))
                            index.append((W, T))
                        except Exception:
                            pass
                # chunk to be safe
                chunk = 500
                for i in range(0, len(calls), chunk):
                    part = calls[i:i+chunk]
                    res = self._aggregate3(part, allow_failure=True)
                    for (w,t), (ok, data) in zip(index[i:i+chunk], res):
                        val = 0
                        if ok and data:
                            try:
                                val = int.from_bytes(data[-32:], 'big')
                            except Exception:
                                val = 0
                        out["balances"][(w,t)] = val
            else:
                for w in wallets:
                    try:
                        W = self.w3.to_checksum_address(w)
                    except Exception:
                        continue
                    for t in tokens:
                        token_str = str(t).strip()
                        if not token_str:
                            continue
                        token_lower = token_str.lower()
                        try:
                            T = self.w3.to_checksum_address(token_str)
                        except Exception:
                            continue
                        if token_lower in native_aliases:
                            try:
                                val = int(self.w3.eth.get_balance(W))
                            except Exception:
                                val = 0
                            out["balances"][(W, T)] = val
                            native_checksums[T] = (native_name, native_symbol)
                            continue
                        try:
                            c = self.w3.eth.contract(address=T, abi=self.erc20_abi)
                            val = c.functions.balanceOf(W).call()
                        except Exception:
                            val = 0
                        out["balances"][(W,T)] = int(val)

            if native_checksums:
                if with_name:
                    out.setdefault("names", {})
                if with_symbol:
                    out.setdefault("symbols", {})
                for checksum, (n_name, n_symbol) in native_checksums.items():
                    if with_name and n_name and checksum not in out["names"]:
                        out["names"][checksum] = n_name
                    if with_symbol and n_symbol and checksum not in out["symbols"]:
                        out["symbols"][checksum] = n_symbol

        # --- ALLOWANCES ---
        if want_allowance and spender and wallets and tokens:
            S = self.w3.to_checksum_address(spender)
            if use_multicall:
                calls = []
                index = []
                for w in wallets:
                    W = self.w3.to_checksum_address(w)
                    for t in tokens:
                        T = self.w3.to_checksum_address(t)
                        try:
                            calls.append( (T, self._enc(T, "allowance", [W, S])) )
                            index.append( (W, T) )
                        except Exception:
                            pass
                chunk = 500
                for i in range(0, len(calls), chunk):
                    part = calls[i:i+chunk]
                    res = self._aggregate3(part, allow_failure=True)
                    for (w,t), (ok, data) in zip(index[i:i+chunk], res):
                        val = 0
                        if ok and data:
                            try:
                                val = int.from_bytes(data[-32:], 'big')
                            except Exception:
                                val = 0
                        out["allowance"][(w,t)] = val
            else:
                for w in wallets:
                    W = self.w3.to_checksum_address(w)
                    for t in tokens:
                        T = self.w3.to_checksum_address(t)
                        try:
                            c = self.w3.eth.contract(address=T, abi=self.erc20_abi)
                            v = c.functions.allowance(W, S).call()
                        except Exception:
                            v = 0
                        out["allowance"][(W,T)] = int(v)

        out["wallets_resolved"] = list(wallets)
        return out

class FileHelper:
    """
    Basic file helpers to ensure placeholders and load simple lists.
    """

    TEMPLATES = {
        'wallets': "# Enter your private keys here (one per line). Supports 0x-prefixed or raw hex.\n",
        'receivers': "# Enter receiver addresses or ENS names (one per line). Example:\n# vitalik.eth\n# 0x1234...abcd\n",
        'contracts': "# Enter token contract addresses and symbols (e.g., 0x... SYMBOL). Use 0xEeeee... for native sentinel.\n",
    }

    @staticmethod
    def ensure_placeholder(file_path: str, kind: str) -> None:
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(FileHelper.TEMPLATES.get(kind, ''))

    @staticmethod
    def _strip_comment(line: str) -> str:
        s = line.strip()
        if not s or s.startswith('#'):
            return ''
        # inline comment support
        if '#' in s:
            s = s.split('#', 1)[0].strip()
        return s

    @staticmethod
    def load_lines(file_path: str) -> List[str]:
        out: List[str] = []
        if not os.path.exists(file_path):
            return out
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                s = FileHelper._strip_comment(line)
                if s:
                    out.append(s)
        return out
