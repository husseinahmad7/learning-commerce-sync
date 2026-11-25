from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.secrets import read_secret


class SecretSettingsTests(SimpleTestCase):
    def test_required_secret_never_falls_back_to_environment_or_demo_default(self):
        with patch.dict("os.environ", {"TEST_SECRET": "demo-value"}, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                read_secret("TEST_SECRET", required=True, default="demo-default")

    def test_file_secret_is_loaded_without_putting_its_value_in_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("fictional-test-value\n")
            with patch.dict("os.environ", {"TEST_SECRET_FILE": str(path)}):
                self.assertEqual(read_secret("TEST_SECRET", required=True), "fictional-test-value")
                path.write_text("")
                with self.assertRaises(ImproperlyConfigured) as failure:
                    read_secret("TEST_SECRET", required=True)
                self.assertNotIn("fictional-test-value", str(failure.exception))

    def test_missing_file_fails_instead_of_using_a_demo_default(self):
        with patch.dict("os.environ", {"TEST_SECRET_FILE": "/nonexistent/learning-commerce-secret"}):
            with self.assertRaises(ImproperlyConfigured):
                read_secret("TEST_SECRET", required=False, default="demo-default")
