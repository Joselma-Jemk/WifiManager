"""Pare-feu Windows (WFP) : bloque le trafic d'un appareil du hotspot. Droits administrateur requis.

Les filtres vivent dans une session « dynamique » : ils disparaissent dès que le service s'arrête ou ferme le moteur.
Un blocage ne reste donc jamais posé par accident, et il ne peut pas verrouiller l'utilisateur dehors.

Deux filtres par appareil :
- paquets reçus de l'appareil (couche IP entrante, adresse distante) : coupe ses requêtes avant tout routage ;
- paquets transférés vers l'appareil (couche de transfert, adresse de destination) : coupe ce qui revenait vers lui.

Vérifié sur la machine cible : l'appareil bloqué ne répond plus au ping, et répond de nouveau dès le retrait.
"""

from __future__ import annotations

import ctypes
import ipaddress
import socket
import struct
import sys
import uuid
from collections.abc import Iterable
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
    c_void_p,
    c_wchar_p,
)
from typing import Protocol

LAYER_INBOUND_IPPACKET_V4 = "c86fd1bf-21cd-497e-a0bb-17425c885c58"
LAYER_IPFORWARD_V4 = "a82acc24-4ee1-4ee1-b465-fd1d25cb10a4"
CONDITION_IP_REMOTE_ADDRESS = "b235ae9a-1d64-49b8-a44c-5ff3d9095045"
CONDITION_IP_DESTINATION_ADDRESS = "2d79133b-b390-45c6-8699-acaceaafed33"

_FWP_UINT32 = 3
_FWP_MATCH_EQUAL = 0
_FWP_ACTION_BLOCK = 0x1001
_SESSION_FLAG_DYNAMIC = 1
_RPC_C_AUTHN_WINNT = 10
_ACCESS_DENIED = 0x00000005
_FWP_E_ACCESS_DENIED = 0x80320005


class WfpError(Exception):
    """Une opération du pare-feu a échoué."""


# --- structures natives (disposition vérifiée : Filter = 200 octets sur Windows 64 bits) ------------------


