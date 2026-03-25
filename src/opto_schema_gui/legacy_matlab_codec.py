from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


class MatlabCodecError(ValueError):
    pass


@dataclass
class _Reader:
    data: bytes
    pos: int = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise MatlabCodecError("Unexpected end of MATLAB serialized payload")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def take_u8(self) -> int:
        return self.take(1)[0]

    def take_u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def take_u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]


_SCALAR_TAGS = {
    1: ("<d", 8),
    2: ("<f", 4),
    3: ("<b", 1),
    4: ("<B", 1),
    5: ("<h", 2),
    6: ("<H", 2),
    7: ("<i", 4),
    8: ("<I", 4),
    9: ("<q", 8),
    10: ("<Q", 8),
}

_SIMPLE_NUMERIC_TAGS = {tag + 16: spec for tag, spec in _SCALAR_TAGS.items()}


def deserialize_legacy_matlab(data: bytes) -> Any:
    reader = _Reader(data)
    value = _deserialize_value(reader)
    if reader.pos != len(reader.data):
        raise MatlabCodecError("Trailing bytes in MATLAB serialized payload")
    return value


def serialize_legacy_matlab(value: Any) -> bytes:
    if isinstance(value, dict):
        return _serialize_struct(value)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, bool):
        return _serialize_logical(value)
    if isinstance(value, (int, float)):
        return _serialize_double(float(value))
    if isinstance(value, (list, tuple)):
        return _serialize_cell(list(value))
    raise MatlabCodecError(f"Unsupported legacy MATLAB value type: {type(value)!r}")


def build_ready_message(confirm_id: int = 0) -> bytes:
    return serialize_legacy_matlab(
        {
            "messageData": "READY",
            "messageType": "COM",
            "confirmID": float(confirm_id),
            "confirm": 0.0,
        }
    )


def extract_legacy_command(data: bytes) -> dict[str, Any] | None:
    try:
        value = deserialize_legacy_matlab(data)
    except MatlabCodecError:
        return None
    if not isinstance(value, dict):
        return None
    if "messageType" not in value or "messageData" not in value:
        return None
    return value


def _deserialize_value(reader: _Reader) -> Any:
    tag = reader.take_u8()
    if tag in (0, 200):
        return _deserialize_string(reader, tag)
    if tag == 128:
        return _deserialize_struct(reader)
    if tag in (33, 34, 35, 36, 37, 38, 39):
        return _deserialize_cell(reader, tag)
    if tag in _SCALAR_TAGS:
        return _deserialize_scalar(reader, tag)
    if tag == 133:
        return _deserialize_logical(reader)
    if tag in _SIMPLE_NUMERIC_TAGS:
        return _deserialize_numeric_simple(reader, tag)
    raise MatlabCodecError(f"Unsupported MATLAB serialization tag: {tag}")


def _deserialize_scalar(reader: _Reader, tag: int) -> Any:
    fmt, size = _SCALAR_TAGS[tag]
    return struct.unpack(fmt, reader.take(size))[0]


def _deserialize_string(reader: _Reader, tag: int) -> str:
    if tag == 200:
        return ""
    nbytes = reader.take_u32()
    return reader.take(nbytes).decode("latin1")


def _deserialize_logical(reader: _Reader) -> Any:
    ndims = reader.take_u8()
    dims = [reader.take_u32() for _ in range(ndims)]
    nbytes = _prod(dims)
    values = [bool(byte) for byte in reader.take(nbytes)]
    return _reshape_matlab(values, dims)


def _deserialize_numeric_simple(reader: _Reader, tag: int) -> Any:
    fmt, size = _SIMPLE_NUMERIC_TAGS[tag]
    ndims = reader.take_u8()
    dims = [reader.take_u32() for _ in range(ndims)]
    count = _prod(dims)
    raw = reader.take(count * size)
    values = list(struct.iter_unpack(fmt, raw))
    flattened = [item[0] for item in values]
    return _reshape_matlab(flattened, dims)


