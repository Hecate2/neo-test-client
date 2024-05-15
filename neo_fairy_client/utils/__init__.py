from neo_fairy_client.utils.types import Hash160Str, Hash256Str, UInt160, UInt256, PublicKeyStr, Signer, WitnessScope, VMState, NamedCurveHash
from neo_fairy_client.utils.interpreters import Interpreter
from neo_fairy_client.utils.misc import to_list
from enum import Enum

ContractManagementAddress = Hash160Str('0xfffdc93764dbaddd97c48f252a53ea4643faa3fd')
CryptoLibAddress = Hash160Str('0x726cb6e0cd8628a1350a611384688911ab75f51b')
GasAddress = Hash160Str('0xd2a4cff31913016155e38e474a2c06d08be276cf')
LedgerAddress = Hash160Str('0xda65b600f7124ce6c79950c1772a36403104f2be')
NeoAddress = Hash160Str('0xef4073a0f2b305a38ec4050e4d3d28bc40ea63f5')
OracleAddress = Hash160Str('0xfe924b7cfe89ddd271abaf7210a80a7e11178758')
PolicyAddress = Hash160Str('0xcc5e4edd9f5f8dba8bb65734541df7a1c081c67b')
RoleManagementAddress = Hash160Str('0x49cf4e5378ffcd4dec034fd98a174c5491e395e2')
StdLibAddress = Hash160Str('0xacce6fd80d44e1796aa0c2c625e9e4e0ce39efc0')

defaultFairyWalletPublicKeySecp256R1 = PublicKeyStr('0262cafcd9cba9463c868e6f9e3cbe490d658941cee3523d4011090a344287e2e1')
defaultFairyWalletPublicKeySecp256K1 = PublicKeyStr('037e42df03eafb19fcbc60d37fa300053a4efc5360e3797d29ccbec488cba1a76e')
defaultFairyWalletScriptHash = Hash160Str('0xd2cefc96ad5cb7b625a0986ef6badde0533731d5')
