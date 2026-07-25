from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = PROJECT_ROOT / "driver" / "Install-ViGEmBus.ps1"
EXPECTED_SHA256 = (
    "89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A"
)


class ViGEmBusInstallerSecurityTests(unittest.TestCase):
    def test_integrity_checks_precede_elevation(self):
        source = INSTALLER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(EXPECTED_SHA256, source)
        self.assertIn("Get-FileHash", source)
        self.assertIn("-Algorithm SHA256", source)
        self.assertIn("Get-AuthenticodeSignature", source)
        self.assertIn("SignatureStatus]::Valid", source)
        self.assertIn("Nefarius Software Solutions e.U.", source)

        hash_check = source.index("Get-FileHash")
        signature_check = source.index("Get-AuthenticodeSignature")
        elevation = source.index("-Verb RunAs")
        self.assertLess(hash_check, elevation)
        self.assertLess(signature_check, elevation)


if __name__ == "__main__":
    unittest.main()
