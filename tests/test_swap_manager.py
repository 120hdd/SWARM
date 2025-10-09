import sys
import types
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

# Provide minimal stubs for external packages if they are missing
if 'web3' not in sys.modules:
    web3 = types.ModuleType('web3')
    class DummyWeb3:
        def __init__(self, provider=None):
            self.eth = SimpleNamespace()
        @staticmethod
        def to_checksum_address(addr):
            return addr
    DummyWeb3.HTTPProvider = lambda url: None
    web3.Web3 = DummyWeb3
    exceptions = types.ModuleType('web3.exceptions')
    class ABIError(Exception):
        pass
    exceptions.ABIFunctionNotFound = ABIError
    exceptions.ContractLogicError = ABIError
    web3.exceptions = exceptions
    sys.modules['web3'] = web3
    sys.modules['web3.exceptions'] = exceptions

if 'eth_account' not in sys.modules:
    eth_account = types.ModuleType('eth_account')
    class DummyAccount:
        @staticmethod
        def from_key(key):
            return SimpleNamespace(address='0x'+key[-40:])
    eth_account.Account = DummyAccount
    messages = types.ModuleType('eth_account.messages')
    messages.encode_structured_data = lambda *a, **k: None
    sys.modules['eth_account'] = eth_account
    sys.modules['eth_account.messages'] = messages

if 'customtkinter' not in sys.modules:
    ct = types.ModuleType('customtkinter')
    class Widget: pass
    ct.CTk = ct.CTkTextbox = ct.CTkButton = ct.CTkLabel = ct.CTkFrame = Widget
    sys.modules['customtkinter'] = ct

if 'questionary' not in sys.modules:
    q = types.ModuleType('questionary')
    q.select = lambda *a, **k: SimpleNamespace(ask=lambda: "")
    sys.modules['questionary'] = q

if 'rich.console' not in sys.modules:
    rc = types.ModuleType('rich.console')
    class DummyConsole:
        def log(self, *a, **k):
            pass
    rc.Console = DummyConsole
    sys.modules['rich.console'] = rc

if 'rich.logging' not in sys.modules:
    rl = types.ModuleType('rich.logging')
    class RichHandler:
        def __init__(self, *a, **k):
            pass
    rl.RichHandler = RichHandler
    sys.modules['rich.logging'] = rl

if 'config' not in sys.modules:
    cfg_stub = types.ModuleType('config')
    cfg_stub.KYBERSWAP_API_HEADERS = {}
    sys.modules['config'] = cfg_stub

for mod in ['rich', 'requests']:
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.kyberSwap import SwapManager

class DummyChainConfig:
    def __init__(self, wallet_file, tokens_file):
        self.ALCHEMY_RPC_URL = 'http://localhost'
        self.WALLET_FILE = wallet_file
        self.TOKENS_KYBER_FILE = tokens_file
        self.INFURA_GAS_API_URL = 'http://localhost'
        self.NATIVE_TOKEN = '0x0000000000000000000000000000000000000000'
        self.CHAIN_ID = 1
        self.CHAIN_NAME = 'test'
        self.MINIMAL_ABI_PERMIT = '[]'
        self.KYBERSWAP_API_ROUTE = 'http://api'
        self.KYBERSWAP_API_BUILD = 'http://build'
        self.KYBERSWAP_API_ENCODE = 'http://encode'

def create_manager(tmpdir):
    wallet_file = os.path.join(tmpdir, 'wallets.txt')
    tokens_file = os.path.join(tmpdir, 'tokens.txt')
    open(tokens_file, 'w').close()
    cfg = DummyChainConfig(wallet_file, tokens_file)
    return SwapManager(cfg)

def test_load_wallets_from_file(tmp_path):
    wallet_path = tmp_path / 'wallets.txt'
    valid_key = '1' * 64
    valid_prefixed = '0x' + '2' * 64
    invalid = 'xyz'
    wallet_path.write_text('\n'.join([valid_key, valid_prefixed, invalid]))

    manager = create_manager(str(tmp_path))
    manager.wallet_file = str(wallet_path)
    manager.load_wallets_from_file()

    assert valid_key in manager.wallet_private_keys
    assert '2' * 64 in manager.wallet_private_keys
    assert len(manager.wallet_private_keys) == 2
    addr1 = manager.wallet_addresses[0]
    addr2 = manager.wallet_addresses[1]
    assert addr1.startswith('0x') and addr2.startswith('0x')

def _mock_contract(support_permit=True, support_nonces=True, support_domain=True):
    contract = MagicMock()
    if support_permit:
        contract.get_function_by_name.return_value = MagicMock()
    else:
        contract.get_function_by_name.side_effect = sys.modules['web3'].exceptions.ABIFunctionNotFound('no permit')

    def nonce_addr(owner):
        return MagicMock(call=MagicMock(return_value=1))
    if support_nonces:
        contract.get_function_by_signature.side_effect = lambda sig: nonce_addr if sig == 'nonces(address)' else MagicMock()
    else:
        contract.get_function_by_signature.side_effect = sys.modules['web3'].exceptions.ABIFunctionNotFound('no nonces')

    if support_domain:
        domain = MagicMock()
        domain.call.return_value = 1
        contract.functions.DOMAIN_SEPARATOR.return_value = domain
    else:
        contract.functions.DOMAIN_SEPARATOR.side_effect = sys.modules['web3'].exceptions.ABIFunctionNotFound('no domain')
    return contract

def test_check_eip2612_support_success(tmp_path):
    manager = create_manager(str(tmp_path))
    contract = _mock_contract()
    manager.w3.eth.contract = MagicMock(return_value=contract)
    manager.w3.to_checksum_address = lambda x: x
    assert manager.check_eip2612_support('token', 'owner')

def test_check_eip2612_support_failure(tmp_path):
    manager = create_manager(str(tmp_path))
    contract = _mock_contract(support_permit=False)
    manager.w3.eth.contract = MagicMock(return_value=contract)
    manager.w3.to_checksum_address = lambda x: x
    assert not manager.check_eip2612_support('token', 'owner')
