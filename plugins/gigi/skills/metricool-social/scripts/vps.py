# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""VPS helper: run commands / put files over SSH (password from KB, never printed)."""
import subprocess, sys, os, paramiko
KB="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
def sec(k): return subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get {k}"],capture_output=True,text=True).stdout.strip()
HOST=sec("PROFIT_SSH_HOST"); USER=sec("PROFIT_SSH_USER"); PW=sec("PROFIT_SSH_PASS")
def client():
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST,username=USER,password=PW,timeout=25); return c
def run(cmd):
    c=client(); _,out,err=c.exec_command(cmd,timeout=120)
    o=out.read().decode(); e=err.read().decode(); c.close()
    return o,e
def put(local,remote):
    c=client(); sf=c.open_sftp(); sf.put(local,remote); sf.close(); c.close()
def get(remote,local):
    c=client(); sf=c.open_sftp(); sf.get(remote,local); sf.close(); c.close()
if __name__=="__main__":
    if sys.argv[1]=="run":
        o,e=run(sys.argv[2]); print(o); print(e,file=sys.stderr)
    elif sys.argv[1]=="put":
        put(sys.argv[2],sys.argv[3]); print("put ok",sys.argv[3])
    elif sys.argv[1]=="get":
        get(sys.argv[2],sys.argv[3]); print("get ok",sys.argv[3])
