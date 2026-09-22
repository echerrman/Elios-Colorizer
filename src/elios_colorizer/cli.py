"""Reproducible development and batch interface; the desktop needs no CLI."""
import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser(description='Local Elios point-cloud colorization')
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='Print source and dependency readiness')
    run = commands.add_parser('colorize', help='Write a new colorized LAS')
    for command in (inspect, run):
        command.add_argument('folder')
        command.add_argument('--las', dest='las_override')
        command.add_argument('--calibration', dest='calibration_override')
    run.add_argument('output')
    run.add_argument('--interval', dest='sample_interval_s', type=float, default=1.0)
    run.add_argument('--max-frames', type=int)
    run.add_argument('--start', dest='start_s', type=float, help='Elapsed seconds from video start')
    run.add_argument('--end', dest='end_s', type=float)
    run.add_argument('--experimental', action='store_true', help='Diagnostic output with an unvalidated calibration; do not treat as reliable colorization')
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        from . import service
        if command == 'inspect':
            print(json.dumps(service.inspect_source(**args), indent=2))
        else:
            if args['sample_interval_s'] <= 0 or (args['max_frames'] is not None and args['max_frames'] < 1):
                parser.error('Interval and max-frames must be positive.')
            result = service.run_colorization(**args, progress=lambda stage, fraction, message:
                                               print(f'{fraction:6.1%} {stage}: {message}', flush=True))
            print(json.dumps(result, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
