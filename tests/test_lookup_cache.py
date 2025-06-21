import multiprocessing
import os
import time

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.lookup_cache import (
    cached_download_file,
    clear_old_files_from_cache,
    uncached_download_file,
)


@pytest.fixture
def mock_cache_dir(tmp_path):
    cache_dir = tmp_path / "cache" / "test_cache"
    cache_dir.mkdir(parents=True)
    return cache_dir


@pytest.mark.parametrize("use_caching_function", [True, False])
def test_download_file_uncached(tmp_path, use_caching_function):
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.content = b"test content"
        mock_get.return_value = mock_response

        if use_caching_function:
            result = cached_download_file(
                "https://example.com/test",
                cache_subdir_name="test_cache",
                cache_root=tmp_path,
            )
        else:
            result = uncached_download_file("https://example.com/test")

        assert result == b"test content"
        mock_get.assert_called_once_with(
            "https://example.com/test", headers={"User-Agent": "conda-lock"}
        )
        mock_response.raise_for_status.assert_called_once()


def test_clear_old_files_from_cache(mock_cache_dir):
    """Verify that files older than the max age are removed."""
    old_file = mock_cache_dir / "old_file.txt"
    recent_file = mock_cache_dir / "recent_file.txt"
    future_file = mock_cache_dir / "future_file.txt"

    old_file.touch()
    recent_file.touch()
    future_file.touch()

    # Set the modification and access times of each file
    t = time.time()
    os.utime(old_file, (t - 100, t - 100))
    os.utime(recent_file, (t - 20, t - 20))
    os.utime(future_file, (t + 100, t + 100))

    clear_old_files_from_cache(mock_cache_dir, max_age_seconds=22)

    # Only the recent file is in the correct time range
    assert not old_file.exists()
    assert recent_file.exists()
    assert not future_file.exists()

    # Immediately rerunning it again should not change anything
    clear_old_files_from_cache(mock_cache_dir, max_age_seconds=22)
    assert recent_file.exists()

    # Lowering the max age should remove the file
    clear_old_files_from_cache(mock_cache_dir, max_age_seconds=20)
    assert not recent_file.exists()


def test_clear_old_files_from_cache_invalid_directory(tmp_path):
    """Verify that only paths within a 'cache' directory are accepted.

    This is a safety measure to prevent accidental deletion of files
    outside of a cache directory.
    """
    valid_cache_dir = tmp_path / "cache" / "valid"
    invalid_cache_dir = tmp_path / "not-cache" / "invalid"

    valid_cache_dir.mkdir(parents=True)
    invalid_cache_dir.mkdir(parents=True)
    clear_old_files_from_cache(valid_cache_dir, max_age_seconds=10)
    with pytest.raises(ValueError):
        clear_old_files_from_cache(Path(invalid_cache_dir), max_age_seconds=10)


def test_cached_download_file(tmp_path):
    """Simulate an interaction with a remote server to test the cache.

    * Download the file for the first time
    * Retrieve the file again immediately (should be cached without sending a request)
    * Retrieve the file again twice more but check that the remote file has been updated
      (should get 304 Not Modified and return the cached version)
    * Retrieve the file again but check that the remote file has been updated
      (should get 200 OK and return the updated version)
    * Retrieve the file again immediately (should be cached without sending a request)
    """
    url = "https://example.com/test.json"
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.content = b"previous content"
        mock_response.status_code = 200
        mock_response.headers = {"ETag": "previous-etag"}
        mock_get.return_value = mock_response

        # Warm the cache
        result = cached_download_file(
            url, cache_subdir_name="test_cache", cache_root=tmp_path
        )
        assert result == b"previous content"
        assert mock_get.call_count == 1
        # No ETag should have been sent because we downloaded for the first time
        assert mock_get.call_args[1]["headers"].get("If-None-Match") is None

        # Calling again immediately should directly return the cached result
        # without sending a new request
        result = cached_download_file(
            url, cache_subdir_name="test_cache", cache_root=tmp_path
        )
        assert result == b"previous content"
        assert mock_get.call_count == 1

    # Now we test HTTP 304 Not Modified
    # We trigger a request by setting dont_check_if_newer_than_seconds to 0
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.content = b"Should be ignored"
        mock_response.status_code = 304
        mock_response.headers = {"ETag": "Should be ignored"}
        mock_get.return_value = mock_response

        for call_count in range(1, 2 + 1):
            # This time we should send the ETag and get a 304
            result = cached_download_file(
                url,
                cache_subdir_name="test_cache",
                cache_root=tmp_path,
                dont_check_if_newer_than_seconds=0,
            )
            assert result == b"previous content"
            assert mock_get.call_count == call_count
            assert (
                mock_get.call_args[1]["headers"].get("If-None-Match") == "previous-etag"
            )

    # Now we test HTTP 200 OK with a new ETag to simulate the remote file being updated
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.content = b"new content"
        mock_response.status_code = 200
        mock_response.headers = {"ETag": "new-etag"}
        mock_get.return_value = mock_response

        result = cached_download_file(
            url,
            cache_subdir_name="test_cache",
            cache_root=tmp_path,
            dont_check_if_newer_than_seconds=0,
        )
        assert result == b"new content"
        assert mock_get.call_count == 1
        assert mock_get.call_args[1]["headers"].get("If-None-Match") == "previous-etag"

    # Verify that we picked up the new content and sent the new ETag
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.content = b"Should be ignored"
        mock_response.status_code = 304
        mock_response.headers = {"ETag": "Should be ignored"}
        mock_get.return_value = mock_response

        result = cached_download_file(
            url,
            cache_subdir_name="test_cache",
            cache_root=tmp_path,
            dont_check_if_newer_than_seconds=0,
        )
        assert result == b"new content"
        assert mock_get.call_count == 1
        assert mock_get.call_args[1]["headers"].get("If-None-Match") == "new-etag"

        # Verify that we return the updated content without sending a new request
        result = cached_download_file(
            url,
            cache_subdir_name="test_cache",
            cache_root=tmp_path,
        )
        assert result == b"new content"
        assert mock_get.call_count == 1


