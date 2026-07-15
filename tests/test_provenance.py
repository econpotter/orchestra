from pathlib import Path

from orchestra.provenance import package_tree_digest, runtime_provenance


def test_package_tree_digest_is_stable_and_content_sensitive(tmp_path: Path):
    package = tmp_path / "orchestra"
    package.mkdir()
    (package / "a.py").write_text("one\n")
    (package / "ignored.pyc").write_text("noise")
    first = package_tree_digest(package)
    (package / "ignored.pyc").write_text("different noise")
    assert package_tree_digest(package) == first
    (package / "a.py").write_text("two\n")
    assert package_tree_digest(package) != first


def test_runtime_provenance_identifies_loaded_package():
    provenance = runtime_provenance()
    assert provenance["version"]
    assert Path(provenance["package_root"]).name == "orchestra"
    assert len(provenance["package_sha256"]) == 64


def test_source_and_installed_prompt_layouts_have_same_digest(tmp_path: Path):
    source = tmp_path / "checkout" / "src" / "orchestra"
    source.mkdir(parents=True)
    (source / "module.py").write_text("code\n")
    prompts = tmp_path / "checkout" / "prompts"
    prompts.mkdir()
    (prompts / "worker.md").write_text("prompt\n")

    installed = tmp_path / "installed" / "orchestra"
    defaults = installed / "defaults" / "prompts"
    defaults.mkdir(parents=True)
    (installed / "module.py").write_text("code\n")
    (defaults / "worker.md").write_text("prompt\n")

    assert package_tree_digest(source) == package_tree_digest(installed)
