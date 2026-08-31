#!/usr/bin/env python3

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREPARE = ROOT / "build-support" / "prepare-build-manifest.py"
VERIFY = ROOT / "build-support" / "verify-proprietary-inputs.py"


class PrepareBuildManifestTest(unittest.TestCase):
    def test_adds_pinned_proprietary_project_and_verifies_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hdmi-los-manifest-test-") as temp:
            base = pathlib.Path(temp)
            manifest = base / "captured.xml"
            profile = base / "profile.json"
            output = base / "build.xml"
            tree = base / "tree"

            root = ET.Element("manifest")
            ET.SubElement(root, "remote", name="github", fetch="..")
            ET.SubElement(root, "default", remote="github")
            ET.SubElement(
                root,
                "project",
                name="LineageOS/android_hardware_qcom_display",
                path="hardware/qcom-caf/sm8550/display",
                revision="1" * 40,
            )
            ET.ElementTree(root).write(manifest, encoding="utf-8", xml_declaration=True)

            payload = b"exact proprietary input\n"
            relative = "vendor/sony/common/proprietary/vendor/lib64/libsdmextension.so"
            profile.write_text(
                json.dumps(
                    {
                        "source": {
                            "qcom_display_path": "hardware/qcom-caf/sm8550/display",
                            "qcom_display_revision": "1" * 40,
                            "proprietary_projects": [
                                {
                                    "name": "TheMuppets/proprietary_vendor_sony_common",
                                    "path": "vendor/sony/common",
                                    "revision": "2" * 40,
                                }
                            ],
                            "proprietary_files": [
                                {
                                    "path": relative,
                                    "sha256": hashlib.sha256(payload).hexdigest(),
                                }
                            ],
                        }
                    }
                )
            )

            subprocess.run(
                [sys.executable, PREPARE, manifest, profile, output], check=True
            )
            projects = {
                item.get("path", item.get("name")): item
                for item in ET.parse(output).getroot().findall("project")
            }
            self.assertEqual(len(projects), 2)
            self.assertEqual(projects["vendor/sony/common"].get("revision"), "2" * 40)

            target = tree.joinpath(*pathlib.PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            subprocess.run([sys.executable, VERIFY, tree, profile], check=True)


if __name__ == "__main__":
    unittest.main()
