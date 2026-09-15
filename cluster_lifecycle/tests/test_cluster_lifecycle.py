"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Offline tests for cluster discovery, REST requests and lifecycle handling.
"""

import argparse
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import cluster_lifecycle as lifecycle


class ClusterLifecycleTests(unittest.TestCase):
    """Verify cloud action boundaries without credentials or network calls."""

    def setUp(self):
        """Create an isolated target and suppress command output during each test."""
        self.target = lifecycle.Target("instance", "workspace", "cluster")
        self.output = io.StringIO()
        contexts = contextlib.ExitStack()
        contexts.enter_context(contextlib.redirect_stdout(self.output))
        self.addCleanup(contexts.close)

    def test_start_and_stop_contract(self):
        """Each action uses the matching POST route and the most recent ETag."""
        for action, state in (("start", "STOPPED"), ("stop", "ACTIVE")):
            with self.subTest(action=action):
                client = Mock()
                client.request.side_effect = [
                    ({"state": state}, {"etag": "v1"}),
                    ({}, {}),
                ]
                lifecycle.change_state(client, self.target, action)
                self.assertEqual(client.request.call_count, 2)
                client.request.assert_called_with(
                    "POST", self.target.path + f"/actions/{action}", etag="v1"
                )
                self.assertIn(
                    "completion has not yet been verified", self.output.getvalue()
                )

    def test_read_only_noop_and_in_progress_do_not_post(self):
        """Status, dry-run, satisfied state and matching transition never mutate."""
        cases = [
            ("status", "FAILED", False),
            ("start", "STOPPED", True),
            ("stop", "ACTIVE", True),
            ("start", "ACTIVE", False),
            ("stop", "STOPPED", False),
            ("start", "STARTING", False),
            ("stop", "STOPPING", False),
        ]
        for action, state, dry_run in cases:
            with self.subTest(action=action, state=state, dry_run=dry_run):
                client = Mock()
                client.request.return_value = ({"state": state}, {})
                lifecycle.change_state(client, self.target, action, dry_run=dry_run)
                client.request.assert_called_once_with("GET", self.target.path)

    def test_invalid_state_is_rejected(self):
        """Failed and opposite-transition states cannot trigger a new action."""
        for state in ("FAILED", "DELETED", "STOPPING", "UNKNOWN", None):
            client = Mock()
            client.request.return_value = ({"state": state}, {})
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.change_state(client, self.target, "start")
            self.assertEqual(client.request.call_count, 1)

    def test_wait_completes_without_resubmission(self):
        """An existing transition can be awaited without submitting another POST."""
        client = Mock()
        client.request.side_effect = [
            ({"state": "STARTING"}, {}),
            ({"state": "ACTIVE"}, {}),
        ]
        lifecycle.change_state(client, self.target, "start", wait=True)
        self.assertTrue(
            all(call.args[0] == "GET" for call in client.request.call_args_list)
        )
        self.assertIn("Completed: ACTIVE", self.output.getvalue())

    def test_wait_failure_and_timeout(self):
        """Polling reports failure and elapsed deadlines without changing resources."""
        client = Mock()
        client.request.side_effect = [
            ({"state": "STARTING"}, {}),
            ({"state": "FAILED"}, {}),
        ]
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "Unexpected cluster state"
        ):
            lifecycle.change_state(client, self.target, "start", wait=True)
        client.request.side_effect = None
        client.request.return_value = ({"state": "STARTING"}, {})
        with patch.object(lifecycle.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "not cancelled"):
                lifecycle.change_state(
                    client, self.target, "start", wait=True, wait_timeout=1
                )

    def test_rest_pagination(self):
        """All result pages are consumed with the returned next-page token."""
        client = lifecycle.WorkbenchClient("https://example.invalid", None, Mock())
        client.request = Mock(
            side_effect=[
                ({"items": [{"key": "a"}]}, {"opc-next-page": "next"}),
                ({"items": [{"key": "b"}]}, {}),
            ]
        )
        self.assertEqual(list(client.items("/items")), [{"key": "a"}, {"key": "b"}])
        self.assertEqual(client.request.call_args.kwargs["params"]["page"], "next")

    def test_repeated_token_and_bad_collection_fail(self):
        """Broken pagination or schemas cannot silently produce partial discovery."""
        client = lifecycle.WorkbenchClient("https://example.invalid", None, Mock())
        for result in (({"items": []}, {"opc-next-page": "same"}), ({}, {})):
            client.request = Mock(return_value=result)
            with self.assertRaises(lifecycle.LifecycleError):
                list(client.items("/items"))

    def test_rest_request_body_redirects_and_error_redaction(self):
        """The adapter sends empty JSON and does not expose raw HTTP errors."""
        session = Mock()
        response = session.request.return_value
        response.status_code = 202
        response.json.return_value = {}
        response.headers = {}
        client = lifecycle.WorkbenchClient("https://example.invalid", None, session)
        client.request("POST", "/action", etag="v2")
        options = session.request.call_args.kwargs
        self.assertEqual(options["json"], {})
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["headers"]["if-match"], "v2")
        response.status_code = 403
        response.text = "SECRET"
        with self.assertRaises(lifecycle.LifecycleError) as error:
            client.request("POST", "/action")
        self.assertNotIn("SECRET", str(error.exception))
        session.request.side_effect = requests.Timeout("SECRET")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "outcome is unknown"):
            client.request("POST", "/action")

    def test_encoded_resource_keys_and_invalid_endpoints(self):
        """Resource keys remain individual path segments and origins require HTTPS."""
        target = lifecycle.Target("instance", "a/b", "c d")
        self.assertIn("/workspaces/a%2Fb/clusters/c%20d", target.path)
        for endpoint in (
            "http://example.invalid",
            "https://user:password@example.invalid",
            "https://example.invalid/path",
            "https://example.invalid?token=x",
        ):
            with self.assertRaises(argparse.ArgumentTypeError):
                lifecycle.WorkbenchClient(endpoint, None, Mock())

    def test_discovery_requires_unique_match(self):
        """Duplicates across workspaces and absent targets cannot cause mutations."""
        control, client = Mock(), Mock()
        control.get_ai_data_platform.return_value.data = SimpleNamespace(
            id="instance", compartment_id="compartment", lifecycle_state="ACTIVE"
        )
        for count in (0, 1, 2):
            client.items.side_effect = [
                [{"key": "workspace"}],
                [{"key": str(i), "displayName": "demo"} for i in range(count)],
            ]
            if count == 1:
                target = lifecycle.discover(
                    control, client, "compartment", "demo", instance_id="instance"
                )
                self.assertEqual(target.cluster_key, "0")
            else:
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.discover(
                        control, client, "compartment", "demo", instance_id="instance"
                    )
        client.request.assert_not_called()

    def test_instance_compartment_mismatch(self):
        """An explicit instance does not bypass the compartment constraint."""
        control, client = Mock(), Mock()
        control.get_ai_data_platform.return_value.data = SimpleNamespace(
            compartment_id="other", lifecycle_state="ACTIVE"
        )
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.discover(
                control, client, "compartment", "demo", instance_id="instance"
            )
        client.items.assert_not_called()

    def test_compartment_ocid_and_duplicate_names(self):
        """OCIDs skip IAM listing and ambiguous names fail explicitly."""
        identity = Mock()
        value = "ocid1.compartment.oc1..example"
        self.assertEqual(
            lifecycle.resolve_compartment(identity, "tenancy", value), value
        )
        identity.list_compartments.assert_not_called()
        result = SimpleNamespace(
            data=[
                SimpleNamespace(id="a", name="demo"),
                SimpleNamespace(id="b", name="demo"),
            ]
        )
        with patch.object(
            lifecycle.oci.pagination, "list_call_get_all_results", return_value=result
        ):
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.resolve_compartment(identity, "tenancy", "demo")


if __name__ == "__main__":
    unittest.main()