def test_download_mapping_file(tmp_path):
    """Verify that we can download the actual mapping file and that it is cached."""
    url = DEFAULT_MAPPING_URL
    from requests import get as requests_get

    responses: list[requests.Response] = []

    def wrapped_get(*args, **kwargs):
        """Wrap requests.get to capture the response."""
        response = requests_get(*args, **kwargs)
        responses.append(response)
        return response

    # Initial download and cache
    with patch("requests.get", wraps=wrapped_get) as mock_get:
        result = cached_download_file(
            url, cache_subdir_name="test_cache", cache_root=tmp_path
        )
        # Ensure the response is valid and content is as expected
        assert len(responses) == 1
        response = responses[0]
        assert response.status_code == 200
        assert len(response.content) > 10000
        assert response.content == result

    # Verify that the file is retrieved from cache
    with patch("requests.get", wraps=wrapped_get) as mock_get:
        result2 = cached_download_file(
            url, cache_subdir_name="test_cache", cache_root=tmp_path
        )
        mock_get.assert_not_called()
        assert result == result2

    # Force cache refresh and verify ETag handling
    with patch("requests.get", wraps=wrapped_get) as mock_get:
        result3 = cached_download_file(
            url,
            cache_subdir_name="test_cache",
            cache_root=tmp_path,
            dont_check_if_newer_than_seconds=0,
        )
        # Ensure the request is made and the response is 304 Not Modified
        assert len(responses) == 2
        response = responses[1]
        assert response is not None
        mock_get.assert_called_once()
        assert response.status_code == 304
        assert len(response.content) == 0
        assert result == result2 == result3


def test_concurrent_cached_download_file(tmp_path):
    """Test concurrent access to cached_download_file with 5 processes."""
    url = "https://example.com/test.json"

    # Use multiprocessing Manager to share state between processes
    with multiprocessing.Manager() as manager:
        results = manager.Queue()
        process_names_emitting_lock_warnings = manager.list()
        process_names_calling_requests_get = manager.list()
        request_count = manager.Value("i", 0)

        def download_file(
            result_queue,
            process_names_emitting_lock_warnings,
            process_names_calling_requests_get,
            request_count,
        ):
            """Download the file in a process and store the result in a queue."""
            import random

            def mock_get(*args, **kwargs):
                time.sleep(6)
                response = MagicMock()
                response.content = b"content"
                response.status_code = 200
                process_name = multiprocessing.current_process().name
                process_names_calling_requests_get.append(process_name)
                request_count.value += 1
                return response

            def mock_warning(msg, *args, **kwargs):
                if "Failed to acquire lock" in msg:
                    process_names_emitting_lock_warnings.append(
                        multiprocessing.current_process().name
                    )

            # Randomize which process calls cached_download_file first
            time.sleep(random.uniform(0, 0.1))

            with patch(
                "conda_lock.lookup_cache.requests.get", side_effect=mock_get
            ), patch(
                "conda_lock.lookup_cache.logger.warning", side_effect=mock_warning
            ):
                result = cached_download_file(
                    url, cache_subdir_name="test_cache", cache_root=tmp_path
                )
                result_queue.put(result)

        # Create and start 5 processes
        process_names = [f"CachedDownloadFileProcess-{i}" for i in range(5)]
        processes = [
            multiprocessing.Process(
                target=download_file,
                args=(
                    results,
                    process_names_emitting_lock_warnings,
                    process_names_calling_requests_get,
                    request_count,
                ),
                name=process_name,
            )
            for process_name in process_names
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join()

        # Collect results from the queue
        assert results.qsize() == len(processes)
        results_list = []
        while not results.empty():
            results_list.append(results.get())
        assert all(result == b"content" for result in results_list)

        # We expect one process to have made the request and the other four
        # to have emitted warnings.
        process_names_calling_requests_get_list = list(
            process_names_calling_requests_get
        )
        process_names_emitting_lock_warnings_list = list(
            process_names_emitting_lock_warnings
        )

        assert (
            len(process_names_calling_requests_get_list)
            == 1
            == len(set(process_names_calling_requests_get_list))
            == request_count.value
        ), f"{process_names_calling_requests_get_list=}"
        assert (
            len(process_names_emitting_lock_warnings_list)
            == 4
            == len(set(process_names_emitting_lock_warnings_list))
        ), f"{process_names_emitting_lock_warnings_list=}"
        assert set(process_names) == set(
            process_names_calling_requests_get_list
            + process_names_emitting_lock_warnings_list
        )
