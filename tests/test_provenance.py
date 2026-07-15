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
