"""Exercise pinned Caddy HTTPS locally without changing the running stack/trust store."""

import argparse
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.integration")
    parser.add_argument("--network", default="archive-desk_default")
    args = parser.parse_args()
    values = dotenv_values(root / args.env_file, encoding="utf-8-sig")
    # This helper uses the existing development gateway credential, never a
    # command-line password, and does not print it or the expanded config.
    if not values.get("DASHBOARD_TEST_PASSWORD"):
        raise SystemExit(
            "Use a private local test environment containing DASHBOARD_TEST_PASSWORD"
        )
    project = "archive-tls-" + uuid.uuid4().hex[:12]
    assert re.fullmatch(r"archive-tls-[a-f0-9]{12}", project)
    scratch = (root / ".tls-validation").resolve()
    assert scratch.is_relative_to(root.resolve())
    scratch.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=scratch) as temporary:
        directory = Path(temporary).resolve()
        assert directory.is_relative_to(scratch)
        configuration = (root / "infra/Caddyfile").read_text(encoding="utf-8")
        marker = "{$SITE_ADDRESS} {"
        if configuration.count(marker) != 1:
            raise SystemExit(
                "The shared Caddy site block changed; review the TLS test adapter"
            )
        caddyfile = directory / "Caddyfile"
        caddyfile.write_text(
            configuration.replace(marker, marker + "\n  tls internal", 1),
            encoding="utf-8",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {
            **os.environ,
            "TLS_TEST_PORT": str(port),
            "TLS_TEST_CADDYFILE": caddyfile.as_posix(),
            "ARCHIVE_NETWORK": args.network,
        }
        compose = [
            "docker",
            "compose",
            "--env-file",
            args.env_file,
            "-p",
            project,
            "-f",
            "compose.tls-test.yaml",
        ]

        def run(*arguments, check=True):
            return subprocess.run(
                [*compose, *arguments],
                cwd=root,
                env=environment,
                check=check,
                capture_output=True,
                text=True,
            )

        def diagnostic():
            result = run("logs", "--no-color", "--tail", "6", "proxy-tls", check=False)
            message = result.stdout + result.stderr
            for value in values.values():
                if value and len(value) >= 8:
                    message = message.replace(value, "<redacted>")
            return message[-1800:]

        try:
            run("up", "-d")
            certificate = directory / "root.crt"
            for _ in range(30):
                # Read only the public CA certificate, never its private key.
                # exec also works for the proxy's temporary in-memory storage.
                copied = run(
                    "exec",
                    "-T",
                    "proxy-tls",
                    "cat",
                    "/data/caddy/pki/authorities/local/root.crt",
                    check=False,
                )
                if copied.returncode == 0 and "BEGIN CERTIFICATE" in copied.stdout:
                    certificate.write_text(copied.stdout, encoding="ascii")
                    break
                if (
                    run(
                        "ps", "-a", "--format", "{{.State}}", "proxy-tls"
                    ).stdout.strip()
                    != "running"
                ):
                    raise RuntimeError("The disposable proxy stopped: " + diagnostic())
                time.sleep(1)
            else:
                raise RuntimeError(
                    "The disposable proxy did not create its local certificate: "
                    + copied.stderr[-300:]
                    + diagnostic()
                )
            context = ssl.create_default_context(cafile=str(certificate))
            with socket.create_connection(
                ("127.0.0.1", port), timeout=10
            ) as connection:
                with context.wrap_socket(
                    connection, server_hostname="localhost"
                ) as secured:
                    protocol = secured.version()
                    assert protocol in {"TLSv1.2", "TLSv1.3"}
            base = f"https://localhost:{port}"
            with httpx.Client(
                base_url=base, verify=context, timeout=20, trust_env=False
            ) as client:
                assert client.get("/api/v1/accounts").status_code == 401
                assert (
                    client.get(
                        "/api/v1/accounts",
                        auth=(values.get("DASHBOARD_USER", "owner"), "wrong-password"),
                    ).status_code
                    == 401
                )
                auth = (
                    values.get("DASHBOARD_USER", "owner"),
                    values["DASHBOARD_TEST_PASSWORD"],
                )
                response = client.get(
                    "/api/v1/accounts",
                    auth=auth,
                    headers={"X-Internal-Token": "forged-client-token"},
                )
                response.raise_for_status()
                assert response.json()["items"]
                mock = next(
                    item for item in response.json()["items"] if item["mode"] == "mock"
                )
                rejected = client.patch(
                    f"/api/v1/accounts/{mock['id']}/pause",
                    auth=auth,
                    headers={"Origin": "https://invalid.example"},
                    json={"paused": True},
                )
                assert rejected.status_code == 403
                page = client.get("/", auth=auth)
                page.raise_for_status()
                assert page.headers["x-content-type-options"] == "nosniff"
                assert page.headers["x-frame-options"] == "DENY"
                assert page.headers["referrer-policy"] == "same-origin"
            print(
                f"PASS: locally trusted {protocol}, HTTPS owner auth, internal-token "
                "replacement, Origin rejection, security headers and dashboard routing"
            )
        finally:
            # This generated project owns only the disposable proxy; its network
            # is external and never removed. The host trust store is untouched.
            cleaned = run("down", check=False)
            assert directory.resolve().is_relative_to(scratch)
            if cleaned.returncode:
                raise RuntimeError(
                    f"Remove the disposable TLS project manually: {project}"
                )


if __name__ == "__main__":
    main()
