import sys
import os
import argparse

def main():
    parser = argparse.ArgumentParser(description='Write content to a file')
    parser.add_argument('target', help='Target file path')
    parser.add_argument('--from-file', dest='from_file', help='Read content from source file instead of stdin')
    parser.add_argument('--append', action='store_true', help='Append to file instead of overwrite')
    parser.add_argument('--encoding', default='utf-8', help='File encoding (default: utf-8)')
    args = parser.parse_args()

    if args.from_file:
        with open(args.from_file, 'r', encoding=args.encoding) as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    content = content.replace('\r\n', '\n')

    mode = 'a' if args.append else 'w'
    os.makedirs(os.path.dirname(os.path.abspath(args.target)), exist_ok=True)
    with open(args.target, mode, encoding=args.encoding) as f:
        f.write(content)

    print(f'OK: {args.target} ({chr(39) + 'appended' + chr(39) if args.append else chr(39) + 'written' + chr(39)}, {len(content)} chars)')

if __name__ == '__main__':
    main()
