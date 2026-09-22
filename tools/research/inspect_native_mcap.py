"""Extract colorization telemetry from the supplied Inspector ROS 2 MCAP.

Requires mcap and mcap-ros2-support, optionally installed in .local_deps.
Input is read-only. Output contains original clock values; no clock correction
or assumption about servo-angle units/sign is silently applied.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.local_deps'))
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory


def stamp_ns(stamp):
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('analysis/native_flight'))
    args = parser.parse_args()
    source = args.input or next(Path('Flight_Data').rglob('*.mcap'))
    out = args.output
    if source.parent.resolve() == out.resolve() or source.parent.resolve() in out.resolve().parents:
        parser.error('Write outputs outside the source flight folder')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'mcap_schemas').mkdir(exist_ok=True)
    topics = {
        '/sensors/servo_angle': ('servo_angle.csv', ['log_time_ns', 'publish_time_ns', 'servo_angle_value']),
        '/sensors/tof': ('tof.csv', ['log_time_ns', 'publish_time_ns', 'sensor_time_ns', 'frame_id', 'range_mm', 'range_sigma_mm', 'range_status']),
        '/kalman_scan2map_node/odometry': ('lidar_odometry.csv', ['log_time_ns', 'publish_time_ns', 'pose_time_ns', 'parent_frame', 'child_frame', 'x_m', 'y_m', 'z_m', 'qx', 'qy', 'qz', 'qw', 'confidence']),
        '/kalman_scan2map_node/fast_odometry': ('lidar_odometry_fast.csv', ['log_time_ns', 'publish_time_ns', 'pose_time_ns', 'parent_frame', 'child_frame', 'x_m', 'y_m', 'z_m', 'qx', 'qy', 'qz', 'qw', 'confidence']),
    }
    files = {t: (out / spec[0]).open('w', newline='', encoding='utf-8') for t, spec in topics.items()}
    writers = {t: csv.writer(handle) for t, handle in files.items()}
    for t, (_, columns) in topics.items():
        writers[t].writerow(columns)
    counts = Counter()
    tf_pairs = {}
    samples = {}
    more = ['/tf', '/camera_0/image_raw/compressed', '/os_node/lidar_scan/compressed', '/os_node/lidar_scan_mask2d']
    factory = DecoderFactory()
    try:
        with source.open('rb') as handle:
            reader = make_reader(handle, validate_crcs=True, decoder_factories=[factory])
            summary = reader.get_summary()
            channels = []
            for cid, channel in summary.channels.items():
                schema = summary.schemas.get(channel.schema_id)
                channels.append({'id': cid, 'topic': channel.topic, 'schema_id': channel.schema_id,
                                 'schema': schema.name if schema else None,
                                 'encoding': channel.message_encoding,
                                 'count': summary.statistics.channel_message_counts.get(cid, 0)})
            for sid, schema in summary.schemas.items():
                name = schema.name.replace('/', '__')
                (out / 'mcap_schemas' / f'{sid}_{name}.msg').write_bytes(schema.data)
            inventory = {'header': vars(reader.get_header()), 'statistics': vars(summary.statistics),
                         'defined_channels': len(channels), 'defined_schemas': len(summary.schemas),
                         'channels': channels, 'chunk_compressions': sorted({c.compression for c in summary.chunk_indexes})}
            (out / 'mcap_inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
            print('Extracting telemetry and checking TF frames...', flush=True)
            # Decode every telemetry message; decode only first image/lidar sample.
            for schema, channel, message in reader.iter_messages(topics=list(topics) + more, log_time_order=False):
                topic = channel.topic
                counts[topic] += 1
                if topic in more and topic != '/tf' and topic in samples:
                    continue
                decoder = factory.decoder_for(channel.message_encoding, schema)
                decoded = decoder(message.data)
                prefix = [message.log_time, message.publish_time]
                if topic == '/sensors/servo_angle':
                    writers[topic].writerow(prefix + [decoded.data])
                elif topic == '/sensors/tof':
                    writers[topic].writerow(prefix + [stamp_ns(decoded.header.stamp), decoded.header.frame_id,
                                                    decoded.range, decoded.range_sigma, decoded.range_status])
                elif topic in topics:
                    p, q = decoded.pose.position, decoded.pose.orientation
                    writers[topic].writerow(prefix + [stamp_ns(decoded.header.stamp), decoded.header.frame_id,
                                                     decoded.child_frame_id, p.x, p.y, p.z, q.x, q.y, q.z, q.w,
                                                     int(decoded.confidence)])
                elif topic == '/tf':
                    for transform in decoded.transforms:
                        pair = transform.header.frame_id + ' -> ' + transform.child_frame_id
                        if pair not in tf_pairs:
                            tf_pairs[pair] = {'count': 0, 'first': str(transform), 'first_stamp_ns': stamp_ns(transform.header.stamp)}
                        tf_pairs[pair]['count'] += 1
                        tf_pairs[pair]['last_stamp_ns'] = stamp_ns(transform.header.stamp)
                elif topic == '/camera_0/image_raw/compressed':
                    image_bytes = bytes(decoded.data)
                    (out / 'vio_camera_0_first_image.jpg').write_bytes(image_bytes)
                    samples[topic] = {'format': decoded.format, 'header': str(decoded.header),
                                      'bytes': len(image_bytes), 'signature': image_bytes[:16].hex()}
                elif topic == '/os_node/lidar_scan/compressed':
                    buf = bytes(decoded.buf)
                    samples[topic] = {'bytes': len(buf), 'signature': buf[:64].hex()}
                elif topic == '/os_node/lidar_scan_mask2d':
                    samples[topic] = {'rows': decoded.rows, 'cols': decoded.cols, 'stamp_ns': stamp_ns(decoded.stamp),
                                      'data_bytes': len(decoded.data), 'reserved': decoded.reserved}
                if sum(counts.values()) % 50000 == 0:
                    print(f'Read {sum(counts.values()):,} selected records', flush=True)
            for channel in channels:
                if channel['topic'] in list(topics) + more:
                    assert counts[channel['topic']] == channel['count'], channel['topic']
            (out / 'extraction_checks.json').write_text(json.dumps({'counts': dict(counts), 'tf_pairs': tf_pairs,
                                                                  'samples': samples, 'all_selected_counts_match_summary': True}, indent=2), encoding='utf-8')
    finally:
        for handle in files.values():
            handle.close()
    print('Extraction complete; record counts match MCAP summary.', flush=True)


if __name__ == '__main__':
    main()
