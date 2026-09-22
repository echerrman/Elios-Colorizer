"""Extract selected messages from the supplied StarNet log, verifying each CRC.

Framing/escaping were inferred from this file; every record is CRC-checked.
Records that fail that check are reported and excluded. This is a scoped reader
for this flight, not a general
vendor-supported StarNet implementation. Camera payload layouts follow the
embedded version-4 Camera schema and version-2 Sensors schema.
"""
import argparse
import binascii
from collections import Counter
import csv
import json
from pathlib import Path
import struct

ESCAPES = {0xAB: 0xAA, 0xAC: 0x55, 0xAD: 0xF0, 0xAF: 0xA5}


def unescape(data):
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0xA5:
            result.append(ESCAPES[data[i + 1]])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('analysis/native_flight'))
    args = parser.parse_args()
    source = args.input or next(Path('Flight_Data').rglob('*starnet.stn'))
    out = args.output
    if source.parent.resolve() == out.resolve() or source.parent.resolve() in out.resolve().parents:
        parser.error('Write outputs outside the source flight folder')
    out.mkdir(parents=True, exist_ok=True)
    schema_dir = out / 'starnet_schemas_verified'
    schema_dir.mkdir(exist_ok=True)
    data = source.read_bytes()
    assert data[0] == 0xAA and data[-1] == 0x55
    specs = {
        (14, 9): ('stn_camera_pitch.csv', ['log_time_us', 'camPitch']),
        (14, 4): ('stn_camera_state.csv', ['log_time_us', 'cameraPitch_deg', 'recording_time_s', 'recording_status', 'resolution', 'image_orientation', 'iso', 'exposure_us']),
        (14, 2): ('stn_video_frame_sync.csv', ['log_time_us', 'frame_index', 'frame_time_us']),
        (14, 1): ('stn_video_offset.csv', ['log_time_us', 'offset_us']),
        (18, 2): ('stn_tof.csv', ['log_time_us', 'range_mm', 'qualified_status', 'sigma_mm', 'range_status', 'sensor_time_us']),
    }
    handles = {k: (out / v[0]).open('w', encoding='utf-8', newline='') for k, v in specs.items()}
    writers = {k: csv.writer(h) for k, h in handles.items()}
    for k, (_, fields) in specs.items():
        writers[k].writerow(fields)
    counts = Counter()
    schemas = {}
    bad = []
    crc_ok = 0
    header_counts = Counter()
    opaque_transport_records = 0
    samples = {}
    try:
        for index, escaped in enumerate(data[1:-1].split(b'\x55\xaa')):
            try:
                block = unescape(escaped)
            except (KeyError, IndexError):
                bad.append({'record': index, 'reason': 'unknown escape'})
                continue
            if binascii.crc_hqx(block[:-2], 0xFFFF) != int.from_bytes(block[-2:], 'little'):
                bad.append({'record': index, 'reason': 'CRC mismatch', 'flags': block[0],
                            'decoded_bytes': len(block), 'prefix_hex': block[:16].hex()})
                continue
            crc_ok += 1
            flags = block[0]
            header_counts[flags] += 1
            if flags == 0 and block[1:3] == b'\xff\xff':
                schema = json.loads(block[3:-2].decode('utf-8'))
                schemas[schema['id']] = schema
                (schema_dir / (schema['name'] + '.json')).write_text(json.dumps(schema, indent=2), encoding='utf-8')
                continue
            if flags not in (2, 6, 7, 14):
                bad.append({'record': index, 'reason': f'unsupported flags {flags}'})
                continue
            # Reliable/acknowledgment envelopes need additional protocol handling.
            # None of the selected measurement streams uses these envelopes.
            if flags in (7, 14):
                opaque_transport_records += 1
                continue
            time_us = int.from_bytes(block[1:6], 'little')
            cursor = 6 + (3 if flags & 4 else 0) + (2 if flags & 9 else 0)
            family, message = block[cursor:cursor + 2]
            payload = block[cursor + 2:-2]
            key = (family, message)
            counts[key] += 1
            if key not in samples:
                samples[key] = {'time_us': time_us, 'payload_bytes': len(payload), 'payload_hex': payload[:64].hex()}
            if key not in specs:
                continue
            if key == (14, 9):
                assert len(payload) == 4
                row = [time_us, struct.unpack('<f', payload)[0]]
            elif key == (14, 4):
                assert len(payload) == 31
                row = [time_us, struct.unpack_from('<b', payload, 9)[0],
                       struct.unpack_from('<H', payload, 13)[0], payload[11] & 15,
                       payload[15], payload[30], struct.unpack_from('<H', payload, 2)[0],
                       struct.unpack_from('<H', payload, 4)[0]]
            elif key == (14, 2):
                assert len(payload) == 12
                row = [time_us, *struct.unpack('<IQ', payload)]
            elif key == (14, 1):
                assert len(payload) == 8
                row = [time_us, struct.unpack('<q', payload)[0]]
            elif key == (18, 2):
                assert len(payload) == 14
                row = [time_us, *struct.unpack('<hBHBQ', payload)]
            writers[key].writerow(row)
    finally:
        for handle in handles.values():
            handle.close()
    inventory = []
    for (family, message), count in sorted(counts.items()):
        schema = schemas.get(family, {})
        name = next((m['name'] for m in schema.get('messages', []) if m['id'] == message), 'UNDEFINED')
        inventory.append({'family_id': family, 'family': schema.get('name'), 'message_id': message,
                          'message': name, 'count': count, 'first_sample': samples[(family, message)]})
    result = {'crc_valid_records': crc_ok, 'bad_records': bad, 'schema_count': len(schemas),
              'opaque_transport_records': opaque_transport_records,
              'scope': 'CRC-checked schema records and normal timestamped messages (flags 2/6); reliable/ACK envelopes (7/14) are inventoried as opaque.',
              'header_flags_counts': dict(header_counts), 'messages': inventory}
    (out / 'starnet_inventory.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{crc_ok:,} CRC-valid records; {len(bad)} failures; {len(schemas)} schemas')
    for item in inventory:
        if item['family_id'] in (14, 18):
            print(item['family'], item['message'], item['count'])
    if bad:
        print('Non-passing records excluded; see starnet_inventory.json. Cross-validate selected streams against MCAP.')


if __name__ == '__main__':
    main()
