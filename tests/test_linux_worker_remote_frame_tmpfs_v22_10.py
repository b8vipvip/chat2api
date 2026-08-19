from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_remote_login_capture_uses_writable_memory_backed_tempdir():
    source = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    assert 'LOGIN_TMPDIR = os.environ.get("CHAT2API_LOGIN_TMPDIR", "/dev/shm")' in source
    assert 'env["TMPDIR"] = LOGIN_TMPDIR' in source
    assert 'env["MAGICK_TMPDIR"] = LOGIN_TMPDIR' in source
    assert '"convert", "xwd:-"' in source


def test_remote_login_tmpdir_is_not_disk_persistent_by_default():
    source = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    assert '"/dev/shm"' in source
    assert 'CHAT2API_LOGIN_TMPDIR' in source
