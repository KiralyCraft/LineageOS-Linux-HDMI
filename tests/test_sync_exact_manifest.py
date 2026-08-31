#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[1]
SYNC = ROOT / "build-support" / "sync-exact-manifest.py"


def run(*args: object, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class ExactManifestSyncTest(unittest.TestCase):
    def test_fetches_only_pinned_commit_and_recreates_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hdmi-los-sync-test-") as temp:
            base = pathlib.Path(temp)
            source = base / "source"
            remotes = base / "remotes"
            remote = remotes / "example"
            tree = base / "tree"
            manifest = base / "manifest.xml"

            source.mkdir()
            remotes.mkdir()
            run("git", "init", "-q", source)
            run("git", "config", "user.name", "HDMI LOS test", cwd=source)
            run("git", "config", "user.email", "hdmi-los-test@localhost", cwd=source)
            (source / "payload.txt").write_text("pinned payload\n")
            (source / "linux-x86").mkdir()
            (source / "linux-x86" / "tool").write_text("linux tool\n")
            (source / "windows-x86").mkdir()
            (source / "windows-x86" / "tool.exe").write_text("unused tool\n")
            run("git", "add", ".", cwd=source)
            run("git", "commit", "-q", "-m", "pinned", cwd=source)
            revision = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
            run("git", "clone", "-q", "--bare", source, remote)

            root = ET.Element("manifest")
            ET.SubElement(root, "remote", name="test", fetch=remotes.as_uri())
            ET.SubElement(root, "default", remote="test")
            project = ET.SubElement(
                root,
                "project",
                name="example",
                path="prebuilts/rust",
                revision=revision,
            )
            ET.SubElement(project, "linkfile", src="payload.txt", dest="root-link.txt")
            ET.SubElement(project, "copyfile", src="payload.txt", dest="root-copy.txt")
            ET.ElementTree(root).write(manifest, encoding="utf-8", xml_declaration=True)

            run(sys.executable, SYNC, manifest, tree, "2")
            checkout = tree / "prebuilts" / "rust"
            self.assertEqual(
                run("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip(), revision
            )
            refs = run("git", "show-ref", cwd=checkout).stdout.splitlines()
            self.assertTrue(refs)
            self.assertTrue(all(line.endswith(" refs/hdmi-los/exact") for line in refs))
            self.assertTrue((tree / "root-link.txt").is_symlink())
            self.assertEqual((tree / "root-link.txt").read_text(), "pinned payload\n")
            self.assertEqual((tree / "root-copy.txt").read_text(), "pinned payload\n")
            self.assertEqual((checkout / "linux-x86" / "tool").read_text(), "linux tool\n")
            self.assertFalse((checkout / "windows-x86").exists())

            # A retry must stay on the captured revision even if the remote has
            # moved forward.
            (source / "payload.txt").write_text("new branch tip\n")
            run("git", "commit", "-q", "-am", "new tip", cwd=source)
            run("git", "push", "-q", remote, "HEAD", cwd=source)
            run(sys.executable, SYNC, manifest, tree, "2")
            self.assertEqual((tree / "root-link.txt").read_text(), "pinned payload\n")
            record = json.loads((tree / ".hdmi-los-exact-manifest.json").read_text())
            self.assertEqual(record["projects"], {"prebuilts/rust": revision})


if __name__ == "__main__":
    unittest.main()
