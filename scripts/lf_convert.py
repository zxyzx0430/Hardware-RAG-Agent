from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_bytes().replace(b"\r\n", b"\n")
p.write_bytes(text)
print("LF" if b"\r\n" not in p.read_bytes() else "CRLF")
