import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LA_ENV_EXAMPLE = (
    REPO_ROOT / "ops" / "systemd" / "user" / "transit-sentinel-lametro.env.example"
)


def test_lametro_env_example_has_safe_secret_and_archive_defaults():
    values = {}
    for raw_line in LA_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value

    assert "SWIFTLY_API_KEY" in values
    assert values["SWIFTLY_API_KEY"] == ""
    assert values["TRANSIT_ARCHIVE_CURRENT_ONLY"] == "1"
    assert values["TRANSIT_ARCHIVE_RETENTION_DAYS"] == "90"


def test_runtime_env_is_ignored_without_ignoring_example():
    runtime_env = "ops/systemd/user/transit-sentinel-lametro.env"
    example_env = f"{runtime_env}.example"

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", runtime_env],
        cwd=REPO_ROOT,
        check=False,
    )
    example = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", example_env],
        cwd=REPO_ROOT,
        check=False,
    )

    assert ignored.returncode == 0
    assert example.returncode == 1


def test_runtime_env_is_excluded_from_docker_build_context():
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "transit-sentinel-*.env" in dockerignore.splitlines()


def test_live_compose_is_explicitly_lametro_only():
    live_compose = (REPO_ROOT / "docker-compose.live-host.yml").read_text(
        encoding="utf-8"
    )

    assert 'TRANSIT_AGENCY: "lametro"' in live_compose
    assert live_compose.count('TRANSIT_AGENCY: "lametro"') == 3
    assert 'TRANSIT_SYSTEM_NAME: "LA Metro Live"' in live_compose
    assert 'TRANSIT_REPLAY_ENABLED: "1"' in live_compose
    assert "MBTA Live" not in live_compose


def test_live_deploy_seeds_only_curated_lametro_replay_without_clearing_live():
    deploy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert "--skip-live" in deploy
    assert "--clear-store" not in deploy
    assert "--replay-case-pack-catalog /app/data/case-packs/lametro" in deploy
    assert "casepack-lametro-saturday-mixed-alert-controls" in deploy
    assert "casepack-lametro-weekday-bus-instability-sequence" in deploy
