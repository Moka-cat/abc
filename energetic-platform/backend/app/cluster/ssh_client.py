from __future__ import annotations

import paramiko
from pathlib import Path


class ClusterSSHClient:
    def __init__(
        self,
        host: str,
        username: str,
        private_key_path: str,
        port: int = 22,
    ) -> None:
        self.host = host
        self.username = username
        self.private_key_path = private_key_path
        self.port = port

    def execute(self, command: str) -> tuple[int, str, str]:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.private_key_path,
            timeout=20,
        )
        try:
            _, stdout, stderr = client.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            return exit_code, stdout.read().decode(), stderr.read().decode()
        finally:
            client.close()

    def put_directory(self, local_directory: Path, remote_directory: str) -> None:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.private_key_path,
            timeout=20,
        )
        try:
            sftp = client.open_sftp()
            self._mkdir_p(sftp, remote_directory)
            for path in local_directory.iterdir():
                if path.is_file():
                    sftp.put(str(path), f"{remote_directory.rstrip('/')}/{path.name}")
            sftp.close()
        finally:
            client.close()

    @staticmethod
    def _mkdir_p(sftp: paramiko.SFTPClient, remote_directory: str) -> None:
        parts = [part for part in remote_directory.split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)
