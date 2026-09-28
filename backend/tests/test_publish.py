"""Publishing must detect secrets even after a subsequent commit removes them."""
import subprocess

import pytest

from scripts.verify_publish import verify


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test User')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    (tmp_path / 'README.md').write_text('Demo project\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'initial')
    return tmp_path


def test_removed_secret_is_still_blocked(repository):
    secret = b'fixture-' + b'x' * 24
    path = repository / 'plugin.txt'
    path.write_bytes(secret)
    git(repository, 'add', '.')
    git(repository, 'commit', '-m', 'add fixture')
    path.unlink()
    git(repository, 'add', '-u')
    git(repository, 'commit', '-m', 'remove fixture')
    with pytest.raises(RuntimeError) as error:
        verify(repository, known={secret})
    assert 'Git object' in str(error.value)
    assert secret.decode() not in str(error.value)


def test_staged_secret_is_blocked_after_working_file_is_cleaned(repository):
    secret = b'staged-' + b'y' * 24
    path = repository / 'plugin.txt'
    path.write_bytes(secret)
    git(repository, 'add', '.')
    path.write_text('Clean working file\n')
    with pytest.raises(RuntimeError):
        verify(repository, known={secret})


def test_login_file_and_key_patterns_are_blocked(repository):
    path = repository / 'cookies.json'
    path.write_text('{}')
    git(repository, 'add', '.')
    with pytest.raises(RuntimeError, match='local data file'):
        verify(repository, known=set())
    git(repository, 'reset', 'HEAD', '--', 'cookies.json')
    path.unlink()
    path = repository / 'plugin.txt'
    path.write_text('github_' + 'pat_' + 'z' * 60)
    git(repository, 'add', '.')
    with pytest.raises(RuntimeError, match='GitHub credential'):
        verify(repository, known=set())


def test_empty_example_configuration_is_allowed(repository):
    (repository / '.env.example').write_text('AUTOMATION_API_TOKEN=\n# Example only\nWORKBUDDY_ACCESS_TOKEN=\n')
    git(repository, 'add', '.')
    assert verify(repository, known=set()) == 2


def test_shared_blob_cannot_hide_a_historical_login_filename(repository):
    (repository / 'example.txt').write_text('{}')
    git(repository, 'add', '.')
    git(repository, 'commit', '-m', 'example')
    path = repository / 'session.json'
    path.write_text('{}')
    git(repository, 'add', '.')
    git(repository, 'commit', '-m', 'login fixture')
    path.unlink()
    git(repository, 'add', '-u')
    git(repository, 'commit', '-m', 'remove login fixture')
    with pytest.raises(RuntimeError, match='session.json'):
        verify(repository, known=set())
