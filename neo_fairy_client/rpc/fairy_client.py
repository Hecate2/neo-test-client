from typing import List, Union, Dict, Any
import base64
import json
import os
import traceback
import requests
import urllib3

from neo_fairy_client.utils import Hash160Str, Hash256Str, PublicKeyStr, Signer
from neo_fairy_client.utils import Interpreter, to_list
from neo3.core.types import UInt160, UInt256
from neo3.contracts import NeoToken, GasToken
from neo3vm import VMState

neo, gas = NeoToken(), GasToken()

RequestExceptions = (
    requests.RequestException,
    requests.ConnectionError,
    requests.HTTPError,
    requests.Timeout,
)
default_request_timeout = None  # 20
default_requests_session = requests.Session()


class RpcBreakpoint:
    def __init__(self, state: Union[str, VMState], break_reason: str, scripthash: Union[str, Hash160Str], contract_name: str,
                 instruction_pointer: int, source_filename: str = None, source_line_num: int = None, source_content=None,
                 exception: str = None, result_stack: Any = None):
        if type(state) is VMState:
            self.state = state
        else:
            self.state: VMState = {'BREAK': VMState.BREAK, 'FAULT': VMState.FAULT, 'HALT': VMState.HALT, 'NONE': VMState.NONE}[state.upper()]
        self.break_reason = break_reason
        if type(scripthash) is str:
            scripthash = Hash160Str(scripthash)
        self.scripthash = scripthash
        self.contract_name = contract_name
        self.instruction_pointer = instruction_pointer
        self.source_filename = source_filename
        self.source_line_num = source_line_num
        self.source_content = source_content
        self.exception = exception
        self.result_stack = result_stack
        
    @classmethod
    def from_raw_result(cls, result: Dict):
        result = result['result']
        return cls(result['state'], result['breakreason'], result['scripthash'], result['contractname'],
                   result['instructionpointer'], source_filename=result['sourcefilename'], source_line_num=result['sourcelinenum'], source_content=result['sourcecontent'])
    
    def __repr__(self):
        if self.state == VMState.HALT:
            return f'''RpcBreakpoint {self.state} {self.result_stack}'''
        if self.source_filename and self.source_line_num:
            return f'''RpcBreakpoint {self.state} {self.source_filename} line {self.source_line_num} instructionPointer {self.instruction_pointer}: {self.source_content}'''
        else:
            return f'''RpcBreakpoint {self.state} {self.contract_name} instructionPointer {self.instruction_pointer};'''


