import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallerSecurityTests(unittest.TestCase):
    def test_secret_environment_file_is_created_private_and_replaced_atomically(self):
        installer = (ROOT / "deploy" / "install-blog-admin.sh").read_text(
            encoding="utf-8"
        )

        umask = installer.index("umask 077")
        temporary = installer.index('ENV_TEMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"')
        write = installer.index("} > \"${ENV_TEMP}\"")
        chmod = installer.index('chmod 600 "${ENV_TEMP}"')
        replace = installer.index('mv -f -- "${ENV_TEMP}" "${ENV_FILE}"')

        self.assertLess(umask, temporary)
        self.assertLess(temporary, write)
        self.assertLess(write, chmod)
        self.assertLess(chmod, replace)
        self.assertNotIn('} > "${ENV_FILE}"', installer)
        self.assertIn("trap cleanup_env_temp EXIT", installer)
        self.assertLess(installer.index("set +x"), installer.index("BLOG_ADMIN_PASSWORD="))
        self.assertIn("export -n BLOG_ADMIN_PASSWORD", installer)

    def test_installer_fails_closed_without_required_pillow_codecs(self):
        installer = (ROOT / "deploy" / "install-blog-admin.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("pillow_is_ready()", installer)
        self.assertIn("dnf install -y python3-pillow", installer)
        self.assertIn("apt-get install -y python3-pil", installer)
        self.assertIn('required = ("jpg", "zlib", "webp")', installer)
        self.assertIn("if ! pillow_is_ready; then", installer)


if __name__ == "__main__":
    unittest.main()
