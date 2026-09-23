"""Read ESP32-S3 serial output"""
import serial
import time

port = 'COM5'
baud = 115200

ser = None
try:
    ser = serial.Serial(port, baud, timeout=3)
    print(f"[OK] Opened {port} @ {baud}")

    time.sleep(1)

    data = b''
    start = time.time()
    while time.time() - start < 5:
        try:
            chunk = ser.read(500)
            if chunk:
                data += chunk
                print(f"[+] Got {len(chunk)} bytes")
                start = time.time()
            else:
                if data:
                    break
                time.sleep(0.5)
        except Exception as e:
            print(f"[-] Read error: {e}")
            break

    if data:
        print(f"\n=== DATA ({len(data)} bytes) ===")
        print(data.decode('utf-8', errors='replace'))
        print("=== END ===")
    else:
        print("[-] No data received")

except serial.SerialException as e:
    print(f"[-] Serial error: {e}")
except Exception as e:
    print(f"[-] Error: {e}")
finally:
    if ser:
        try:
            ser.close()
        except Exception:
            pass
