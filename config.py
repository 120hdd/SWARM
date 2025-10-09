# config.py
import eth_abi
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_PATH = Path(__file__).resolve().parent / "resources"  # removed trailing slash
KYBER_ROUTER = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"
KYBERSWAP_API_BASE = "https://aggregator-api.kyberswap.com/"
KYBERSWAP_API_HEADERS = {
        "Content-Type": "application/json",
        "x-client-id": "barsavaClientId"  # Replace with your actual Client ID
    }

ALCHEMY_API_KEY = os.getenv('ALCHEMY_API_KEY')
INFURA_API_KEY = os.getenv('INFURA_API_KEY')

ENS_REGISTRY_ADDRESS = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
MULTICALL3_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11"

MULTICALL3_ABI = """
[
  {
    "inputs": [
      {
        "components": [
          {"internalType": "address", "name": "target", "type": "address"},
          {"internalType": "bool", "name": "allowFailure", "type": "bool"},
          {"internalType": "bytes", "name": "callData", "type": "bytes"}
        ],
        "internalType": "struct Multicall3.Call3[]",
        "name": "calls",
        "type": "tuple[]"
      }
    ],
    "name": "aggregate3",
    "outputs": [
      {
        "components": [
          {"internalType": "bool", "name": "success", "type": "bool"},
          {"internalType": "bytes", "name": "returnData", "type": "bytes"}
        ],
        "internalType": "struct Multicall3.Result[]",
        "name": "returnData",
        "type": "tuple[]"
      }
    ],
    "stateMutability": "payable",
    "type": "function"
  }
]
"""

QUOTER_ABI = '''[
    {
        "inputs": [
            {"internalType": "address", "name": "tokenIn", "type": "address"},
            {"internalType": "address", "name": "tokenOut", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
        ],
        "name": "quoteExactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]'''

MINIMAL_ABI_PERMIT = '''[
        {
            "inputs": [
                {"internalType": "address", "name": "owner", "type": "address"},
                {"internalType": "address", "name": "spender", "type": "address"},
                {"internalType": "uint256", "name": "value", "type": "uint256"},
                {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                {"internalType": "uint8", "name": "v", "type": "uint8"},
                {"internalType": "bytes32", "name": "r", "type": "bytes32"},
                {"internalType": "bytes32", "name": "s", "type": "bytes32"}
            ],
            "name": "permit",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
            "name": "nonces",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "nonces",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]'''
    
ERC20_PERMIT_ABI = '''[
        {
            "inputs": [
                {"internalType": "address", "name": "owner", "type": "address"},
                {"internalType": "address", "name": "spender", "type": "address"},
                {"internalType": "uint256", "name": "value", "type": "uint256"},
                {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                {"internalType": "uint8", "name": "v", "type": "uint8"},
                {"internalType": "bytes32", "name": "r", "type": "bytes32"},
                {"internalType": "bytes32", "name": "s", "type": "bytes32"}
            ],
            "name": "permit",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "name",
            "outputs": [{"internalType": "string", "name": "", "type": "string"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "version",
            "outputs": [{"internalType": "string", "name": "", "type": "string"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "nonces",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
            "name": "nonces",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [],
            "name": "DOMAIN_SEPARATOR",
            "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]'''
    
TOKEN_ABI = '''[
  {
    "type": "function",
    "name": "transfer",
    "stateMutability": "nonpayable",
    "inputs": [
      {"name": "_to", "type": "address"},
      {"name": "_value", "type": "uint256"}
    ],
    "outputs": [{"name": "", "type": "bool"}]
  },
  {
    "type":"function",
    "name":"symbol",
    "stateMutability":"view",
    "inputs":[],
    "outputs":[{"name":"","type":"string"}]
    },
  {
    "type":"function",
    "name":"name",
    "stateMutability":"view",
    "inputs":[],
    "outputs":[{"name":"","type":"string"}]
  },
  {
    "type": "function",
    "name": "approve",
    "stateMutability": "nonpayable",
    "inputs": [
      {"name": "_spender", "type": "address"},
      {"name": "_value", "type": "uint256"}
    ],
    "outputs": [{"name": "", "type": "bool"}]
  },
  {
    "type": "function",
    "name": "allowance",
    "stateMutability": "view",
    "inputs": [
      {"name": "_owner", "type": "address"},
      {"name": "_spender", "type": "address"}
    ],
    "outputs": [{"name": "remaining", "type": "uint256"}]
  },
  {
    "type": "function",
    "name": "balanceOf",
    "stateMutability": "view",
    "inputs": [{"name": "_owner", "type": "address"}],
    "outputs": [{"name": "balance", "type": "uint256"}]
  },
  {
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}]
  },
  {
    "type": "event",
    "name": "Transfer",
    "inputs": [
      {"indexed": true, "name": "from", "type": "address"},
      {"indexed": true, "name": "to", "type": "address"},
      {"indexed": false, "name": "value", "type": "uint256"}
    ],
    "anonymous": false
  },
  {
    "type": "event",
    "name": "Approval",
    "inputs": [
      {"indexed": true, "name": "owner", "type": "address"},
      {"indexed": true, "name": "spender", "type": "address"},
      {"indexed": false, "name": "value", "type": "uint256"}
    ],
    "anonymous": false
  }
]'''