def _deserialize_struct(reader: _Reader) -> dict[str, Any]:
    nfields = reader.take_u32()
    field_name_lengths = [reader.take_u32() for _ in range(nfields)]
    field_names_concat = reader.take(sum(field_name_lengths)).decode("latin1")
    ndims = reader.take_u32()
    dims = [reader.take_u32() for _ in range(ndims)]
    field_names: list[str] = []
    offset = 0
    for length in field_name_lengths:
        field_names.append(field_names_concat[offset : offset + length])
        offset += length

    mode = reader.take_u8()
    if mode != 1:
        raise MatlabCodecError("Unsupported MATLAB struct serialization mode")
    contents = _deserialize_value(reader)
    if not isinstance(contents, list):
        raise MatlabCodecError("Expected cell array content for serialized struct")
    count = _prod(dims)
    if count != 1:
        raise MatlabCodecError("Only scalar legacy structs are supported")
    if len(contents) != len(field_names):
        raise MatlabCodecError("Struct field/value count mismatch")
    return dict(zip(field_names, contents))


def _deserialize_cell(reader: _Reader, tag: int) -> list[Any]:
    if tag == 33:
        ndims = reader.take_u8()
        dims = [reader.take_u32() for _ in range(ndims)]
        count = _prod(dims)
        return [_deserialize_value(reader) for _ in range(count)]
    if tag == 34:
        content = _deserialize_value(reader)
        if not isinstance(content, list):
            return [content]
        return content
    if tag == 36:
        chars = _deserialize_value(reader)
        lengths = _deserialize_value(reader)
        empties = _deserialize_value(reader)
        if not isinstance(chars, str) or not isinstance(lengths, list) or not isinstance(empties, list):
            raise MatlabCodecError("Unsupported cell-string payload")
        items: list[str] = []
        offset = 0
        for length, is_empty in zip(lengths, empties):
            length_int = int(length)
            item = chars[offset : offset + length_int]
            offset += length_int
            items.append("" if is_empty else item)
        return items
    if tag == 37:
        _ = reader.take_u8()
        ndims = reader.take_u8()
        dims = [reader.take_u32() for _ in range(ndims)]
        return [None] * _prod(dims)
    if tag == 39:
        content = _deserialize_value(reader)
        if isinstance(content, list):
            return [bool(item) for item in content]
        return [bool(content)]
    raise MatlabCodecError(f"Unsupported MATLAB cell serialization tag: {tag}")


def _serialize_double(value: float) -> bytes:
    return bytes([1]) + struct.pack("<d", value)


def _serialize_string(value: str) -> bytes:
    encoded = value.encode("latin1")
    return bytes([0]) + struct.pack("<I", len(encoded)) + encoded


def _serialize_logical(value: bool) -> bytes:
    return bytes([133, 2]) + struct.pack("<II", 1, 1) + (b"\x01" if value else b"\x00")


def _serialize_numeric_simple(values: list[float], dims: tuple[int, ...]) -> bytes:
    payload = b"".join(struct.pack("<d", value) for value in values)
    return bytes([17, len(dims)]) + b"".join(struct.pack("<I", dim) for dim in dims) + payload


def _serialize_cell(values: list[Any]) -> bytes:
    serialized = b"".join(serialize_legacy_matlab(value) for value in values)
    return bytes([33, 2]) + struct.pack("<II", len(values), 1) + serialized


def _serialize_struct(value: dict[str, Any]) -> bytes:
    field_names = list(value.keys())
    field_name_bytes = [name.encode("latin1") for name in field_names]
    parts = [
        bytes([128]),
        struct.pack("<I", len(field_names)),
        b"".join(struct.pack("<I", len(name)) for name in field_name_bytes),
        b"".join(field_name_bytes),
        struct.pack("<I", 2),
        struct.pack("<II", 1, 1),
        bytes([1]),
        _serialize_cell([value[name] for name in field_names]),
    ]
    return b"".join(parts)


def _prod(values: list[int]) -> int:
    total = 1
    for value in values:
        total *= int(value)
    return total


def _reshape_matlab(values: list[Any], dims: list[int]) -> Any:
    if not dims:
        return values[0] if values else []
    if _prod(dims) == 1:
        return values[0]
    return values
