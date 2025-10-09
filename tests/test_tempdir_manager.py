"""Tests for tempdir_manager module."""

import os
import tempfile

from pathlib import Path
from unittest import mock

import pytest

import conda_lock.tempdir_manager as tm


@pytest.fixture(autouse=True)
def isolate_tempdir_state():
    """Isolate thread-local state between tests."""
    # Save original state
    original_delete = tm.state.delete_temp_paths
    original_paths = tm.state._preserved_paths.copy()

    # Clear for test
    tm.state.delete_temp_paths = True
    tm.state._preserved_paths.clear()

    yield

    # Restore original state
    tm.state.delete_temp_paths = original_delete
    tm.state._preserved_paths.clear()
    tm.state._preserved_paths.extend(original_paths)


@pytest.fixture
def test_temp_dir():
    """Provide a safe temporary directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_atexit(monkeypatch):
    """Mock atexit.register and return the mock."""
    mock_register = mock.MagicMock()
    monkeypatch.setattr("conda_lock.tempdir_manager.atexit.register", mock_register)
    return mock_register


class TestTemporaryDirectory:
    """Test temporary_directory function."""

    def test_default_delete_true(self, test_temp_dir):
        """Test temporary_directory with default delete=True behavior."""
        with tm.temporary_directory(
            prefix="test-default-", dir=str(test_temp_dir)
        ) as path:
            assert Path(path).exists()
            assert "test-default-" in path
            assert path.startswith(str(test_temp_dir))
            saved_path = path

        # Should be cleaned up after context
        assert not Path(saved_path).exists()

    def test_explicit_delete_true(self, test_temp_dir):
        """Test temporary_directory with explicit delete=True."""
        with tm.temporary_directory(
            prefix="test-explicit-true-", dir=str(test_temp_dir), delete=True
        ) as path:
            assert Path(path).exists()
            assert "test-explicit-true-" in path
            assert path.startswith(str(test_temp_dir))
            saved_path = path

        # Should be cleaned up after context
        assert not Path(saved_path).exists()

    def test_delete_false_preserve(self, test_temp_dir):
        """Test temporary_directory with delete=False preserves directory."""
        with tm.temporary_directory(
            prefix="test-preserve-", dir=str(test_temp_dir), delete=False
        ) as path:
            assert Path(path).exists()
            assert "test-preserve-" in path
            assert path.startswith(str(test_temp_dir))
            saved_path = path

        # Should still exist after context when preserved
        assert Path(saved_path).exists()

        # Should be tracked
        assert saved_path in tm.state._preserved_paths

    def test_delete_false_overrides_state_true(self, test_temp_dir):
        """Test delete=False overrides default delete behavior (state=True)."""
        tm.state.delete_temp_paths = True  # Enable deletion via state

        with tm.temporary_directory(
            prefix="test-override-", dir=str(test_temp_dir), delete=False
        ) as path:
            assert Path(path).exists()
            assert path.startswith(str(test_temp_dir))
            saved_path = path

        # Should still exist despite state flag (explicit override)
        assert Path(saved_path).exists()

    def test_delete_true_overrides_state_false(self, test_temp_dir):
        """Test delete=True overrides default preserve behavior (state=False)."""
        tm.state.delete_temp_paths = False  # Enable preservation via state

        with tm.temporary_directory(
            prefix="test-override-true-", dir=str(test_temp_dir), delete=True
        ) as path:
            assert Path(path).exists()
            assert path.startswith(str(test_temp_dir))
            saved_path = path

        # Should be cleaned up despite state flag (explicit override)
        assert not Path(saved_path).exists()

    def test_custom_prefix_and_dir(self, test_temp_dir):
        """Test temporary_directory with custom prefix and dir."""
        custom_prefix = "custom-prefix-"
        with tm.temporary_directory(
            prefix=custom_prefix, dir=str(test_temp_dir), delete=True
        ) as path:
            assert Path(path).exists()
            assert path.startswith(str(test_temp_dir))
            assert custom_prefix in path
            saved_path = path

        # Should be cleaned up
        assert not Path(saved_path).exists()


class TestMkdtempWithCleanup:
    """Test mkdtemp_with_cleanup function."""

    def test_default_delete_true(self, test_temp_dir, mock_atexit):
        """Test mkdtemp_with_cleanup with default delete=True behavior."""
        path = tm.mkdtemp_with_cleanup(prefix="test-mkdtemp-", dir=str(test_temp_dir))
        assert Path(path).exists()
        assert "test-mkdtemp-" in path
        assert path.startswith(str(test_temp_dir))
        # Should have registered cleanup
        mock_atexit.assert_called_once()

    def test_explicit_delete_true(self, test_temp_dir, mock_atexit):
        """Test mkdtemp_with_cleanup with explicit delete=True."""
        path = tm.mkdtemp_with_cleanup(
            prefix="test-explicit-true-", dir=str(test_temp_dir), delete=True
        )
        assert Path(path).exists()
        assert path.startswith(str(test_temp_dir))
        # Should have registered cleanup
        mock_atexit.assert_called_once()

    def test_delete_false_preserve(self, test_temp_dir, mock_atexit):
        """Test mkdtemp_with_cleanup with delete=False preserves directory."""
        path = tm.mkdtemp_with_cleanup(
            prefix="test-preserve-mkdtemp-", dir=str(test_temp_dir), delete=False
        )
        assert Path(path).exists()
        assert path.startswith(str(test_temp_dir))
        assert path in tm.state._preserved_paths
        # Should register cleanup on the first preserved path
        mock_atexit.assert_called_once_with(tm._log_preserved_paths)

    def test_delete_false_overrides_state_true(self, test_temp_dir, mock_atexit):
        """Test delete=False overrides default delete behavior (state=True)."""
        tm.state.delete_temp_paths = True  # Enable deletion via state

        path = tm.mkdtemp_with_cleanup(
            prefix="test-override-false-", dir=str(test_temp_dir), delete=False
        )
        assert Path(path).exists()
        assert path.startswith(str(test_temp_dir))
        assert path in tm.state._preserved_paths
        # Should register cleanup on the first preserved path despite state
        mock_atexit.assert_called_once_with(tm._log_preserved_paths)

    def test_delete_true_overrides_state_false(self, test_temp_dir, mock_atexit):
        """Test delete=True overrides default preserve behavior (state=False)."""
        tm.state.delete_temp_paths = False  # Enable preservation via state

        path = tm.mkdtemp_with_cleanup(
            prefix="test-override-true-mkdtemp-", dir=str(test_temp_dir), delete=True
        )
        assert Path(path).exists()
        assert path.startswith(str(test_temp_dir))
        # Should have registered cleanup despite state flag (explicit override)
        mock_atexit.assert_called_once()

    def test_custom_prefix_and_dir(self, test_temp_dir, mock_atexit):
        """Test mkdtemp_with_cleanup with custom prefix and dir."""
        path = tm.mkdtemp_with_cleanup(
            prefix="custom-mkdtemp-", dir=str(test_temp_dir), delete=True
        )
        assert path.startswith(str(test_temp_dir))
        assert "custom-mkdtemp-" in path
        # Should have registered cleanup
        mock_atexit.assert_called_once()


class TestTemporaryFileWithContents:
    """Test temporary_file_with_contents function."""

    def test_default_delete_true(self, test_temp_dir):
        """Test temporary_file_with_contents with default delete=True behavior."""
        content = "test file content"

        with tm.temporary_file_with_contents(content, dir=str(test_temp_dir)) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            saved_path = path

        # Should be cleaned up after context
        assert not saved_path.exists()

    def test_explicit_delete_true(self, test_temp_dir):
        """Test temporary_file_with_contents with explicit delete=True."""
        content = "test explicit true content"

        with tm.temporary_file_with_contents(
            content, dir=str(test_temp_dir), delete=True
        ) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            saved_path = path

        # Should be cleaned up after context
        assert not saved_path.exists()

    def test_delete_false_preserve(self, test_temp_dir):
        """Test temporary_file_with_contents with delete=False preserves file."""
        content = "test preserve content"

        with tm.temporary_file_with_contents(
            content, dir=str(test_temp_dir), delete=False
        ) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            assert str(path) in tm.state._preserved_paths
            saved_path = path

        # Should still exist after context when preserved
        assert saved_path.exists()
        assert saved_path.read_text() == content

    def test_delete_false_overrides_state_true(self, test_temp_dir):
        """Test delete=False overrides default delete behavior (state=True)."""
        tm.state.delete_temp_paths = True  # Enable deletion via state
        content = "test override false content"

        with tm.temporary_file_with_contents(
            content, dir=str(test_temp_dir), delete=False
        ) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            assert str(path) in tm.state._preserved_paths
            saved_path = path

        # Should still exist despite state flag (explicit override)
        assert saved_path.exists()

    def test_delete_true_overrides_state_false(self, test_temp_dir):
        """Test delete=True overrides default preserve behavior (state=False)."""
        tm.state.delete_temp_paths = False  # Enable preservation via state
        content = "test override true content"

        with tm.temporary_file_with_contents(
            content, dir=str(test_temp_dir), delete=True
        ) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            saved_path = path

        # Should be cleaned up despite state flag (explicit override)
        assert not saved_path.exists()

    def test_custom_prefix_and_dir(self, test_temp_dir):
        """Test temporary_file_with_contents with custom prefix and dir."""
        content = "test custom content"

        with tm.temporary_file_with_contents(
            content, prefix="custom-file-", dir=str(test_temp_dir), delete=True
        ) as path:
            assert path.exists()
            assert path.read_text() == content
            assert path.parent == test_temp_dir
            assert "custom-file-" in path.name
            saved_path = path

        # Should be cleaned up
        assert not saved_path.exists()


class TestTracking:
    """Test directory tracking functionality."""

    def test_track_function(self, test_temp_dir, mock_atexit):
        """Test the _track function."""
        test_path = str(test_temp_dir / "test-track-path")
        tm._track(test_path)

        assert test_path in tm.state._preserved_paths
        assert len(tm.state._preserved_paths) == 1
        # Should register _log_preserved_paths on first track
        mock_atexit.assert_called_once()

    def test_log_preserved_paths_empty(self, capsys):
        """Test _log_preserved_paths when no paths are preserved."""
        tm._log_preserved_paths()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_log_preserved_paths_with_dirs(self, test_temp_dir, capsys):
        """Test _log_preserved_paths when directories are preserved."""
        dir1 = str(test_temp_dir / "dir1")
        dir2 = str(test_temp_dir / "dir2")
        tm.state._preserved_paths.extend([dir1, dir2])

        # Create the directories
        Path(dir1).mkdir(parents=True, exist_ok=True)
        Path(dir2).mkdir(parents=True, exist_ok=True)

        tm._log_preserved_paths()

        captured = capsys.readouterr()
        assert "Preserved temporary paths:" in captured.err
        assert f"{dir1}{os.sep}" in captured.err  # Directory with separator
        assert f"{dir2}{os.sep}" in captured.err  # Directory with separator
        assert "=" * 60 in captured.err

    def test_log_preserved_paths_with_missing_paths(self, test_temp_dir, capsys):
        """Test _log_preserved_paths when some paths are missing."""
        existing_dir = str(test_temp_dir / "existing_dir")
        missing_file = str(test_temp_dir / "missing_file")
        tm.state._preserved_paths.extend([existing_dir, missing_file])

        # Create existing dir, leave missing file missing
        Path(existing_dir).mkdir(parents=True, exist_ok=True)

        tm._log_preserved_paths()

        captured = capsys.readouterr()
        assert "Preserved temporary paths:" in captured.err
        assert f"{existing_dir}{os.sep}" in captured.err  # Directory with separator
        assert missing_file in captured.err
        assert "WARNING: missing path:" in captured.err

        # No manual cleanup needed - test_temp_dir fixture handles it


class TestModuleState:
    """Test module-level state management."""

    def test_delete_temp_paths_flag(self):
        """Test that delete_temp_paths flag can be set."""
        # Test setting to False (preserve)
        tm.state.delete_temp_paths = False
        assert tm.state.delete_temp_paths is False

        # Test setting to True (delete)
        tm.state.delete_temp_paths = True
        assert tm.state.delete_temp_paths is True


class TestIntegration:
    """Integration tests for multiple functions working together."""

    def test_mixed_delete_modes(self, test_temp_dir, mock_atexit):
        """Test mixing different delete modes in the same test."""
        # Create some preserved paths
        tm.state.delete_temp_paths = False

        preserved_dir = tm.mkdtemp_with_cleanup(
            prefix="mixed-preserved-", dir=str(test_temp_dir)
        )
        preserved_file = None
        with tm.temporary_file_with_contents(
            "mixed content", dir=str(test_temp_dir), delete=False
        ) as pf:
            preserved_file = pf

        assert preserved_dir in tm.state._preserved_paths
        assert str(preserved_file) in tm.state._preserved_paths

        # Create some deleted paths
        tm.state.delete_temp_paths = True

        deleted_dir = tm.mkdtemp_with_cleanup(
            prefix="mixed-deleted-", dir=str(test_temp_dir)
        )
        deleted_dir_path = Path(deleted_dir)
        with tm.temporary_file_with_contents(
            "deleted content", dir=str(test_temp_dir), delete=True
        ):
            pass

        # Both preserved paths should still exist
        assert Path(preserved_dir).exists()
        assert preserved_file.exists()
        # Deleted dir should exist before we simulate exit
        assert deleted_dir_path.exists()

        # Simulate exit and run cleanup for the deleted directory.
        # It should be the second registered function.
        assert mock_atexit.call_count == 2
        cleanup_func = mock_atexit.call_args_list[1].args[0]
        cleanup_func()

        # Deleted paths should be gone
        assert not deleted_dir_path.exists()

    def test_override_state_with_explicit_delete(self, test_temp_dir, mock_atexit):
        """Explicit delete parameter overrides state setting."""
        # Set state to preserve
        tm.state.delete_temp_paths = False

        # But explicitly delete
        deleted_dir = tm.mkdtemp_with_cleanup(
            prefix="explicit-delete-", dir=str(test_temp_dir), delete=True
        )
        deleted_dir_path = Path(deleted_dir)

        # File is handled separately and cleaned up by its own context manager
        with tm.temporary_file_with_contents(
            "explicit content", dir=str(test_temp_dir), delete=True
        ):
            pass

        # Assert that cleanup was registered and directory still exists
        mock_atexit.assert_called_once()
        assert deleted_dir_path.exists()

        # Simulate exit by running the registered atexit function for the directory
        registered_cleanup_func = mock_atexit.call_args.args[0]
        registered_cleanup_func()

        # Assert that the directory is now deleted
        assert not deleted_dir_path.exists()

    def test_context_manager_preserves_when_state_true(self, test_temp_dir):
        """Context manager preserves when delete=False despite state=True."""
        tm.state.delete_temp_paths = True  # State says delete

        with tm.temporary_directory(
            prefix="context-preserve-", dir=str(test_temp_dir), delete=False
        ) as path:
            assert Path(path).exists()
            saved_path = path

        # Should still exist despite state flag
        assert Path(saved_path).exists()
