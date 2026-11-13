"""Tests for lab_engine.runtime: thread limits and where numba caches code."""

from __future__ import annotations

import os
from pathlib import Path

import lab_engine
import pytest
from lab_engine import runtime


def _write_package(root: Path, files: dict[str, str]) -> Path:
    """Write ``files`` (relative path to text) under ``root`` and return it."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class TestLimitThreads:
    """``limit_threads`` sets every thread-pool variable to one thread."""

    def test_sets_every_variable_to_one(self) -> None:
        environ: dict[str, str] = {}
        runtime.limit_threads(environ)
        assert environ == dict.fromkeys(runtime.THREAD_LIMIT_VARIABLES, "1")

    def test_overrides_values_already_set(self) -> None:
        environ = dict.fromkeys(runtime.THREAD_LIMIT_VARIABLES, "8")
        runtime.limit_threads(environ)
        assert environ == dict.fromkeys(runtime.THREAD_LIMIT_VARIABLES, "1")

    def test_leaves_other_variables_alone(self) -> None:
        environ = {"PATH": "/usr/bin", "OMP_NUM_THREADS": "4"}
        runtime.limit_threads(environ)
        assert environ["PATH"] == "/usr/bin"

    def test_covers_the_pools_numpy_numba_and_torch_start(self) -> None:
        assert set(runtime.THREAD_LIMIT_VARIABLES) == {
            "NUMBA_NUM_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        }


class TestSourceDigest:
    """``source_digest`` changes exactly when the package's code or data does."""

    def test_same_source_gives_the_same_digest(self, tmp_path: Path) -> None:
        files = {"a.py": "A = 1\n", "b.py": "B = 2\n"}
        first = _write_package(tmp_path / "one", files)
        second = _write_package(tmp_path / "two", files)
        assert runtime.source_digest(first) == runtime.source_digest(second)

    def test_changes_when_a_file_changes(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 2\n"})
        before = runtime.source_digest(package)
        (package / "b.py").write_text("B = 3\n")
        assert runtime.source_digest(package) != before

    def test_changes_when_a_file_is_added(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        (package / "b.py").write_text("")
        assert runtime.source_digest(package) != before

    def test_changes_when_a_file_is_renamed(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        (package / "a.py").rename(package / "c.py")
        assert runtime.source_digest(package) != before

    def test_changes_when_a_nested_module_changes(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "", "sub/b.py": "B = 1\n"})
        before = runtime.source_digest(package)
        (package / "sub" / "b.py").write_text("B = 2\n")
        assert runtime.source_digest(package) != before

    def test_changes_when_a_data_file_changes(self, tmp_path: Path) -> None:
        # Compiled code freezes globals, so data read into one counts too.
        package = _write_package(tmp_path, {"a.py": "", "data/weights.txt": "1 2 3"})
        before = runtime.source_digest(package)
        (package / "data" / "weights.txt").write_text("1 2 4")
        assert runtime.source_digest(package) != before

    def test_changes_when_nested_data_changes(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "", "data/models/w.bin": "1"})
        before = runtime.source_digest(package)
        (package / "data" / "models" / "w.bin").write_text("2")
        assert runtime.source_digest(package) != before

    def test_only_the_top_data_folder_counts(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": ""})
        before = runtime.source_digest(package)
        _write_package(package, {"sub/data/x.txt": "x"})
        assert runtime.source_digest(package) == before

    @pytest.mark.parametrize("link", ["data", "data/models", "sub"])
    def test_refuses_a_linked_folder(self, tmp_path: Path, link: str) -> None:
        # The walk sees a link to a folder as a file, so what's behind it would
        # go unhashed (or fail to read). Refuse it rather than guess.
        package = _write_package(tmp_path / "package", {"a.py": "", "data/w.txt": "1"})
        target = _write_package(tmp_path / "target", {"x.py": "", "w.bin": "1"})
        linked = package / link
        if linked.exists():
            for path in sorted(linked.rglob("*"), reverse=True):
                path.unlink()
            linked.rmdir()
        linked.parent.mkdir(parents=True, exist_ok=True)
        linked.symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError, match="link to a folder"):
            runtime.source_digest(package)

    def test_ignores_a_hidden_link_to_a_folder(self, tmp_path: Path) -> None:
        # Hidden, like any hidden file or folder, so neither hashed nor refused.
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        target = _write_package(tmp_path / "target", {"x.py": ""})
        (package / ".cache").symlink_to(target, target_is_directory=True)
        assert runtime.source_digest(package) == before

    def test_ignores_the_pycache(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        _write_package(
            package,
            {
                "__pycache__/a.cpython-313.pyc": "x",
                "__pycache__/numba/0123456789abcdef/lab_engine_x/a.f-1.py313.nbi": "x",
                "sub/__pycache__/b.cpython-313.pyc": "x",
                "__pycache__/stray.py": "x",
            },
        )
        assert runtime.source_digest(package) == before

    def test_ignores_other_files_outside_the_data_folder(self, tmp_path: Path) -> None:
        # Editors' backups and autosaves, notes: none of them is compiled.
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        _write_package(
            package,
            {"notes.md": "x", "a.py~": "x", "#a.py#": "x", "a.py.orig": "x", "sub/b.txt": "x"},
        )
        assert runtime.source_digest(package) == before

    def test_ignores_hidden_files(self, tmp_path: Path) -> None:
        # Swap files, Finder's .DS_Store, hidden files among the data.
        package = _write_package(tmp_path, {"a.py": "A = 1\n", "data/w.txt": "1"})
        before = runtime.source_digest(package)
        _write_package(
            package,
            {
                ".a.py.swp": "x",
                ".DS_Store": "x",
                ".scratch.py": "x",
                "data/.w.txt.swp": "x",
                ".hidden/c.py": "x",
            },
        )
        assert runtime.source_digest(package) == before

    def test_ignores_a_link_to_nothing(self, tmp_path: Path) -> None:
        # Emacs leaves .#name.py links to nowhere beside files it's editing.
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        (package / ".#a.py").symlink_to(tmp_path / "missing")
        (package / "b.py").symlink_to(tmp_path / "missing")
        assert runtime.source_digest(package) == before

    def test_a_file_gone_before_it_is_read_counts_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alone = _write_package(tmp_path / "alone", {"a.py": "A = 1\n"})
        package = _write_package(tmp_path / "pair", {"a.py": "A = 1\n", "b.py": ""})
        read_bytes = Path.read_bytes

        def vanish(path: Path) -> bytes:
            if path.name == "b.py":
                raise FileNotFoundError(path)
            return read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", vanish)
        assert runtime.source_digest(package) == runtime.source_digest(alone)

    def test_ignores_modification_times(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path, {"a.py": "A = 1\n"})
        before = runtime.source_digest(package)
        os.utime(package / "a.py", (0, 0))
        assert runtime.source_digest(package) == before

    def test_does_not_depend_on_where_the_package_is(self, tmp_path: Path) -> None:
        files = {"a.py": "A = 1\n"}
        near = _write_package(tmp_path / "x", files)
        far = _write_package(tmp_path / "y" / "z", files)
        assert runtime.source_digest(near) == runtime.source_digest(far)


class TestNumbaCacheDir:
    """The cache directory is named after the source digest."""

    def test_is_the_digest_under_the_cache_root(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        root = tmp_path / "cache"
        assert runtime.numba_cache_dir(package, root) == root / runtime.source_digest(package)

    def test_defaults_to_this_package_and_its_pycache(self) -> None:
        package = Path(lab_engine.__file__).resolve().parent
        assert package == runtime.PACKAGE_DIR
        assert package / "__pycache__" / "numba" == runtime.NUMBA_CACHE_ROOT
        assert runtime.numba_cache_dir() == (
            package / "__pycache__" / "numba" / runtime.source_digest(package)
        )


class TestCheckSourceUnchanged:
    """The engine refuses to run code compiled while its source was changing."""

    def _cache(self, root: Path, digest: str) -> Path:
        stamp = root / digest / "lab_engine_x" / "search.alpha_beta-1.py313.nbi"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("compiled")
        return stamp

    def test_passes_while_the_source_is_unchanged(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        digest = runtime.source_digest(package)
        stamp = self._cache(tmp_path / "cache", digest)
        runtime.check_source_unchanged(digest, package, tmp_path / "cache")
        assert stamp.exists()

    def test_a_change_deletes_that_cache_and_stops(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        digest = runtime.source_digest(package)
        root = tmp_path / "cache"
        mixed = self._cache(root, digest)
        other = self._cache(root, "fedcba9876543210")
        (package / "a.py").write_text("A = 2\n")
        with pytest.raises(RuntimeError, match="changed"):
            runtime.check_source_unchanged(digest, package, root)
        assert not (root / digest).exists()
        assert not mixed.exists()
        assert other.exists()

    @pytest.mark.parametrize("expected", ["", "..", "abc", "0123456789ABCDEF", "0123456789abcdef0"])
    def test_refuses_anything_but_a_digest(self, tmp_path: Path, expected: str) -> None:
        # It deletes cache_root / expected, so expected must name one cache.
        # Every case here resolves inside tmp_path, should the guard ever fail.
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        stamp = self._cache(tmp_path / "cache", runtime.source_digest(package))
        with pytest.raises(ValueError, match="digest"):
            runtime.check_source_unchanged(expected, package, tmp_path / "cache")
        assert stamp.exists()

    def test_refuses_an_absolute_path(self, tmp_path: Path) -> None:
        # cache_root / "/abs" is "/abs", so an absolute path would escape the
        # cache. This one is inside tmp_path, so a failing guard deletes only it.
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        keep = tmp_path / "elsewhere" / "keep.txt"
        keep.parent.mkdir()
        keep.write_text("keep")
        with pytest.raises(ValueError, match="digest"):
            runtime.check_source_unchanged(str(keep.parent), package, tmp_path / "cache")
        assert keep.exists()

    def test_a_link_to_a_folder_appearing_counts_as_a_change(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        digest = runtime.source_digest(package)
        root = tmp_path / "cache"
        mixed = self._cache(root, digest)
        target = _write_package(tmp_path / "target", {"x.py": ""})
        (package / "sub").symlink_to(target, target_is_directory=True)
        with pytest.raises(RuntimeError, match="changed"):
            runtime.check_source_unchanged(digest, package, root)
        assert not mixed.exists()

    def test_a_change_with_no_cache_yet_still_stops(self, tmp_path: Path) -> None:
        package = _write_package(tmp_path / "package", {"a.py": "A = 1\n"})
        digest = runtime.source_digest(package)
        (package / "b.py").write_text("")
        with pytest.raises(RuntimeError, match="changed"):
            runtime.check_source_unchanged(digest, package, tmp_path / "cache")


class TestImportingThePackage:
    """``import lab_engine`` applies both settings to this process."""

    def test_limits_every_thread_pool(self) -> None:
        for name in runtime.THREAD_LIMIT_VARIABLES:
            assert os.environ[name] == "1", name

    def test_points_numba_at_the_cache_dir(self) -> None:
        assert os.environ["NUMBA_CACHE_DIR"] == str(runtime.numba_cache_dir())

    def test_records_the_digest_it_used(self) -> None:
        assert runtime.source_digest(runtime.PACKAGE_DIR) == lab_engine.SOURCE_DIGEST
        assert os.environ["NUMBA_CACHE_DIR"] == str(
            runtime.NUMBA_CACHE_ROOT / lab_engine.SOURCE_DIGEST
        )
