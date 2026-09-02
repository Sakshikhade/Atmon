"""Unit and integration tests for scripts/reset-local.sh."""

import subprocess
from pathlib import Path


def test_reset_script_wipes_outbox_db(tmp_path: Path):
    """Test that running reset-local.sh removes data/outbox.db while leaving logs."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "reset-local.sh"
    assert script_path.exists()

    # Create dummy database and log file in a mock repo structure
    mock_root = tmp_path / "repo"
    mock_scripts = mock_root / "scripts"
    mock_data = mock_root / "data"
    mock_scripts.mkdir(parents=True)
    mock_data.mkdir(parents=True)

    dummy_db = mock_data / "outbox.db"
    dummy_db.write_text("dummy sqlite db content")
    dummy_log = mock_data / "behavior_log.csv"
    dummy_log.write_text("frame,timestamp,label\n")

    # Copy script to mock repo
    test_script = mock_scripts / "reset-local.sh"
    test_script.write_text(script_path.read_text())
    test_script.chmod(0o755)

    # Run default reset
    result = subprocess.run([str(test_script)], capture_output=True, text=True, check=True)
    assert result.returncode == 0
    assert not dummy_db.exists()
    assert dummy_log.exists()
    assert "removed local database: data/outbox.db" in result.stdout


def test_reset_script_all_flag_wipes_everything(tmp_path: Path):
    """Test that running reset-local.sh --all removes both database and log files."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "reset-local.sh"

    mock_root = tmp_path / "repo"
    mock_scripts = mock_root / "scripts"
    mock_data = mock_root / "data"
    mock_scripts.mkdir(parents=True)
    mock_data.mkdir(parents=True)

    dummy_db = mock_data / "outbox.db"
    dummy_db.write_text("dummy sqlite db content")
    dummy_log = mock_data / "behavior_log.csv"
    dummy_log.write_text("frame,timestamp,label\n")

    test_script = mock_scripts / "reset-local.sh"
    test_script.write_text(script_path.read_text())
    test_script.chmod(0o755)

    # Run with --all
    result = subprocess.run([str(test_script), "--all"], capture_output=True, text=True, check=True)
    assert result.returncode == 0
    assert not dummy_db.exists()
    assert not dummy_log.exists()
    assert "removed local behavior log: data/behavior_log.csv" in result.stdout


def test_reset_script_help():
    """Test that running reset-local.sh --help outputs usage information."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "reset-local.sh"
    result = subprocess.run([str(script_path), "--help"], capture_output=True, text=True, check=True)
    assert result.returncode == 0
    assert "Usage:" in result.stdout