class _Guid(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_uint8 * 8)]

    @classmethod
    def of(cls, text: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class _DisplayData(Structure):
    _fields_ = [("name", c_wchar_p), ("description", c_wchar_p)]


class _ByteBlob(Structure):
    _fields_ = [("size", c_uint32), ("data", POINTER(c_uint8))]


class _ValueUnion(Union):
    _fields_ = [("uint8", c_uint8), ("uint16", c_uint16), ("uint32", c_uint32), ("pointer", c_void_p)]


class _Value(Structure):
    _fields_ = [("type", c_uint32), ("u", _ValueUnion)]


class _Condition(Structure):
    _fields_ = [("fieldKey", _Guid), ("matchType", c_uint32), ("conditionValue", _Value)]


class _ActionUnion(Union):
    _fields_ = [("filterType", _Guid), ("calloutKey", _Guid)]


class _Action(Structure):
    _fields_ = [("type", c_uint32), ("u", _ActionUnion)]


class _ContextUnion(Union):
    _fields_ = [("rawContext", c_uint64), ("providerContextKey", _Guid)]


class _Filter(Structure):
    _fields_ = [
        ("filterKey", _Guid),
        ("displayData", _DisplayData),
        ("flags", c_uint32),
        ("providerKey", POINTER(_Guid)),
        ("providerData", _ByteBlob),
        ("layerKey", _Guid),
        ("subLayerKey", _Guid),
        ("weight", _Value),
        ("numFilterConditions", c_uint32),
        ("filterCondition", POINTER(_Condition)),
        ("action", _Action),
        ("ctx", _ContextUnion),
        ("reserved", POINTER(_Guid)),
        ("filterId", c_uint64),
        ("effectiveWeight", _Value),
    ]


class _SubLayer(Structure):
    _fields_ = [
        ("subLayerKey", _Guid),
        ("displayData", _DisplayData),
        ("flags", c_uint16),
        ("providerKey", POINTER(_Guid)),
        ("providerData", _ByteBlob),
        ("weight", c_uint16),
    ]


class _Session(Structure):
    _fields_ = [
        ("sessionKey", _Guid),
        ("displayData", _DisplayData),
        ("flags", c_uint32),
        ("txnWaitTimeoutInMSec", c_uint32),
        ("processId", c_uint32),
        ("sid", c_void_p),
        ("username", c_wchar_p),
        ("kernelMode", c_uint32),
    ]


class Engine(Protocol):
    """Ce dont `Firewall` a besoin : le vrai moteur Windows, ou un faux pour les tests."""

    def open(self) -> None: ...
    def add_block(self, layer: str, field: str, ip: str, name: str) -> int: ...
    def delete(self, filter_id: int) -> None: ...
    def close(self) -> None: ...


class NativeEngine:
    """Moteur WFP réel (fwpuclnt.dll). Windows et droits administrateur requis."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WfpError("Le pare-feu Windows n'est disponible que sous Windows")
        self._fwp = ctypes.windll.fwpuclnt
        for name in ("FwpmEngineOpen0", "FwpmEngineClose0", "FwpmSubLayerAdd0", "FwpmFilterAdd0", "FwpmFilterDeleteById0"):
            getattr(self._fwp, name).restype = c_uint32
        self._engine = c_void_p()
        self._sublayer = _SubLayer()
        self._opened = False

    @staticmethod
    def _check(code: int, action: str) -> None:
        if code == 0:
            return
        hint = " (droits administrateur requis)" if code in (_ACCESS_DENIED, _FWP_E_ACCESS_DENIED) else ""
        raise WfpError(f"{action} a échoué : 0x{code:08X}{hint}")

    def open(self) -> None:
        session = _Session()
        session.flags = _SESSION_FLAG_DYNAMIC  # les filtres disparaissent avec la session
        session.displayData = _DisplayData("WifiManager", "Blocage des appareils du hotspot")
        self._check(
            self._fwp.FwpmEngineOpen0(None, _RPC_C_AUTHN_WINNT, None, byref(session), byref(self._engine)),
            "Ouverture du moteur du pare-feu",
        )
        self._opened = True
        self._sublayer.subLayerKey = _Guid.from_buffer_copy(uuid.uuid4().bytes_le)
        self._sublayer.displayData = _DisplayData("WifiManager", "")
        self._sublayer.weight = 0xFFFF  # priorité maximale : passe avant les autres sous-couches
        self._check(self._fwp.FwpmSubLayerAdd0(self._engine, byref(self._sublayer), None), "Création de la sous-couche")

    def add_block(self, layer: str, field: str, ip: str, name: str) -> int:
        condition = _Condition()
        condition.fieldKey = _Guid.of(field)
        condition.matchType = _FWP_MATCH_EQUAL
        condition.conditionValue.type = _FWP_UINT32
        condition.conditionValue.u.uint32 = struct.unpack("!I", socket.inet_aton(ip))[0]

        flt = _Filter()
        flt.displayData = _DisplayData(name, "WifiManager")
        flt.layerKey = _Guid.of(layer)
        flt.subLayerKey = self._sublayer.subLayerKey
        flt.weight.type = 0  # FWP_EMPTY : poids automatique
        flt.numFilterConditions = 1
        flt.filterCondition = ctypes.pointer(condition)
        flt.action.type = _FWP_ACTION_BLOCK
        filter_id = c_uint64(0)
        self._check(self._fwp.FwpmFilterAdd0(self._engine, byref(flt), None, byref(filter_id)), f"Ajout du filtre « {name} »")
        return int(filter_id.value)

    def delete(self, filter_id: int) -> None:
        self._check(self._fwp.FwpmFilterDeleteById0(self._engine, c_uint64(filter_id)), "Retrait d'un filtre")

    def close(self) -> None:
        if self._opened:
            self._fwp.FwpmEngineClose0(self._engine)  # session dynamique : tout ce qui reste est retiré ici
            self._opened = False


# --- logique de blocage (indépendante de Windows) -----------------------------------------------------------


def check_blockable(ip: str, protected: Iterable[str] = ()) -> str:
    """Refuse toute adresse qu'on ne doit jamais bloquer : mal formée, publique, locale, ou protégée."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise WfpError(f"Adresse IP invalide : {ip!r}") from exc
    if address.version != 4:
        raise WfpError("Seules les adresses IPv4 sont gérées")
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast:
        raise WfpError(f"Adresse non bloquable : {ip}")
    if ip in set(protected):
        raise WfpError(f"Adresse protégée (le PC lui-même ou sa passerelle) : {ip}")
    return str(address)


class Firewall:
    """Pose et retire les blocages par adresse IP. Chaque blocage = deux filtres, posés ou retirés ensemble."""

    def __init__(self, engine: Engine, protected: Iterable[str] = ()) -> None:
        self._engine = engine
        self._protected = set(protected)
        self._filters: dict[str, list[int]] = {}
        self._engine.open()

    def set_protected(self, addresses: Iterable[str]) -> None:
        self._protected = set(addresses)

    @property
    def blocked(self) -> frozenset[str]:
        return frozenset(self._filters)

    def block(self, ip: str) -> None:
        ip = check_blockable(ip, self._protected)
        if ip in self._filters:
            return
        ids: list[int] = []
        try:
            ids.append(
                self._engine.add_block(LAYER_INBOUND_IPPACKET_V4, CONDITION_IP_REMOTE_ADDRESS, ip, f"Bloque depuis {ip}")
            )
            ids.append(
                self._engine.add_block(LAYER_IPFORWARD_V4, CONDITION_IP_DESTINATION_ADDRESS, ip, f"Bloque vers {ip}")
            )
        except WfpError:
            for filter_id in ids:  # un blocage à moitié posé n'est pas acceptable : on défait ce qui a réussi
                try:
                    self._engine.delete(filter_id)
                except WfpError:
                    pass
            raise
        self._filters[ip] = ids

    def unblock(self, ip: str) -> None:
        ids = self._filters.get(ip)
        if ids is None:
            return
        errors: list[str] = []
        remaining: list[int] = []
        for filter_id in ids:
            try:
                self._engine.delete(filter_id)
            except WfpError as exc:
                errors.append(str(exc))
                remaining.append(filter_id)
        if remaining:
            self._filters[ip] = remaining  # on garde la trace de ce qui reste pour réessayer
            raise WfpError("; ".join(errors))
        del self._filters[ip]

    def sync(self, wanted: Iterable[str]) -> tuple[set[str], set[str]]:
        """Aligne les blocages sur la liste voulue. Renvoie (ajoutés, retirés). Un échec n'empêche pas le reste."""
        wanted_set = set(wanted)
        added: set[str] = set()
        removed: set[str] = set()
        failures: list[str] = []
        for ip in sorted(set(self._filters) - wanted_set):
            try:
                self.unblock(ip)
                removed.add(ip)
            except WfpError as exc:
                failures.append(str(exc))
        for ip in sorted(wanted_set - set(self._filters)):
            try:
                self.block(ip)
                added.add(ip)
            except WfpError as exc:
                failures.append(str(exc))
        if failures:
            raise WfpError("; ".join(failures))
        return added, removed

    def close(self) -> None:
        self._filters.clear()
        self._engine.close()
