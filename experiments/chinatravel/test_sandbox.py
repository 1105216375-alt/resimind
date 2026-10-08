"""Real OS permission probes using temporary public/private fixtures only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from experiments.chinatravel.sandbox import build_worker_command, profile_for_worker


@unittest.skipUnless(sys.platform == "darwin", "macOS seatbelt test")
class SandboxTests(unittest.TestCase):
    def test_real_worker_permissions(self):
        with tempfile.TemporaryDirectory(prefix="chinatravel-sandbox-") as temporary:
            root = Path(temporary)
            upstream, repo, output = root / "upstream", root / "public", root / "output"
            database = upstream / "chinatravel/environment/database"
            for path in (database, repo / "src", repo / "experiments", output,
                         upstream / "data", root / "private"):
                path.mkdir(parents=True)
            query = root / "public-query.json"
            query.write_text('{"query":"public fixture"}')
            gold, private = upstream / "data/gold.json", root / "private/config.json"
            gold.write_text("fixture gold, never read")
            private.write_text("fixture config, never read")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                arguments = (sys.executable, upstream, repo, output, query, database, port)
                profile = profile_for_worker(*arguments)
                command = build_worker_command(sys.executable, ["--probe"], *arguments[1:])
                assert command[:3] == ["/usr/bin/sandbox-exec", "-p", profile]
                assert command[4:] == ["-m", "experiments.chinatravel.upstream_worker", "--probe"]
                probe = r'''
import errno,json,os,socket,sys
from pathlib import Path
query,output,gold,private,port = sys.argv[1:]
checks={"public_read":json.loads(Path(query).read_text())["query"]=="public fixture"}
Path(output,"allowed.txt").write_text("allowed")
checks["output_write"]=True
for name,path in (("gold_denied",gold),("private_denied",private)):
    try:
        fd=os.open(path,os.O_RDONLY)
        os.close(fd)
        checks[name]=False
    except PermissionError:
        checks[name]=True
try:
    Path(private).write_text("must never be written")
    checks["private_write_denied"]=False
except PermissionError:
    checks["private_write_denied"]=True
with socket.socket() as connection:
    connection.settimeout(2)
    connection.connect(("127.0.0.1",int(port)))
    checks["ipc_allowed"]=True
for name,address in (("external_denied",("1.1.1.1",443)),("other_port_denied",("127.0.0.1",1))):
    with socket.socket() as connection:
        connection.settimeout(2)
        try:
            result=connection.connect_ex(address)
            checks[name]=result in (errno.EPERM,errno.EACCES)
        except PermissionError:
            checks[name]=True
print(json.dumps(checks))
'''
                result = subprocess.run(command[:4] + ["-I", "-S", "-c", probe, str(query),
                                        str(output), str(gold), str(private), str(port)],
                                        capture_output=True, text=True, timeout=20,
                                        cwd=output, env={"PATH": "/usr/bin:/bin", "HOME": str(output)},
                                        close_fds=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                checks = json.loads(result.stdout)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(private.read_text(), "fixture config, never read")


if __name__ == "__main__":
    unittest.main()