ENS_REGISTRY_ABI = """
[
  {
    "type":"function","stateMutability":"view","name":"resolver",
    "inputs":[{"name":"node","type":"bytes32"}],
    "outputs":[{"name":"resolver","type":"address"}]
  }
]
"""
ENS_PUBLIC_RESOLVER_ABI = """
[
  {
    "type":"function","stateMutability":"view","name":"name",
    "inputs":[{"name":"node","type":"bytes32"}],
    "outputs":[{"name":"","type":"string"}]
  },
  {
    "type":"function","stateMutability":"view","name":"addr",
    "inputs":[{"name":"node","type":"bytes32"}],
    "outputs":[{"name":"ret","type":"address"}]
  }
]
"""

class POLYGON :
# RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    
    CHAIN_ID = 137
    CHAIN_NAME = "polygon"
    NATIVE_TOKEN = "0x0000000000000000000000000000000000001010"

    # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, CHAIN_NAME, "tokens.txt")
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")
    
    

    # Token-specific settings (optional)
    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS

    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY
        
    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"
    # Uniswap V3 Quoter Address

class OP :
    # RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

    CHAIN_ID = "10"
    CHAIN_NAME = "optimism"
    NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

    # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")
    
    # Paths to your wallet and address files
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, "OP", "tokens.txt") #token_contracts
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")



    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS

    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY


    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"

class Base :
    # RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    
    CHAIN_ID = "8453"
    CHAIN_NAME = "base"
    NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

    # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")

    # Paths to your wallet and address files
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, "BASE", "tokens.txt") #token_contracts
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")

    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS

    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY
    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"

class ARB :
    # RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    
    CHAIN_ID = "59144"
    CHAIN_NAME = "arbitrum"
    NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

    # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")

    # Paths to your wallet and address files
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, "ARB", "tokens.txt") #token_contracts
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")
  


    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS
    
    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY
    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"
    
class Linea :
    # RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://linea-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    
    CHAIN_ID = "59144"
    CHAIN_NAME = "linea"
    NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

     # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")


    # Paths to your wallet and address files
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, "LINEA", "tokens.txt") #token_contracts
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")



    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS

    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY
    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"

class ETHER :
    # RPC URL for connecting to Polygon mainnet
    ALCHEMY_API_KEY = ALCHEMY_API_KEY
    ALCHEMY_RPC_URL = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

    CHAIN_ID = "1"
    CHAIN_NAME = "ethereum"
    NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

    # Paths to your wallet and address files
    KYBERSWAP_API_ROUTE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/routes")
    KYBERSWAP_API_BUILD = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/build")
    KYBERSWAP_API_ENCODE = os.path.join(KYBERSWAP_API_BASE + CHAIN_NAME  + "/api/v1/route/encode")
    
    # Paths to your wallet and address files
    WALLET_FILE = os.path.join(BASE_PATH,"wallet.txt") #private keys
    CONTRACTS_FILE = os.path.join(BASE_PATH, "ETHER", "tokens.txt") #token_contracts
    RECEIVERS_FILE = os.path.join(BASE_PATH, "receiver_wallet.txt")
 


    MINIMAL_ABI_PERMIT = MINIMAL_ABI_PERMIT
    ERC20_PERMIT_ABI = ERC20_PERMIT_ABI
    TOKEN_ABI = TOKEN_ABI
    MULTICALL3_ADDRESS = MULTICALL3_ADDRESS     # or override with chain-specific address
    MULTICALL3_ABI = MULTICALL3_ABI
    ENS_REGISTRY_ABI = ENS_REGISTRY_ABI
    ENS_PUBLIC_RESOLVER_ABI = ENS_PUBLIC_RESOLVER_ABI
    ENS_REGISTRY_ADDRESS = ENS_REGISTRY_ADDRESS
    # Infura Gas API Key for gas price estimation
    INFURA_API_KEY = INFURA_API_KEY
    INFURA_GAS_API_URL = f"https://gas.api.infura.io/v3/{INFURA_API_KEY}/networks/{CHAIN_ID}/suggestedGasFees"
    
MODULE_PATH = Path(__file__).resolve().parent / "modules"
