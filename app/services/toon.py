
import json
import logging
from typing import Any, Dict, List, Union
from datetime import date, datetime

logger = logging.getLogger(__name__)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

class ToonCodec:
    """
    Token-Oriented Object Notation (TOON) Codec.
    
    Implements dictionary-based compression for JSON-like structures.
    Replaces repeated strings with reference tags (~index) and a lookup table.
    """
    def __init__(self):
        self.lookup_table = []
        self.reverse_lookup = {}
        self.raw_size = 0
        self.toon_size = 0

    def encode(self, data: Any) -> Dict[str, Any]:
        """
        Encodes data into TOON format.
        Returns: {"data": compressed_structure, "lookup": [strings], "meta": {...}}
        """
        # 1. Normalize
        json_str = json.dumps(data, default=json_serial)
        normalized_data = json.loads(json_str)
        self.raw_size = len(json_str)

        # 2. Reset state
        self.lookup_table = []
        self.reverse_lookup = {}

        # 3. Compress
        encoded_data = self._compress_recursive(normalized_data)

        # 4. Construct Payload
        toon_payload = {
            "lookup": self.lookup_table,
            "data": encoded_data
        }
        
        toon_str = json.dumps(toon_payload)
        self.toon_size = len(toon_str)

        reduction_pct = 0.0
        if self.raw_size > 0:
            reduction_pct = ((self.raw_size - self.toon_size) / self.raw_size) * 100.0

        return {
            "payload": toon_payload, # The actual TOON object
            "meta": {
                "raw_len": self.raw_size,
                "toon_len": self.toon_size,
                "savings": f"{reduction_pct:.2f}%"
            }
        }

    def decode(self, payload: Dict[str, Any]) -> Any:
        """
        Decodes a TOON payload.
        Expects: {"lookup": [...], "data": ...}
        """
        lookup = payload.get("lookup", [])
        data = payload.get("data")
        return self._decompress_recursive(data, lookup)

    def _compress_recursive(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {
                self._get_ref(k): self._compress_recursive(v) 
                for k, v in node.items()
            }
        elif isinstance(node, list):
            return [self._compress_recursive(item) for item in node]
        elif isinstance(node, str):
            return self._get_ref(node)
        else:
            return node

    def _get_ref(self, value: str) -> str:
        if value not in self.reverse_lookup:
            idx = len(self.lookup_table)
            self.lookup_table.append(value)
            self.reverse_lookup[value] = idx
        return f"~{self.reverse_lookup[value]}"

    def _decompress_recursive(self, node: Any, lookup: List[str]) -> Any:
        if isinstance(node, dict):
            decoded = {}
            for k, v in node.items():
                key = self._resolve_ref(k, lookup)
                decoded[key] = self._decompress_recursive(v, lookup)
            return decoded
        elif isinstance(node, list):
            return [self._decompress_recursive(item, lookup) for item in node]
        elif isinstance(node, str):
            return self._resolve_ref(node, lookup)
        return node

    def _resolve_ref(self, val: str, lookup: List[str]) -> str:
        if val.startswith("~"):
            try:
                idx = int(val[1:])
                if 0 <= idx < len(lookup):
                    return lookup[idx]
            except ValueError:
                pass
        return val

# Singleton
toon = ToonCodec()
