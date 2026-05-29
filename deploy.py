import argparse
import glob
import os
import sys
import time

from paramiko import SSHClient, AutoAddPolicy
from scp import SCPClient

HOST = "43.165.166.57"
USER = "ubuntu"
SSH_KEY = os.path.join(".tmp-ssh", "futunsystemv3_deploy_ed25519")
SERVER_PATH = "/home/ubuntu/furunsystemv4/current"
SERVICE = "furun-api"
SSH_KEY_ABS = os.path.abspath(SSH_KEY)


def log(msg):
    print(msg, flush=True)


def make_client(timeout=15):
    c = SSHClient()
    c.set_missing_host_key_policy(AutoAddPolicy())
    c.connect(hostname=HOST, username=USER, key_filename=SSH_KEY_ABS, timeout=timeout)
    return c


def ssh_exec(remote_cmd, timeout=30):
    log(f"  [SSH] {remote_cmd[:100]}")
    t0 = time.time()
    client = make_client(timeout=timeout)
    try:
        _stdin, stdout, stderr = client.exec_command(remote_cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        rc = stdout.channel.recv_exit_status()
        elapsed = time.time() - t0
        log(f"  <<< rc={rc} ({elapsed:.1f}s)")
        if out:
            for line in out.splitlines()[:10]:
                log(f"      {line}")
        if err and rc != 0:
            for line in err.splitlines()[:5]:
                log(f"      stderr: {line}")
        return rc == 0
    finally:
        client.close()


def scp_upload(local_path, remote_path, recurse=False, timeout=60):
    log(f"  [SCP] {local_path} -> {remote_path}")
    t0 = time.time()
    client = make_client(timeout=timeout)
    try:
        with SCPClient(client.get_transport()) as scp:
            scp.put(local_path, remote_path, recursive=recurse)
        elapsed = time.time() - t0
        log(f"  <<< OK ({elapsed:.1f}s)")
        return True
    except Exception as e:
        log(f"  *** FAIL: {e}")
        return False
    finally:
        client.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-frontend", action="store_true")
    ap.add_argument("--skip-backend", action="store_true")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    log(f"[INFO] Working dir: {os.getcwd()}")

    if not args.skip_frontend:
        log("[BUILD] Building frontend...")
        r = os.system("npm run build --prefix web")
        if r != 0:
            log("[ERR] Frontend build failed")
            sys.exit(1)

        log("[DEPLOY] Uploading frontend...")
        scp_upload("web/dist/index.html", f"{SERVER_PATH}/web/dist/")
        ssh_exec(f"rm -f {SERVER_PATH}/web/dist/assets/index-*", timeout=10)
        for f in glob.glob("web/dist/assets/*"):
            scp_upload(f, f"{SERVER_PATH}/web/dist/assets/")

    if not args.skip_backend:
        log("[DEPLOY] Uploading backend...")
        if not scp_upload("app", f"{SERVER_PATH}/", recurse=True, timeout=180):
            log("[ERR] Backend upload failed")
            sys.exit(1)

    log(f"[RESTART] Restarting {SERVICE}...")
    ssh_exec(f"find {SERVER_PATH} -name '*.pyc' -delete", timeout=20)
    ssh_exec(f"sudo systemctl stop {SERVICE} &", timeout=10)
    ssh_exec(f"sleep 3 && sudo systemctl kill {SERVICE} 2>/dev/null; sudo systemctl reset-failed {SERVICE} 2>/dev/null; sudo systemctl start {SERVICE}", timeout=60)
    log("  Waiting 8s...")
    time.sleep(8)

    log("[CHECK] curl health...")
    ok = ssh_exec("curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/", timeout=10)

    if ok:
        log("[OK] Deploy successful!")
    else:
        log("[ERR] Health check failed")
        log("[ERR] Checking journal...")
        ssh_exec("sudo journalctl -u furun-api --no-pager -n 10")
        sys.exit(1)


if __name__ == "__main__":
    main()
