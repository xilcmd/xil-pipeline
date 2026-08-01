# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for docs/build_docs.py — MkDocs documentation generator."""

import importlib.util
import os
from pathlib import Path

# Load the module
spec = importlib.util.spec_from_file_location(
    "build_docs",
    os.path.join(os.path.dirname(__file__), "..", "docs", "build_docs.py")
)
build_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_docs)


# ─── Tests: convert_path_to_namespace ───

class TestConvertPathToNamespace:
    def test_flat_module(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "XILP001_script_parser.py"
        assert build_docs.convert_path_to_namespace(f, root) == "XILP001_script_parser"

    def test_nested_module(self, tmp_path):
        root = tmp_path / "myproject"
        (root / "subpkg").mkdir(parents=True)
        f = root / "subpkg" / "loader.py"
        assert build_docs.convert_path_to_namespace(f, root) == "subpkg.loader"

    def test_no_py_extension_preserved(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        d = root / "subpkg"
        namespace = build_docs.convert_path_to_namespace(d, root)
        assert namespace == "subpkg"


# ─── Tests: should_document_file ───

class TestShouldDocumentFile:
    def test_accepts_normal_module(self, tmp_path):
        root = tmp_path / "xil-pipeline"
        root.mkdir()
        f = root / "XILP001_script_parser.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is True

    def test_rejects_dunder_file(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "__init__.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_rejects_test_file(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "test_parser.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_rejects_private_module(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "_internal.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_rejects_file_in_venv(self, tmp_path):
        root = tmp_path / "myproject"
        (root / "venv" / "lib").mkdir(parents=True)
        f = root / "venv" / "lib" / "some.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_rejects_file_in_archive_dir(self, tmp_path):
        root = tmp_path / "myproject"
        (root / "archive_old").mkdir(parents=True)
        f = root / "archive_old" / "legacy.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_rejects_file_in_subdir_with_hyphen(self, tmp_path):
        # Subdirectory with hyphen is not a valid Python package
        root = tmp_path / "myproject"
        (root / "my-subdir").mkdir(parents=True)
        f = root / "my-subdir" / "module.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is False

    def test_accepts_file_in_root_with_hyphen_in_root_name(self, tmp_path):
        # Bug fix: root dir name having a hyphen should NOT filter out files in root
        root = tmp_path / "xil-pipeline"
        root.mkdir()
        f = root / "XILP001_script_parser.py"
        f.touch()
        assert build_docs.should_document_file(f, root) is True


# ─── Tests: should_copy_markdown_file ───

class TestShouldCopyMarkdownFile:
    def test_accepts_readme(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "README.md"
        assert build_docs.should_copy_markdown_file(f) is True

    def test_rejects_file_in_venv(self, tmp_path):
        root = tmp_path / "myproject"
        (root / "venv").mkdir(parents=True)
        f = root / "venv" / "README.md"
        assert build_docs.should_copy_markdown_file(f) is False

    def test_rejects_file_with_spaces(self, tmp_path):
        root = tmp_path / "myproject"
        root.mkdir()
        f = root / "My Script Notes.md"
        assert build_docs.should_copy_markdown_file(f) is False

    def test_rejects_file_in_site(self, tmp_path):
        root = tmp_path / "myproject"
        (root / "site").mkdir(parents=True)
        f = root / "site" / "index.md"
        assert build_docs.should_copy_markdown_file(f) is False


# ─── Tests: link_markdown_files ───

class TestLinkMarkdownFiles:
    def test_creates_symlink(self, tmp_path):
        """link_markdown_files should create a symlink, not a copy."""
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()

        src = project_root / "README.md"
        src.write_text("# Hello\n")

        build_docs.link_markdown_files(project_root, docs_base, project_root)

        dest = docs_base / "README.md"
        assert dest.exists()
        if dest.is_symlink():
            assert dest.resolve() == src.resolve()
        else:
            assert dest.read_bytes() == src.read_bytes()

    def test_replaces_stale_copy_with_symlink(self, tmp_path):
        """link_markdown_files should replace an existing regular file with a symlink."""
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()

        src = project_root / "README.md"
        src.write_text("# Hello\n")

        # Pre-place a regular file (as if a previous copy run left it)
        dest = docs_base / "README.md"
        dest.write_text("stale content")

        build_docs.link_markdown_files(project_root, docs_base, project_root)

        assert dest.exists()
        if dest.is_symlink():
            assert dest.resolve() == src.resolve()
        else:
            assert dest.read_bytes() == src.read_bytes()

    def test_returns_count(self, tmp_path):
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()

        (project_root / "README.md").write_text("# A\n")
        (project_root / "CHANGELOG.md").write_text("# B\n")

        count = build_docs.link_markdown_files(project_root, docs_base, project_root)
        assert count == 2


# ─── Tests: link_sample_audio ───

class TestLinkSampleAudio:
    def test_creates_symlink_for_mp3(self, tmp_path):
        """link_sample_audio creates a relative symlink in docs/samples/."""
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        docs_base = project_root / "docs"
        docs_base.mkdir()

        src = project_root / "samples" / "demo.mp3"
        src.write_bytes(b"ID3")

        build_docs.link_sample_audio(project_root, docs_base)

        dest = docs_base / "samples" / "demo.mp3"
        assert dest.exists()
        if dest.is_symlink():
            assert dest.resolve() == src.resolve()
        else:
            assert dest.read_bytes() == src.read_bytes()

    def test_returns_count(self, tmp_path):
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        docs_base = project_root / "docs"
        docs_base.mkdir()

        (project_root / "samples" / "a.mp3").write_bytes(b"ID3")
        (project_root / "samples" / "b.mp3").write_bytes(b"ID3")

        count = build_docs.link_sample_audio(project_root, docs_base)
        assert count == 2

    def test_returns_zero_when_no_samples_dir(self, tmp_path):
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()

        count = build_docs.link_sample_audio(project_root, docs_base)
        assert count == 0

    def test_returns_zero_when_no_mp3s(self, tmp_path):
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        (project_root / "samples" / "README.md").write_text("# Samples\n")
        docs_base = project_root / "docs"
        docs_base.mkdir()

        count = build_docs.link_sample_audio(project_root, docs_base)
        assert count == 0

    def test_replaces_stale_symlink(self, tmp_path):
        """Stale symlinks in docs/samples/ are replaced, not duplicated."""
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        docs_base = project_root / "docs"
        (docs_base / "samples").mkdir(parents=True)

        src = project_root / "samples" / "demo.mp3"
        src.write_bytes(b"ID3")

        stale = docs_base / "samples" / "demo.mp3"
        stale.write_bytes(b"old")

        build_docs.link_sample_audio(project_root, docs_base)

        assert stale.exists()
        if stale.is_symlink():
            assert stale.resolve() == src.resolve()
        else:
            assert stale.read_bytes() == src.read_bytes()

    def test_ignores_non_mp3_files_in_samples(self, tmp_path):
        """Only *.mp3 files are linked — markdown and other files are ignored."""
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        docs_base = project_root / "docs"
        docs_base.mkdir()

        (project_root / "samples" / "show.mp3").write_bytes(b"ID3")
        (project_root / "samples" / "notes.md").write_text("# Notes\n")
        (project_root / "samples" / "cover.wav").write_bytes(b"RIFF")

        count = build_docs.link_sample_audio(project_root, docs_base)
        assert count == 1
        assert (docs_base / "samples" / "show.mp3").exists()
        assert not (docs_base / "samples" / "notes.md").exists()
        assert not (docs_base / "samples" / "cover.wav").exists()


# ─── Integration: code_root path resolves to project root ───

class TestCodeRootPath:
    def test_docs_script_file_exists(self):
        script = Path(__file__).parent.parent / "docs" / "build_docs.py"
        assert script.exists()

    def test_expected_code_root_exists(self):
        # The script's project_root (parent.parent of the script) should be the project dir
        script = Path(__file__).parent.parent / "docs" / "build_docs.py"
        expected_code_root = script.parent.parent  # xil-pipeline/
        assert expected_code_root.exists()
        # And it should contain our known source files (now in src/xil_pipeline/)
        assert (expected_code_root / "src" / "xil_pipeline" / "XILP001_script_parser.py").exists()
        assert (expected_code_root / "src" / "xil_pipeline" / "XILP002_producer.py").exists()

    def test_rejects_file_in_docs_subdir(self, tmp_path):
        # docs/ is the output dir — scanning it causes docs/docs/ duplication
        root = tmp_path / "myproject"
        (root / "docs").mkdir(parents=True)
        f = root / "docs" / "index.md"
        assert build_docs.should_copy_markdown_file(f) is False

    def test_rejects_file_in_pytest_cache(self, tmp_path):
        root = tmp_path / "myproject"
        (root / ".pytest_cache").mkdir(parents=True)
        f = root / ".pytest_cache" / "README.md"
        assert build_docs.should_copy_markdown_file(f) is False


# ─── Tests: generated docs go into docs/xil-pipeline/, not docs/ ───

class TestGeneratedDocsSubdir:
    def test_module_docs_land_in_named_subdir(self, tmp_path):
        """Generated .md files should go to docs/<code_root_name>/, not docs/ root."""
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()

        # Create a minimal source file
        src = project_root / "XILP001_script_parser.py"
        src.write_text('"""Parser module."""\n')

        # The expected output path under the named subdir
        expected = docs_base / "xil-pipeline" / "XILP001_script_parser.md"

        # Simulate what main() should do: place docs in docs/<code_root.name>/
        docs_dir = docs_base / project_root.name  # docs/xil-pipeline/
        docs_dir.mkdir(parents=True, exist_ok=True)

        namespace = build_docs.convert_path_to_namespace(src, project_root)
        build_docs.create_module_doc(src, docs_dir, namespace)

        assert expected.exists(), f"Expected {expected} to exist"

    def test_clean_removes_named_subdir(self, tmp_path):
        """clean_generated_docs must remove docs/<code_root_name>/."""
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()
        generated = docs_base / "xil-pipeline"
        generated.mkdir()
        (generated / "XILP001_script_parser.md").write_text("# test\n")

        build_docs.clean_generated_docs(docs_base, project_root)

        assert not generated.exists()

    def test_generated_dirname_is_not_derived_from_checkout_dir(self, tmp_path):
        """The output directory must NOT follow the checkout directory's name.

        Read the Docs checks out into "latest"/"stable" while GitHub Actions uses
        ".../xil-pipeline/". Deriving the name from the checkout meant RTD grew a
        SECOND doc tree beside the committed docs/xil-pipeline/, duplicating every
        mkdocstrings anchor — ~86 "Multiple primary URLs" warnings that aborted
        --strict on RTD while CI stayed green.
        """
        # A checkout named the way Read the Docs names it.
        project_root = tmp_path / "latest"
        project_root.mkdir()
        docs_base = project_root / "docs"
        docs_base.mkdir()
        # Literal, not the constant: this asserts real behaviour, so it fails
        # on the old code-root-derived implementation rather than erroring out.
        committed = docs_base / "xil-pipeline"
        committed.mkdir()
        (committed / "stale.md").write_text("# stale\n")

        build_docs.clean_generated_docs(docs_base, project_root)

        # The committed tree is what gets cleaned — not docs/latest/.
        assert not committed.exists()
        assert not (docs_base / "latest").exists(), (
            "clean_generated_docs created or left a checkout-named tree; "
            "a second tree beside docs/xil-pipeline/ duplicates every anchor"
        )

    def test_generated_dirname_constant_matches_committed_tree(self):
        """The constant must match the tree that is actually committed."""
        repo_root = Path(build_docs.__file__).resolve().parent.parent
        assert (repo_root / "docs" / build_docs.GENERATED_DOCS_DIRNAME).is_dir()


# ─── Tests: DOC_CATEGORIES navigation grouping ───

class TestDocCategories:
    """Root-level docs are filed into docs/<category>/ so the nav is grouped.

    Without this the nav is ASCII filename order (CLAUDE.md and DIRECTION_TYPES
    ahead of "About"), because mkdocs.yml has no `nav:` and awesome-pages falls
    back to sorting by filename.
    """

    def _project(self, tmp_path):
        project_root = tmp_path / "xil-pipeline"
        (project_root / "docs").mkdir(parents=True)
        return project_root, project_root / "docs"

    def test_mapped_root_doc_lands_in_category(self, tmp_path):
        project_root, docs_base = self._project(tmp_path)
        name = next(iter(build_docs.DOC_CATEGORIES))
        category = build_docs.DOC_CATEGORIES[name]
        (project_root / name).write_text("# doc\n")

        build_docs.link_markdown_files(project_root, docs_base, project_root)

        assert (docs_base / category / name).is_symlink()
        assert not (docs_base / name).exists(), "should not also land in docs/ root"

    def test_unmapped_root_doc_still_lands_flat(self, tmp_path):
        """Files outside the map keep mirroring the repo structure."""
        project_root, docs_base = self._project(tmp_path)
        (project_root / "about-something.md").write_text("# doc\n")

        build_docs.link_markdown_files(project_root, docs_base, project_root)

        assert (docs_base / "about-something.md").is_symlink()

    def test_categories_are_relative_names(self):
        """A category must be a plain folder name, not a path or absolute."""
        for name, category in build_docs.DOC_CATEGORIES.items():
            assert category and not category.startswith(("/", ".")), name
            assert "/" not in category and "\\" not in category, name


class TestCleanPreservesHandWrittenDocs:
    """clean_generated_docs must never delete committed documentation."""

    def test_removes_symlinks_but_keeps_real_files_in_categories(self, tmp_path):
        """Category folders mix generated symlinks with committed pages.

        rmtree here would silently delete hand-written documentation — the whole
        reason the sweep is symlink-only.
        """
        project_root = tmp_path / "xil-pipeline"
        docs_base = project_root / "docs"
        category = next(iter(build_docs.DOC_CATEGORIES.values()))
        category_dir = docs_base / category
        category_dir.mkdir(parents=True)

        real = category_dir / "hand-written.md"
        real.write_text("# keep me\n")
        pages = category_dir / ".pages"
        pages.write_text("title: Configuration\n")
        source = project_root / "generated-source.md"
        source.write_text("# generated\n")
        link = category_dir / "generated-source.md"
        link.symlink_to(source)

        build_docs.clean_generated_docs(docs_base, project_root)

        assert real.exists(), "committed page was deleted"
        assert pages.exists(), "committed .pages was deleted"
        assert not link.is_symlink(), "stale symlink was not cleaned"

    def test_removes_broken_symlink(self, tmp_path):
        """A source that was deleted leaves a dangling link; clear it."""
        project_root = tmp_path / "xil-pipeline"
        docs_base = project_root / "docs"
        category = next(iter(build_docs.DOC_CATEGORIES.values()))
        category_dir = docs_base / category
        category_dir.mkdir(parents=True)
        broken = category_dir / "gone.md"
        broken.symlink_to(project_root / "never-existed.md")

        build_docs.clean_generated_docs(docs_base, project_root)

        assert not broken.is_symlink()

    def test_sweeps_stale_root_symlink_for_categorized_file(self, tmp_path):
        """Newly categorizing a file must not leave its old docs/ root link.

        MkDocs would resolve the page from docs/ root instead of the category
        folder, and every relative link inside it would point at the wrong
        neighbours — a --strict failure.
        """
        project_root = tmp_path / "xil-pipeline"
        docs_base = project_root / "docs"
        docs_base.mkdir(parents=True)
        name = next(iter(build_docs.DOC_CATEGORIES))
        source = project_root / name
        source.write_text("# doc\n")
        stale = docs_base / name
        stale.symlink_to(source)

        build_docs.clean_generated_docs(docs_base, project_root)

        assert not stale.is_symlink()

    def test_keeps_docs_pages_navigation_file(self, tmp_path):
        """docs/.pages is committed navigation, not build output.

        Deleting it was why no curated top-level nav could exist: RTD runs
        build_docs.py as pre_build, so the file vanished before MkDocs ran.
        """
        project_root = tmp_path / "xil-pipeline"
        docs_base = project_root / "docs"
        docs_base.mkdir(parents=True)
        pages = docs_base / ".pages"
        pages.write_text("nav:\n  - README.md\n  - ...\n")

        build_docs.clean_generated_docs(docs_base, project_root)

        assert pages.exists(), "committed top-level nav was deleted"


# ─── Tests: cross-drive symlink fallback ───

class TestCrossDriveSymlinkFallback:
    """On Windows, os.path.relpath raises ValueError when src and dest are on different
    drives.  Both link_sample_audio and link_markdown_files must catch that and fall
    back to an absolute-path symlink so the link is still created."""

    def test_link_sample_audio_falls_back_to_absolute_on_value_error(self, tmp_path, monkeypatch):
        """link_sample_audio falls back to absolute path when relpath raises ValueError."""
        project_root = tmp_path / "xil-pipeline"
        (project_root / "samples").mkdir(parents=True)
        docs_base = project_root / "docs"
        docs_base.mkdir()

        src = project_root / "samples" / "demo.mp3"
        src.write_bytes(b"ID3")

        def raising_relpath(path, start="."):
            raise ValueError("path is on mount 'C:', start on mount 'D:'")

        monkeypatch.setattr(build_docs.os.path, "relpath", raising_relpath)

        build_docs.link_sample_audio(project_root, docs_base)

        dest = docs_base / "samples" / "demo.mp3"
        assert dest.exists()
        if dest.is_symlink():
            assert dest.resolve() == src.resolve()
        else:
            assert dest.read_bytes() == src.read_bytes()

    def test_link_markdown_files_falls_back_to_absolute_on_value_error(self, tmp_path, monkeypatch):
        """link_markdown_files falls back to absolute path when relpath raises ValueError."""
        project_root = tmp_path / "xil-pipeline"
        project_root.mkdir(exist_ok=True)
        docs_base = project_root / "docs"
        docs_base.mkdir()

        src = project_root / "README.md"
        src.write_text("# Hello\n")

        def raising_relpath(path, start="."):
            raise ValueError("path is on mount 'C:', start on mount 'D:'")

        monkeypatch.setattr(build_docs.os.path, "relpath", raising_relpath)

        count = build_docs.link_markdown_files(project_root, docs_base, project_root)

        assert count >= 1
        dest = docs_base / "README.md"
        assert dest.exists()
        if dest.is_symlink():
            assert dest.resolve() == src.resolve()
        else:
            assert dest.read_bytes() == src.read_bytes()
