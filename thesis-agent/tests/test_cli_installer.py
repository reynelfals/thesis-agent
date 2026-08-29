from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_installer_replaces_wrong_version_in_runtime_target(tmp_path) -> None:
    install_dir = tmp_path / "runtime-bin"
    install_dir.mkdir()
    _executable(install_dir / "alpaca", "#!/bin/sh\necho v0.0.12\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "go",
        """#!/bin/sh
set -eu
test "$1" = "install"
test "$2" = "github.com/alpacahq/cli/cmd/alpaca@v0.0.13"
cat > "$GOBIN/alpaca" <<'EOF'
#!/bin/sh
echo v0.0.13
EOF
chmod +x "$GOBIN/alpaca"
""",
    )
    script = Path(__file__).parents[1] / "scripts" / "install_alpaca_cli.sh"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["ALPACA_CLI_INSTALL_DIR"] = str(install_dir)

    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v0.0.13"
    assert subprocess.check_output(
        [str(install_dir / "alpaca"), "version"],
        text=True,
    ).strip() == "v0.0.13"