class FairyClient:
    def __init__(self, target_url: str = 'http://localhost:16868',
                 wallet_address: str = None, wallet_path: str = None, wallet_password: str = None,
                 contract_scripthash: Hash160Str = None, signers: Union[Signer, List[Signer], None] = None,
                 fairy_session: str = None, function_default_relay=True, script_default_relay=False,
                 auto_reset_fairy_session=True,
                 with_print=True, verbose_return=False, verify_SSL: bool = True,
                 requests_session: requests.Session = default_requests_session,
                 requests_timeout: Union[int, None] = default_request_timeout,
                 auto_set_neo_balance=100_0000_0000, auto_set_gas_balance=100_0000_0000):
        """
        Fairy RPC client to interact with both normal Neo3 and Fairy RPC backend.
        Fairy RPC backend helps you test and debug transactions with sessions, which contain snapshots.
        Use fairy_session strings to name your snapshots.
        :param target_url: url to the rpc server affliated to neo-cli
        :param wallet_address: address of your wallet (starting with 'N'); "NVbGwMfRQVudTjWAUJwj4K68yyfXjmgbPp"
        :param wallet_path: 'wallets/dev.json'
        :param wallet_password: '12345678'
        :param signers: by default, which account(s) will sign the transactions with which scope
            https://docs.neo.org/docs/en-us/basic/concept/transaction.html#signature-scope
        :param fairy_session: Any string designated by you to name your session which contains snapshot.
            If None, will use normal RPC without session string. No snapshot will be used or recorded
        :param function_default_relay: if True, will write your transaction to chain or fairy snapshot
        :param script_default_relay: if True, will write your transaction to chain or fairy snapshot
        :param with_print: print results for each RPC call
        :param verbose_return: return (parsed_result, raw_result, post_data) if True. return parsed result if False.
            This is to avoid reading previous_result for concurrency safety.
            For concurrency, set verbose_return=True
        :param requests_session: requests.Session
        :param requests_timeout: raise Exceptions if request not completed in that many seconds. None for no limit
        """
        self.target_url: str = target_url
        self.contract_scripthash: Union[Hash160Str, None] = contract_scripthash
        self.requests_session: requests.Session = requests_session
        if wallet_address:
            self.wallet_address: Union[str, None] = wallet_address
            wallet_scripthash = Hash160Str.from_address(wallet_address)
            self.wallet_scripthash: Union[Hash160Str, None] = wallet_scripthash
            self.signers: List[Signer] = to_list(signers) or [Signer(wallet_scripthash)]
        else:
            self.wallet_address = None
            self.wallet_scripthash = None
            self.signers: List[Signer] = []
            print('WARNING: No wallet address specified when building the fairy client!')
        self.wallet_path: Union[str, None] = wallet_path
        self.wallet_password: Union[str, None] = wallet_password
        self.previous_post_data = None
        self.with_print: bool = with_print
        self.previous_raw_result: Union[dict, None] = None
        self.previous_result: Any = None
        self.previous_txBase64Str: Union[str, None] = None
        self.previous_gas_consumed: Union[int, None] = None
        self.previous_network_fee: Union[int, None] = None
        self.verbose_return: bool = verbose_return
        self.function_default_relay: bool = function_default_relay
        self.script_default_relay: bool = script_default_relay
        self.fairy_session: Union[str, None] = fairy_session
        self.verify_SSL: bool = verify_SSL
        self.requests_timeout: Union[int, None] = requests_timeout
        if verify_SSL is False:
            print('WARNING: Will ignore SSL certificate errors!')
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        try:
            if fairy_session and auto_reset_fairy_session:
                self.delete_snapshots(fairy_session)
                self.new_snapshots_from_current_system(fairy_session)
            self.open_fairy_wallet()
            if auto_set_neo_balance and self.wallet_scripthash:
                self.set_neo_balance(auto_set_neo_balance)
            if auto_set_gas_balance and self.wallet_scripthash:
                self.set_gas_balance(auto_set_gas_balance)
        except:
            traceback.print_exc()
            print(f"WARNING: Failed at some fairy operations at {target_url} with wallet `{wallet_path}`!")

    def assign_wallet_address(self, wallet_address: str, signers: Union[Signer, List[Signer]] = None):
        """
        :param wallet_address: address of your wallet (starting with 'N'); "NVbGwMfRQVudTjWAUJwj4K68yyfXjmgbPp"
        :param signers: Signer(wallet_scripthash or wallet_address). By Signer you can assign WitnessScope
        """
        self.wallet_address: str = wallet_address
        wallet_scripthash = Hash160Str.from_address(wallet_address)
        self.wallet_scripthash: Hash160Str = wallet_scripthash
        self.signers: List[Signer] = to_list(signers) or [Signer(wallet_scripthash)]

    @staticmethod
    def request_body_builder(method, parameters: List):
        return json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": parameters,
            "id": 1,
        }, separators=(',', ':'))
    
    @staticmethod
    def bytes_to_UInt160(bytestring: bytes):
        return Hash160Str.from_UInt160(UInt160.deserialize_from_bytes(bytestring))
    
    @staticmethod
    def base64_struct_to_bytestrs(base64_struct: dict) -> List[bytes]:
        processed_struct = []
        if type(base64_struct) is dict and 'type' in base64_struct and base64_struct['type'] == 'Struct':
            values = base64_struct['value']
            for value in values:
                if value['type'] == 'ByteString':
                    processed_struct.append(base64.b64decode(value['value']))
        return processed_struct
    
    def meta_rpc_method_with_raw_result(self, method: str, parameters: List) -> Any:
        post_data = self.request_body_builder(method, parameters)
        self.previous_post_data = post_data
        result = json.loads(self.requests_session.post(self.target_url, post_data, timeout=self.requests_timeout).text)
        if 'error' in result:
            raise ValueError(result['error'])
        self.previous_raw_result = result
        self.previous_result = None
        return result

    def meta_rpc_method(self, method: str, parameters: List, relay: bool = None, do_not_raise_on_result=False) -> Any:
        post_data = self.request_body_builder(method, parameters)
        self.previous_post_data = post_data
        result = json.loads(self.requests_session.post(self.target_url, post_data, timeout=self.requests_timeout, verify=self.verify_SSL).text)
        if 'error' in result:
            raise ValueError(result['error']['message'])
        if type(result['result']) is dict:
            result_result: dict = result['result']
            self.previous_gas_consumed = int(result_result.get('gasconsumed', 0)) or None
            self.previous_network_fee = int(result_result.get('networkfee', 0)) or None
            if 'exception' in result_result and result_result['exception'] is not None:
                if do_not_raise_on_result:
                    return result_result['exception']
                else:
                    print(post_data)
                    print(result)
                    if 'traceback' in result_result and result_result['traceback']:
                        raise ValueError(result_result['traceback'])
                    raise ValueError(result_result['exception'])
            if relay or (relay is None and self.function_default_relay):
                if method in {'invokefunction', 'invokescript'} and 'tx' not in result_result:
                    raise ValueError('No `tx` in response. '
                                     'Did you call `client.openwallet()` before `invokefunction`?')
                if 'tx' in result_result:
                    tx = result_result['tx']
                    self.previous_txBase64Str = tx
                    self.sendrawtransaction(tx)
                # else:
                #     self.previous_txBase64Str = None
        self.previous_raw_result = result
        self.previous_result = self.parse_stack_from_raw_result(result)
        if self.verbose_return:
            return self.previous_result, result, post_data
        return self.previous_result
    
    def print_previous_result(self):
        print(self.previous_result)
    
    def sendrawtransaction(self, transaction: str):
        """
        :param transaction: result['tx']. e.g. "ALmNfAb4lqIAAA...="
        """
        return self.meta_rpc_method("sendrawtransaction", [transaction], relay=False)
    
    def getrawtransaction(self, transaction_hash: Hash256Str, verbose: bool = False):
        return self.meta_rpc_method("getrawtransaction", [str(transaction_hash), verbose], relay=False)
    
    def calculatenetworkfee(self, txBase64Str):
        return self.meta_rpc_method("calculatenetworkfee", [txBase64Str], relay=False)
    
    @property
    def totalfee(self):
        return self.previous_network_fee + self.previous_gas_consumed

    @property
    def previous_total_fee(self):
        return self.totalfee

    @property
    def previous_system_fee(self):
        return self.previous_gas_consumed
    
    def openwallet(self, path: str = None, password: str = None) -> dict:
        """
        WARNING: usually you should use this method along with __init__.
        Use another TestClient object to open another wallet
        """
        if not path:
            path = self.wallet_path
        if not password:
            password = self.wallet_password
        if self.verbose_return:
            open_wallet_result, _, _ = self.meta_rpc_method("openwallet", [path, password])
        else:
            open_wallet_result = self.meta_rpc_method("openwallet", [path, password])
        if not open_wallet_result:
            raise ValueError(f'Failed to open wallet {path} with given password.')
        return open_wallet_result

    def closewallet(self) -> dict:
        if self.verbose_return:
            close_wallet_result, _, _ = self.meta_rpc_method("closewallet", [])
        else:
            close_wallet_result = self.meta_rpc_method("closewallet", [])
        if not close_wallet_result:
            raise ValueError(f'Failed to close wallet.')
        return close_wallet_result

    @staticmethod
    def parse_stack_from_raw_result(raw_result: dict):
        def parse_single_item(item: Union[Dict, List]):
            if 'iterator' in item:
                item = item['iterator']
                if item:
                    if type(item[0]['value']) is not list:
                        return [parse_single_item(i) for i in item]
                    else:
                        return {parse_single_item(i['value'][0]): parse_single_item(i['value'][1]) for i in item}
                else:
                    assert item == []
                    return item
            _type = item['type']
            if _type == 'Any' and 'value' not in item:
                return None
            else:
                value = item['value']
            if _type == 'Integer':
                return int(value)
            elif _type == 'Boolean':
                return value
            elif _type == 'ByteString' or _type == 'Buffer':
                byte_value = base64.b64decode(value)
                try:
                    return byte_value.decode()
                except UnicodeDecodeError:
                    try:
                        len_bytes = len(byte_value)
                        if len_bytes == 20:
                            return Hash160Str.from_UInt160(UInt160(byte_value))
                        if len_bytes == 32:
                            return Hash256Str.from_UInt256(UInt256(byte_value))
                    except Exception:
                        pass
                    # may be an N3 address starting with 'N'
                    # TODO: decode to N3 address
                    return byte_value
            elif _type == 'Array':
                return [parse_single_item(i) for i in value]
            elif _type == 'Struct':
                return tuple([parse_single_item(i) for i in value])
            elif _type == 'Map':
                return {parse_single_item(i['key']): parse_single_item(i['value']) for i in value}
            elif _type == 'Pointer':
                return int(value)
            else:
                raise ValueError(f'Unknown type {_type}')
        
        result: Dict = raw_result['result']
        if type(result) is not dict or 'stack' not in result:
            return result
        if not result['stack']:
            return result['stack']
        stack: List = result['stack']
        if len(stack) > 1:  # typically happens when we invoke a script calling a series of methods
            return [parse_single_item(item) for item in stack]
        else:  # if the stack has only 1 item, we simply return the item without a wrapping list
            result: List = stack[0]
            return parse_single_item(result)
    
    @classmethod
    def parse_params(cls, param: Union[str, int, dict, Hash160Str, UInt160, UInt256, bytes]) -> Dict[str, str]:
        type_param = type(param)
        if type_param is UInt160:
            return {
                'type': 'Hash160',
                'value': str(Hash160Str.from_UInt160(param)),
            }
        elif type_param is Hash160Str:
            return {
                'type': 'Hash160',
                'value': str(param),
            }
        elif type_param is UInt256:
            return {
                'type': 'Hash256',
                'value': str(Hash256Str.from_UInt256(param)),
            }
        elif type_param is Hash256Str:
            return {
                'type': 'Hash256',
                'value': str(param),
            }
        elif type_param is PublicKeyStr:
            return {
                'type': 'PublicKey',
                'value': str(param),
            }
        elif type_param is bool:
            return {
                'type': 'Boolean',
                'value': param,
            }
        elif type_param is int:
            return {
                'type': 'Integer',
                'value': str(param),
            }
        elif type_param is str:
            return {
                'type': 'String',
                'value': param,
            }
        elif type_param is bytes:
            # not the best way to judge, but maybe no better method
            try:
                return {
                    'type': 'String',
                    'value': param.decode(),
                }
            except UnicodeDecodeError:
                return {
                    'type': 'ByteArray',
                    'value': base64.b64encode(param).decode()
                }
        elif type_param is list:
            return {
                'type': 'Array',
                'value': [cls.parse_params(param_) for param_ in param]
            }
        elif type_param is dict:
            return {
                'type': 'Map',
                'value': [{'key': cls.parse_params(k), 'value': cls.parse_params(v)} for k, v in param.items()]
            }
        elif param is None:
            return {
                'type': 'Any',
            }
        raise ValueError(f'Unable to handle param {param} with type {type_param}')
    
    def invokefunction_of_any_contract(self, scripthash: Hash160Str, operation: str,
                                       params: List[Union[str, int, dict, Hash160Str, UInt160]] = None,
                                       signers: List[Signer] = None, relay: bool = None, do_not_raise_on_result=False,
                                       with_print=True, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        if self.with_print and with_print:
            if fairy_session:
                print(f'{fairy_session}::invokefunction {operation}')
            else:
                print(f'invokefunction {operation}')
        
        params = params or []
        signers = signers or self.signers
        parameters = [
            str(scripthash),
            operation,
            list(map(lambda param: self.parse_params(param), params)),
            list(map(lambda signer: signer.to_dict(), signers)),
        ]
        if fairy_session:
            result = self.meta_rpc_method(
                'invokefunctionwithsession', [fairy_session, relay or (relay is None and self.function_default_relay)] + parameters, relay=False,
                do_not_raise_on_result=do_not_raise_on_result)
        else:
            result = self.meta_rpc_method('invokefunction', parameters, relay=relay,
                                          do_not_raise_on_result=do_not_raise_on_result)
        return result
    
    def invokefunction(self, operation: str, params: List[Union[str, int, Hash160Str, UInt160]] = None,
                       signers: List[Signer] = None, relay: bool = None, do_not_raise_on_result=False, with_print=True,
                       fairy_session: str = None) -> Any:
        if self.contract_scripthash is None or self.contract_scripthash == Hash160Str.zero():
            raise ValueError(f'Please set client.contract_scripthash before invoking function. Got {self.contract_scripthash}')
        return self.invokefunction_of_any_contract(self.contract_scripthash, operation, params,
                                                   signers=signers, relay=relay or (relay is None and self.function_default_relay),
                                                   do_not_raise_on_result=do_not_raise_on_result,
                                                   with_print=with_print, fairy_session=fairy_session)
    
    def invokescript(self, script: Union[str, bytes], signers: List[Signer] = None, relay: bool = None,
                     fairy_session: str = None) -> Any:
        if type(script) is bytes:
            script: str = script.decode()
        signers = signers or self.signers
        fairy_session = fairy_session or self.fairy_session
        if fairy_session:
            relay = relay or (relay is None and self.script_default_relay)
            result = self.meta_rpc_method(
                'invokescriptwithsession',
                [fairy_session, relay, script, list(map(lambda signer: signer.to_dict(), signers))],
                relay=False)
        else:
            result = self.meta_rpc_method(
                'invokescript',
                [script, list(map(lambda signer: signer.to_dict(), signers))],
                relay=relay)
        return result
    
    def sendfrom(self, asset_id: Hash160Str, from_address: str, to_address: str, value: int,
                 signers: List[Signer] = None):
        """

        :param asset_id: NEO: '0xef4073a0f2b305a38ec4050e4d3d28bc40ea63f5';
            GAS: '0xd2a4cff31913016155e38e474a2c06d08be276cf'
        :param from_address: "NgaiKFjurmNmiRzDRQGs44yzByXuSkdGPF"
        :param to_address: "NikhQp1aAD1YFCiwknhM5LQQebj4464bCJ"
        :param value: 100000000, including decimals
        :param signers:
        :return:
        """
        if not signers:
            signers = [self.signers]
        return self.meta_rpc_method('sendfrom', [
            asset_id.to_str(),
            from_address, to_address, value,
            signers
        ])
    
    def sendtoaddress(self, asset_id: Hash160Str, address, value: int):
        return self.meta_rpc_method('sendtoaddress', [
            asset_id.string, address, value,
        ])
    
    def send_neo_to_address(self, to_address: Hash160Str, value: int):
        return self.sendtoaddress(Hash160Str.from_UInt160(neo.hash), to_address, value)
    
    def send_gas_to_address(self, to_address: Hash160Str, value: int):
        return self.sendtoaddress(Hash160Str.from_UInt160(gas.hash), to_address, value)
    
    def getwalletbalance(self, asset_id: Hash160Str) -> int:
        return int(self.meta_rpc_method('getwalletbalance', [asset_id.to_str()])['balance'])
    
    def get_neo_balance(self, owner: Hash160Str = None, with_print=False) -> int:
        return self.invokefunction_of_any_contract(Hash160Str.from_UInt160(neo.hash), 'balanceOf', params=[owner or self.wallet_scripthash], relay=False, with_print=with_print)
        # return self.getwalletbalance(Hash160Str.from_UInt160(NeoToken().hash))

    def get_gas_balance(self, owner: Hash160Str = None, with_print=False) -> int:
        return self.invokefunction_of_any_contract(Hash160Str.from_UInt160(gas.hash), 'balanceOf', params=[owner or self.wallet_scripthash], relay=False, with_print=with_print)
        # return self.getwalletbalance(Hash160Str.from_UInt160(GasToken().hash))
    
    def get_nep17token_balance(self, token_address: Hash160Str, owner: Hash160Str = None, with_print=False):
        return self.invokefunction_of_any_contract(token_address, "balanceOf", params=[owner or self.wallet_scripthash], relay=False, with_print=with_print)

    def get_nep11token_balance(self, token_address: Hash160Str, tokenId: Union[bytes, str, int], owner: Hash160Str = None, with_print=False):
        return self.invokefunction_of_any_contract(token_address, "balanceOf", params=[owner or self.wallet_scripthash, tokenId], relay=False, with_print=with_print)

    b"""
    Fairy features below! Mount your neo-cli RpcServer with
    https://github.com/Hecate2/neo-fairy-test/
    before using the following methods!
    """

    def open_fairy_wallet(self, path: str = None, password: str = None) -> dict:
        if not path:
            path = self.wallet_path
        if not password:
            password = self.wallet_password
        if self.verbose_return:
            open_wallet_result, _, _ = self.meta_rpc_method("openfairywallet", [path, password])
        else:
            open_wallet_result = self.meta_rpc_method("openfairywallet", [path, password])
        if not open_wallet_result:
            raise ValueError(f'Failed to open wallet {path} with given password.')
        return open_wallet_result

    def close_fairy_wallet(self) -> dict:
        if self.verbose_return:
            close_wallet_result, _, _ = self.meta_rpc_method("closefairywallet", [])
        else:
            close_wallet_result = self.meta_rpc_method("closefairywallet", [])
        if not close_wallet_result:
            raise ValueError(f'Failed to close wallet.')
        return close_wallet_result

    def new_snapshots_from_current_system(self, fairy_sessions: Union[List[str], str] = None):
        fairy_sessions = fairy_sessions or self.fairy_session
        if fairy_sessions is None:
            raise ValueError('No RpcServer session specified')
        if type(fairy_sessions) is str:
            return self.meta_rpc_method("newsnapshotsfromcurrentsystem", [fairy_sessions])
        return self.meta_rpc_method("newsnapshotsfromcurrentsystem", fairy_sessions)
    
    def delete_snapshots(self, fairy_sessions: Union[List[str], str]):
        return self.meta_rpc_method("deletesnapshots", to_list(fairy_sessions))
    
    def list_snapshots(self):
        return self.meta_rpc_method("listsnapshots", [])
    
    def rename_snapshot(self, old_name: str, new_name: str):
        return self.meta_rpc_method("renamesnapshot", [old_name, new_name])
    
    def copy_snapshot(self, old_name: str, new_name: str):
        return self.meta_rpc_method("copysnapshot", [old_name, new_name])
    
    def set_snapshot_timestamp(self, timestamp_ms: Union[int, None], fairy_session: str = None) -> Dict[str, int]:
        fairy_session = fairy_session or self.fairy_session
        return self.meta_rpc_method("setsnapshottimestamp", [fairy_session, timestamp_ms])
    
    def get_snapshot_timestamp(self, fairy_sessions: Union[List[str], str, None] = None) -> Dict[str, int]:
        fairy_sessions = fairy_sessions or self.fairy_session
        if fairy_sessions is None:
            raise ValueError('No RpcServer session specified')
        if type(fairy_sessions) is str:
            return self.meta_rpc_method("getsnapshottimestamp", [fairy_sessions])
        return self.meta_rpc_method("getsnapshottimestamp", fairy_sessions)

    def set_snapshot_random(self, designated_random: Union[int, None], fairy_session: str = None) -> Dict[str, Union[int, None]]:
        """
        @param designated_random: use None to delete the designated random and let Fairy choose any random number
        """
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method("setsnapshotrandom", [fairy_session, designated_random])
        for k in result:
            result[k] = None if result[k] is None else int(result[k])
        return result

    def get_snapshot_random(self, fairy_sessions: Union[List[str], str] = None) -> Dict[str, int]:
        fairy_sessions = fairy_sessions or self.fairy_session
        if type(fairy_sessions) is str:
            result = self.meta_rpc_method("getsnapshotrandom", [fairy_sessions])
        else:
            result = self.meta_rpc_method("getsnapshotrandom", fairy_sessions)
        for k, v in result.items():
            result[k] = None if not v else int(v)
        return result

    def virtual_deploy(self, nef: bytes, manifest: str, fairy_session: str = None) -> Hash160Str:
        fairy_session = fairy_session or self.fairy_session
        return Hash160Str(self.meta_rpc_method("virtualdeploy", [fairy_session, base64.b64encode(nef).decode(), manifest])[fairy_session])

    def virutal_deploy_from_path(self, nef_path_and_filename: str, fairy_session: str = None,
                                 auto_dumpnef=True, dumpnef_backup=True, auto_set_debug_info=True) -> Hash160Str:
        """
        auto virtual deploy which also executes dumpnef (on your machine) and SetDebugInfo (with RPC)
        :param nef_path_and_filename: `../NFTLoan/NFTLoan/bin/sc/NFTFlashLoan.nef`
        """
        fairy_session = fairy_session or self.fairy_session
        path, nef_filename = os.path.split(nef_path_and_filename)  # '../NFTLoan/NFTLoan/bin/sc', 'NFTFlashLoan.nef'
        assert nef_filename.endswith('.nef')
        with open(nef_path_and_filename, 'rb') as f:
            nef = f.read()
        contract_path_and_filename = nef_path_and_filename[:-4]  # '../NFTLoan/NFTLoan/bin/sc/NFTFlashLoan'
        with open(contract_path_and_filename+".manifest.json", 'r') as f:
            manifest = f.read()
        contract_hash = self.virtual_deploy(nef, manifest, fairy_session)
        nefdbgnfo_path_and_filename = contract_path_and_filename + '.nefdbgnfo'
        dumpnef_path_and_filename = contract_path_and_filename + '.nef.txt'
        if os.path.exists(nefdbgnfo_path_and_filename):
            if auto_dumpnef:
                if dumpnef_backup and os.path.exists(dumpnef_path_and_filename) and not os.path.exists(contract_path_and_filename + '.bk.txt'):
                    # only backup the .nef.txt file when no backup exists
                    os.rename(dumpnef_path_and_filename, contract_path_and_filename + '.bk.txt')
                print(f'dumpnef {nef_filename}', os.popen(f'dumpnef {nef_path_and_filename} > {nef_path_and_filename}.txt').read())
            if auto_set_debug_info and os.path.exists(dumpnef_path_and_filename) \
                    and os.path.getmtime(dumpnef_path_and_filename) >= os.path.getmtime(nef_path_and_filename) \
                    and fairy_session:
                with open(nefdbgnfo_path_and_filename, 'rb') as f:
                    nefdbgnfo = f.read()
                with open(dumpnef_path_and_filename, 'r') as f:
                    dumpnef = f.read()
                self.set_debug_info(nefdbgnfo, dumpnef, contract_hash)
        else:
            print('WARNING! No .nefdbgnfo found.'
                  'It is highly recommended to generate .nefdbgnfo for debugging.'
                  'If you are writing contracts in C#,'
                  'consider building your project with command `nccs your.csproj --debug`.')
        return contract_hash

    @staticmethod
    def all_to_base64(key: Union[str, bytes, int]) -> str:
        if type(key) is str:
            key = key.encode()
        if type(key) is int:
            key = Interpreter.int_to_bytes(key)
        if type(key) is bytes:
            key = base64.b64encode(key).decode()
        else:
            raise ValueError(f'Unexpected input type {type(key)} {key}')
        return key

    def get_storage_with_session(self, key: Union[str, bytes, int], fairy_session: str = None, contract_scripthash: Hash160Str = None) -> Dict[str, str]:
        fairy_session = fairy_session or self.fairy_session
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("getstoragewithsession", [fairy_session, contract_scripthash, self.all_to_base64(key)])

    def find_storage_with_session(self, key: Union[str, bytes, int], fairy_session: str = None, contract_scripthash: Hash160Str = None) -> Dict[str, str]:
        fairy_session = fairy_session or self.fairy_session
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("findstoragewithsession", [fairy_session, contract_scripthash, self.all_to_base64(key)])

    def put_storage_with_session(self, key: Union[str, bytes, int], value: Union[str, bytes, int], fairy_session: str = None, contract_scripthash: Hash160Str = None) -> Dict[str, str]:
        """
        :param value==0 deletes the key-value pair
        """
        fairy_session = fairy_session or self.fairy_session
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("putstoragewithsession", [fairy_session, contract_scripthash, self.all_to_base64(key), self.all_to_base64(value)])

    def set_neo_balance(self, balance: Union[int, float], fairy_session: str = None, account: Hash160Str = None):
        balance = int(balance)
        fairy_session = fairy_session or self.fairy_session
        account = account or self.wallet_scripthash
        return self.meta_rpc_method("setneobalance", [fairy_session, account, balance])

    def set_gas_balance(self, balance: Union[int, float], fairy_session: str = None, account: Hash160Str = None):
        balance = int(balance)
        fairy_session = fairy_session or self.fairy_session
        account = account or self.wallet_scripthash
        return self.meta_rpc_method("setgasbalance", [fairy_session, account, balance])

    def set_nep17_balance(self, contract: Hash160Str, balance: int, fairy_session: str = None, account: Hash160Str = None, byte_prefix: int = 1):
        if byte_prefix >= 256 or byte_prefix < 0:
            raise ValueError(f'Only 0<=byte_prefix<=255 accepted. Got {byte_prefix}')
        fairy_session = fairy_session or self.fairy_session
        account = account or self.wallet_scripthash
        return self.meta_rpc_method("setnep17balance", [fairy_session, contract, account, balance, byte_prefix])

    b"""
    Fairy debugger features!
    """
    """debug info and file names"""
    def set_debug_info(self, nefdbgnfo: bytes, dumpnef_content: str, contract_scripthash: Hash160Str = None) -> Dict[Hash160Str, bool]:
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return {Hash160Str(k): v for k, v in self.meta_rpc_method("setdebuginfo", [contract_scripthash, self.all_to_base64(nefdbgnfo), dumpnef_content]).items()}

    def list_debug_info(self) -> List[Hash160Str]:
        return [Hash160Str(i) for i in self.meta_rpc_method("listdebuginfo", [])]

    def list_filenames_of_contract(self, contract_scripthash: Hash160Str = None) -> List[Hash160Str]:
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("listfilenamesofcontract", [contract_scripthash])

    def delete_debug_info(self, contract_scripthashes: Union[List[Hash160Str], Hash160Str]) -> Dict[Hash160Str, bool]:
        if type(contract_scripthashes) is Hash160Str:
            result: Dict[str, bool] = self.meta_rpc_method("deletedebuginfo", [contract_scripthashes])
        else:
            result: Dict[str, bool] = self.meta_rpc_method("deletedebuginfo", contract_scripthashes)
        return {Hash160Str(k): v for k, v in result.items()}

    """breakpoints"""
    def set_assembly_breakpoints(self, instruction_pointers: Union[int, List[int]], contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        if type(instruction_pointers) is int:
            return self.meta_rpc_method("setassemblybreakpoints", [contract_scripthash, instruction_pointers])
        else:
            return self.meta_rpc_method("setassemblybreakpoints", [contract_scripthash] + list(instruction_pointers))

    def list_assembly_breakpoints(self, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("listassemblybreakpoints", [contract_scripthash])

    def delete_assembly_breakpoints(self, instruction_pointers: Union[int, List[int]] = None, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        instruction_pointers = [] if instruction_pointers is None else instruction_pointers
        if type(instruction_pointers) is int:
            return self.meta_rpc_method("deleteassemblybreakpoints", [contract_scripthash, instruction_pointers])
        else:
            return self.meta_rpc_method("deleteassemblybreakpoints", [contract_scripthash] + list(instruction_pointers))

    def set_source_code_breakpoint(self, filename: str, line_num: int, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("setsourcecodebreakpoints", [contract_scripthash, filename, line_num])

    def set_source_code_breakpoints(self, filename_and_line_num: List[Union[str, int]], contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("setsourcecodebreakpoints", [contract_scripthash] + filename_and_line_num)

    def list_source_code_breakpoints(self, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("listsourcecodebreakpoints", [contract_scripthash])

    def delete_source_code_breakpoint(self, filename: str, line_num: int, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        return self.meta_rpc_method("deletesourcecodebreakpoints", [contract_scripthash, filename, line_num])

    def delete_source_code_breakpoints(self, filename_and_line_num: List[Union[str, int]] = None, contract_scripthash: Hash160Str = None):
        contract_scripthash = contract_scripthash or self.contract_scripthash
        filename_and_line_num = filename_and_line_num or []
        return self.meta_rpc_method("deletesourcecodebreakpoints", [contract_scripthash] + filename_and_line_num)

    def delete_debug_snapshots(self, fairy_sessions: Union[List[str], str]):
        if type(fairy_sessions) is str:
            return self.meta_rpc_method("deletedebugsnapshots", [fairy_sessions])
        return self.meta_rpc_method("deletedebugsnapshots", fairy_sessions)

    def list_debug_snapshots(self):
        return self.meta_rpc_method("listdebugsnapshots", [])

    def get_method_by_instruction_pointer(self, instruction_pointer: int, scripthash: Hash160Str = None):
        scripthash = scripthash or self.contract_scripthash
        return self.meta_rpc_method("getmethodbyinstructionpointer", [scripthash, instruction_pointer])

    def debug_any_function_with_session(self, scripthash: Hash160Str, operation: str,
                                       params: List[Union[str, int, dict, Hash160Str, UInt160]] = None,
                                       signers: List[Signer] = None, relay: bool = None, do_not_raise_on_result=False,
                                       with_print=True, fairy_session: str = None) -> Any:
        scripthash = scripthash or self.contract_scripthash
        fairy_session = fairy_session or self.fairy_session
        if self.with_print and with_print:
            if fairy_session:
                print(f'{fairy_session}::debugfunction {operation}')
            else:
                print(f'debugfunction {operation}')
    
        params = params or []
        signers = signers or self.signers
        parameters = [
            str(scripthash),
            operation,
            list(map(lambda param: self.parse_params(param), params)),
            list(map(lambda signer: signer.to_dict(), signers)),
        ]
        raw_result = self.meta_rpc_method_with_raw_result(
            'debugfunctionwithsession',
            [fairy_session, relay or (relay is None and self.function_default_relay)] + parameters)
        result = raw_result['result']
        return RpcBreakpoint(result['state'], result['breakreason'],
                             result['scripthash'], result['contractname'], result['instructionpointer'],
                             result['sourcefilename'], result['sourcelinenum'], result['sourcecontent'],
                             exception=result['exception'], result_stack=self.parse_stack_from_raw_result(raw_result))

    def debug_function_with_session(self, operation: str,
                                        params: List[Union[str, int, dict, Hash160Str, UInt160]] = None,
                                        signers: List[Signer] = None, relay: bool = None, do_not_raise_on_result=False,
                                        with_print=True, fairy_session: str = None) -> Any:
        return self.debug_any_function_with_session(
            self.contract_scripthash, operation,
            params=params, signers=signers, relay=relay, do_not_raise_on_result=do_not_raise_on_result,
            with_print=with_print, fairy_session=fairy_session)

    def debug_continue(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugcontinue", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def debug_step_into(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugstepinto", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def debug_step_out(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugstepout", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def debug_step_over(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugstepover", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def debug_step_over_source_code(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugstepoversourcecode", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def debug_step_over_assembly(self, fairy_session: str = None) -> RpcBreakpoint:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("debugstepoverassembly", [fairy_session])
        return RpcBreakpoint.from_raw_result(result)

    def get_local_variables(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getlocalvariables", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)

    def get_arguments(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getarguments", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)

    def get_static_fields(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getstaticfields", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)

    def get_evaluation_stack(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getevaluationstack", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)

    def get_instruction_pointer(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getinstructionpointer", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)[0]

    def get_variable_value_by_name(self, variable_name: str, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getvariablevaluebyname", [fairy_session, variable_name, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)

    def get_variable_names_and_values(self, invocation_stack_index: int = 0, fairy_session: str = None) -> Any:
        fairy_session = fairy_session or self.fairy_session
        result = self.meta_rpc_method_with_raw_result("getvariablenamesandvalues", [fairy_session, invocation_stack_index])
        return self.parse_stack_from_raw_result(result)
    
    def get_contract_opcode_coverage(self, scripthash: UInt160 = None) -> Dict[int, bool]:
        scripthash = scripthash or self.contract_scripthash
        result: Dict[str, bool] = self.meta_rpc_method_with_raw_result("getcontractopcodecoverage", [scripthash])['result']
        return {int(k): v for k, v in result.items()}
    
    def clear_contract_opcode_coverage(self, scripthash: UInt160 = None) -> Dict[int, bool]:
        scripthash = scripthash or self.contract_scripthash
        result: Dict[str, bool] = self.meta_rpc_method_with_raw_result("clearcontractopcodecoverage", [scripthash])['result']
        return {int(k): v for k, v in result.items()}
