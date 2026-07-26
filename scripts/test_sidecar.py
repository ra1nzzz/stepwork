"""Quick test: spawn the sidecar EXE, verify ready + health_check, then shutdown."""

import json
import os
import struct
import subprocess
import sys
import threading

os.environ["STEPWORK_HOME"] = os.path.join(
    os.environ.get("TEMP", "/tmp"), "stepwork-test-sidecar"
)

if len(sys.argv) > 1:
    cmd = sys.argv[1:]
else:
    cmd = [
        r"d:\Code\STEPWORK\apps\desktop\src-tauri\binaries"
        r"\stepwork-worker-x86_64-pc-windows-msvc.exe"
    ]

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)


def send(method, params=None, req_id=None):
    req = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if req_id is not None:
        req["id"] = req_id
    body = json.dumps(req).encode("utf-8")
    proc.stdin.write(struct.pack(">I", len(body)) + body)
    proc.stdin.flush()


def read_stdout():
    try:
        while True:
            header = proc.stdout.read(4)
            if len(header) < 4:
                print("STDOUT: stream closed")
                break
            length = struct.unpack(">I", header)[0]
            payload = proc.stdout.read(length)
            frame = json.loads(payload)
            if frame.get("method"):
                print(f"STDOUT: notification {frame['method']}")
                if frame["method"] == "runtime.ready":
                    print(f"  pid={frame['params']['pid']}")
                    send("runtime.health_check", {}, req_id="1")
            elif frame.get("id"):
                print(f"STDOUT: response id={frame['id']}")
                if frame.get("result"):
                    print(f"  result={json.dumps(frame['result'], indent=2)}")
                if frame["id"] == "1":
                    send("runtime.shutdown", {}, req_id="2")
            else:
                print(f"STDOUT: unknown {frame}")
    except Exception as e:
        print(f"STDOUT ERROR: {e}")


def read_stderr():
    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            print(f"STDERR: {line.decode('utf-8', errors='replace').strip()}")
    except Exception as e:
        print(f"STDERR ERROR: {e}")


t1 = threading.Thread(target=read_stdout, daemon=True)
t2 = threading.Thread(target=read_stderr, daemon=True)
t1.start()
t2.start()

try:
    proc.wait(timeout=30)
    print(f"EXIT CODE: {proc.returncode}")
except subprocess.TimeoutExpired:
    proc.kill()
    print("TIMEOUT: killed after 30s")
